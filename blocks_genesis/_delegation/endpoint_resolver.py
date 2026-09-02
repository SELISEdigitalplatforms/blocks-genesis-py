"""Resolves IAM's token endpoint.

Callers never know the URL. Route paths are prefixable, so the effective path is
`{base}/{prefix}/oidc/token` and may never be hardcoded.

Resolution order, per section 5.5 of the delegated-access spec:

1. Discovery (primary) -- `GET {BLOCKS_IAM_BASE_URL}/{tenant_id}/.well-known/openid-configuration`,
   taking `token_endpoint`. Survives prefix and route changes.
2. `BLOCKS_IAM_TOKEN_ENDPOINT` (fallback) -- a complete URL, not a base and not a template, so no
   prefix is ever guessed.

A successful discovery is cached per tenant. A fallback is not cached, so discovery is retried
lazily on later calls. If neither is configured, startup fails.

`Tenant.jwt_token_parameters.issuer` is deliberately not used as a base URL: IAM separates
`issuer` from `apiBase`, and the issuer is an identifier, not a reachable API host.
"""

import asyncio
import logging
import os
from typing import Dict, Optional
from urllib.parse import urlparse

import aiohttp

from blocks_genesis._core.configuration import get_configurations
from blocks_genesis._delegation.constants import (
    FRONTEND_RUNTIME_SECTION,
    IAM_BASE_URL_KEY,
    IAM_TOKEN_ENDPOINT_KEY,
)

logger = logging.getLogger(__name__)

DISCOVERY_TIMEOUT_SECONDS = 5


def _resolve_setting(key: str) -> Optional[str]:
    """Environment variable, then the `FrontendRuntime` section, then the configuration root.

    `FrontendRuntime` comes before the root because that is where Blocks services put their runtime
    settings -- it is the expected home for these keys, not a last resort. A bare top-level key still
    works, so a config file that sets it either way resolves.
    """
    value = os.getenv(key)
    if value and value.strip():
        return value.strip()

    try:
        configurations = get_configurations() or {}
    except RuntimeError:
        # Configurations are optional here: a host may run on environment variables alone.
        return None

    section = configurations.get(FRONTEND_RUNTIME_SECTION) or {}
    if isinstance(section, dict):
        value = section.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    value = configurations.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()

    return None


class DelegationTokenEndpointResolver:
    def __init__(self) -> None:
        self._discovered: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._fallback_warning_logged = False

    def ensure_configured(self) -> None:
        """Raise when neither key is configured, so a misconfigured deployment fails fast."""
        if _resolve_setting(IAM_BASE_URL_KEY) or _resolve_setting(IAM_TOKEN_ENDPOINT_KEY):
            return

        raise RuntimeError(
            f"Delegated access is unconfigured: set '{IAM_BASE_URL_KEY}' (preferred, used for OIDC "
            f"discovery) or '{IAM_TOKEN_ENDPOINT_KEY}' (a complete token endpoint URL). Both must "
            "point at IAM's internal address, never the public host."
        )

    async def get_token_endpoint_async(self, tenant_id: str) -> str:
        if not tenant_id:
            raise RuntimeError("Cannot resolve the IAM token endpoint without a tenant.")

        cached = self._discovered.get(tenant_id)
        if cached:
            return cached

        async with self._lock:
            cached = self._discovered.get(tenant_id)
            if cached:
                return cached

            discovered = await self._try_discover_async(tenant_id)
            if discovered:
                self._discovered[tenant_id] = discovered
                return discovered

        configured = _resolve_setting(IAM_TOKEN_ENDPOINT_KEY)
        if configured:
            if not self._fallback_warning_logged:
                self._fallback_warning_logged = True
                logger.warning(
                    "Falling back to the configured token endpoint '%s'. Discovery will be retried lazily.",
                    IAM_TOKEN_ENDPOINT_KEY,
                )
            return configured

        raise RuntimeError(
            f"OIDC discovery failed for tenant '{tenant_id}' and '{IAM_TOKEN_ENDPOINT_KEY}' is not "
            "configured. Refusing to guess the token endpoint path."
        )

    async def _try_discover_async(self, tenant_id: str) -> Optional[str]:
        base_url = _resolve_setting(IAM_BASE_URL_KEY)
        if not base_url:
            return None

        url = f"{base_url.rstrip('/')}/{tenant_id}/.well-known/openid-configuration"

        try:
            timeout = aiohttp.ClientTimeout(total=DISCOVERY_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        logger.warning(
                            "OIDC discovery for the IAM token endpoint returned %s", response.status
                        )
                        return None

                    document = await response.json(content_type=None)
        except Exception as ex:  # noqa: BLE001
            logger.warning("OIDC discovery for the IAM token endpoint was unreachable: %s", ex)
            return None

        token_endpoint = (document or {}).get("token_endpoint")
        if not isinstance(token_endpoint, str):
            logger.warning("OIDC discovery document has no token_endpoint.")
            return None

        parsed = urlparse(token_endpoint)
        if not parsed.scheme or not parsed.netloc:
            logger.warning("OIDC discovery returned a token_endpoint that is not an absolute URL.")
            return None

        return token_endpoint

    def reset(self) -> None:
        """Drop the per-tenant cache. Intended for tests."""
        self._discovered.clear()
        self._fallback_warning_logged = False


_resolver: Optional[DelegationTokenEndpointResolver] = None


def get_endpoint_resolver() -> DelegationTokenEndpointResolver:
    global _resolver
    if _resolver is None:
        _resolver = DelegationTokenEndpointResolver()
    return _resolver


def set_endpoint_resolver(resolver: Optional[DelegationTokenEndpointResolver]) -> None:
    global _resolver
    _resolver = resolver
