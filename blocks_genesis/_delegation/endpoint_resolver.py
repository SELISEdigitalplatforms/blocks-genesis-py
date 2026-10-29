"""Resolves IAM's token endpoint.

Callers never know the URL. Route paths are prefixable, so the effective path is
`{base}/{prefix}/oidc/token` and may never be hardcoded.

Resolution order, per section 5.5 of the delegated-access spec:

1. Discovery (primary) -- `GET {BLOCKS_IAM_BASE_URL}/{tenant_id}/.well-known/openid-configuration`,
   taking `token_endpoint`. Survives prefix and route changes.
2. `BLOCKS_IAM_TOKEN_ENDPOINT` (fallback) -- a complete URL, not a base and not a template, so no
   prefix is ever guessed.

A discovered endpoint is accepted only on the same origin as `BLOCKS_IAM_BASE_URL`, and neither
call follows a redirect, so the grant is only ever posted to IAM's internal address.

A successful discovery is cached per tenant for `DISCOVERY_CACHE_SECONDS`. A fallback is not
cached, so discovery is retried lazily on later calls. If neither is configured, startup fails.

`Tenant.jwt_token_parameters.issuer` is deliberately not used as a base URL: IAM separates
`issuer` from `apiBase`, and the issuer is an identifier, not a reachable API host.
"""

import asyncio
import logging
import os
import time
from typing import Callable, Dict, Optional, Tuple
from urllib.parse import quote, urlparse

import aiohttp

from blocks_genesis._core.configuration import get_configurations
from blocks_genesis._delegation.constants import (
    DISCOVERY_CACHE_SECONDS,
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


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> Optional[Tuple[str, str, Optional[int]]]:
    """Scheme, host and port, from the parsed URL so userinfo, case and a trailing dot cannot fool it."""
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".")
        port = parsed.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if not scheme or not host:
        return None
    return scheme, host, port


class DelegationTokenEndpointResolver:
    def __init__(self, time_func: Optional[Callable[[], float]] = None) -> None:
        self._discovered: Dict[str, Tuple[str, float]] = {}
        self._lock = asyncio.Lock()
        self._fallback_warning_logged = False
        self._time = time_func or time.monotonic

    def _cached(self, tenant_id: str) -> Optional[str]:
        entry = self._discovered.get(tenant_id)
        if entry and self._time() < entry[1]:
            return entry[0]
        return None

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

        cached = self._cached(tenant_id)
        if cached:
            return cached

        async with self._lock:
            cached = self._cached(tenant_id)
            if cached:
                return cached

            discovered = await self._try_discover_async(tenant_id)
            if discovered:
                self._discovered[tenant_id] = (discovered, self._time() + DISCOVERY_CACHE_SECONDS)
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

        url = f"{base_url.rstrip('/')}/{quote(tenant_id, safe='')}/.well-known/openid-configuration"

        try:
            timeout = aiohttp.ClientTimeout(total=DISCOVERY_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # A 3xx is not followed; it fails the status check below.
                async with session.get(url, allow_redirects=False) as response:
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

        expected = _origin(base_url)
        if expected is None or _origin(token_endpoint) != expected:
            logger.warning(
                "OIDC discovery returned a token_endpoint outside the origin of '%s'; it was rejected.",
                IAM_BASE_URL_KEY,
            )
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
