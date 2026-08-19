"""Redeems a delegation grant for a short-lived Blocks access token.

Tenant comes from `BlocksContext`, never from a raw header. In a worker it was populated from the
message `SecurityContext`; in an API request it comes from the validated token.

The cache is a dict of per-grant entries, each guarded by its own `asyncio.Lock`. That lock is what
gives single-flight: fifty concurrent callers on one grant perform exactly one exchange. The dict
does not evict on its own, so entries are removed when a message settles and swept periodically.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp
from opentelemetry.trace import StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation.constants import (
    BLOCKS_KEY_HEADER,
    DELEGATION_GRANT_TOKEN_TYPE,
    TOKEN_EXCHANGE_GRANT_TYPE,
    TOKEN_RENEWAL_MARGIN_SECONDS,
)
from blocks_genesis._delegation.context import DelegatedTokenContext
from blocks_genesis._delegation.endpoint_resolver import (
    DelegationTokenEndpointResolver,
    get_endpoint_resolver,
)
from blocks_genesis._delegation.signature import new_nonce, sign
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant_service import get_tenant_service

logger = logging.getLogger(__name__)

EXCHANGE_TIMEOUT_SECONDS = 15
SWEEP_INTERVAL_CALLS = 64


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float

    def is_usable(self, now: float) -> bool:
        """Servable until one minute before expiry."""
        return now < self.expires_at - TOKEN_RENEWAL_MARGIN_SECONDS


class DelegatedTokenProvider:
    def __init__(
        self,
        tenant_service: Any = None,
        endpoint_resolver: Optional[DelegationTokenEndpointResolver] = None,
        session_factory: Any = None,
        time_func: Any = None,
    ) -> None:
        self._tenant_service = tenant_service
        self._endpoint_resolver = endpoint_resolver
        self._session_factory = session_factory
        self._time = time_func or time.time

        self._tokens: Dict[str, _CachedToken] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._call_count = 0

    @property
    def _tenants(self) -> Any:
        return self._tenant_service or get_tenant_service()

    @property
    def _resolver(self) -> DelegationTokenEndpointResolver:
        return self._endpoint_resolver or get_endpoint_resolver()

    async def get_token_async(self) -> Optional[str]:
        """A valid access token for the ambient grant, or None when it cannot be redeemed."""
        delegation_id = DelegatedTokenContext.current()
        if not delegation_id:
            return None

        context = BlocksContextManager.get_context()
        tenant_id = context.tenant_id if context else None
        if not tenant_id:
            logger.warning(
                "A delegation grant is in scope but BlocksContext carries no tenant; "
                "no delegated token will be issued."
            )
            return None

        self._call_count += 1
        if self._call_count % SWEEP_INTERVAL_CALLS == 0:
            self._sweep_expired()

        now = self._time()
        cached = self._tokens.get(delegation_id)
        if cached and cached.is_usable(now):
            return cached.access_token

        lock = self._locks.setdefault(delegation_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock: whoever held it first may already have refreshed.
            now = self._time()
            cached = self._tokens.get(delegation_id)
            if cached and cached.is_usable(now):
                return cached.access_token

            self._tokens.pop(delegation_id, None)

            try:
                token = await self._exchange_async(tenant_id, delegation_id)
            except Exception as ex:  # noqa: BLE001
                logger.error("Token exchange failed: %s", ex)
                return None

            if token is None:
                return None

            self._tokens[delegation_id] = token
            return token.access_token

    def invalidate(self, delegation_grant_id: Optional[str]) -> None:
        """Drop the cached token for a grant. Called when a message settles."""
        if not delegation_grant_id:
            return
        self._tokens.pop(delegation_grant_id, None)
        self._locks.pop(delegation_grant_id, None)

    async def _exchange_async(self, tenant_id: str, delegation_id: str) -> Optional[_CachedToken]:
        tenant = await self._tenants.get_tenant(tenant_id)
        salt = getattr(tenant, "tenant_salt", None) if tenant else None
        if not salt:
            logger.error("No tenant salt available for tenant %s; cannot sign a token exchange.", tenant_id)
            return None

        endpoint = await self._resolver.get_token_endpoint_async(tenant_id)

        ts = int(self._time())
        nonce = new_nonce()

        form = {
            "grant_type": TOKEN_EXCHANGE_GRANT_TYPE,
            "subject_token": delegation_id,
            "subject_token_type": DELEGATION_GRANT_TOKEN_TYPE,
            "nonce": nonce,
            "ts": str(ts),
            "sig": sign(tenant_id, delegation_id, nonce, ts, salt),
        }

        headers = {BLOCKS_KEY_HEADER: tenant_id}

        session_context = (
            self._session_factory()
            if self._session_factory is not None
            else aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=EXCHANGE_TIMEOUT_SECONDS))
        )

        # This is the one call that cannot go through a shared HTTP helper -- it would recurse into
        # redeeming a grant -- so it carries its own span and propagates trace context itself.
        # Exchange rate and p99 latency are the metrics that say whether delegation is healthy.
        with Activity("blocks.delegation.token_exchange") as activity:
            activity.set_property("blocks.tenant_id", tenant_id)

            # W3C traceparent, so the exchange links to IAM's side of the call.
            TraceContextTextMapPropagator().inject(headers)

            async with session_context as session:
                async with session.post(endpoint, data=form, headers=headers) as response:
                    body = await response.text()

                    activity.set_property("http.response.status_code", response.status)

                    if response.status < 200 or response.status >= 300:
                        # The OAuth error code is safe to log and tag. The grant id is neither.
                        error = _read_error_code(body)
                        activity.set_property("blocks.oauth_error", error)
                        activity.set_status(StatusCode.ERROR, error)

                        logger.warning(
                            "Token exchange rejected with HTTP %s (%s).", response.status, error
                        )
                        return None

            activity.set_status(StatusCode.OK)

        return self._read_token(body)

    def _read_token(self, body: str) -> Optional[_CachedToken]:
        try:
            payload = json.loads(body)
        except Exception as ex:  # noqa: BLE001
            logger.error("Token exchange response could not be parsed: %s", ex)
            return None

        access_token = _first_of(payload, "access_token", "accessToken", "AccessToken")
        if not access_token:
            return None

        expires_in = _first_of(payload, "expires_in", "expiresIn", "ExpiresIn")
        try:
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            expires_in = 0

        if expires_in <= 0:
            # A response with no lifetime gets a deliberately short one: twice the renewal margin,
            # so it is servable for the margin's length and then refetched. Trusting it for longer
            # would risk presenting a token IAM has already expired.
            expires_in = TOKEN_RENEWAL_MARGIN_SECONDS * 2

        return _CachedToken(access_token=str(access_token), expires_at=self._time() + expires_in)

    def _sweep_expired(self) -> None:
        now = self._time()
        stale = [key for key, token in self._tokens.items() if not token.is_usable(now)]
        for key in stale:
            self._tokens.pop(key, None)
            # Only drop an unlocked lock: a locked one has a refresh in flight.
            lock = self._locks.get(key)
            if lock is not None and not lock.locked():
                self._locks.pop(key, None)


def _first_of(payload: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


def _read_error_code(body: str) -> str:
    try:
        return str(json.loads(body).get("error", "unknown"))
    except Exception:  # noqa: BLE001
        return "unknown"


_provider: Optional[DelegatedTokenProvider] = None


def get_delegated_token_provider() -> DelegatedTokenProvider:
    global _provider
    if _provider is None:
        _provider = DelegatedTokenProvider()
    return _provider


def set_delegated_token_provider(provider: Optional[DelegatedTokenProvider]) -> None:
    global _provider
    _provider = provider


async def delegated_auth_headers(existing: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Headers for an outbound call, carrying the delegated token when a grant is in scope.

    The .NET SDK attaches the token inside `HttpService`, the helper every outbound Blocks call
    goes through. Python has no equivalent shared helper, so this is the parity API: merge the
    result into your request headers. An `Authorization` header already present always wins.
    """
    headers: Dict[str, str] = dict(existing or {})

    if any(key.lower() == "authorization" for key in headers):
        return headers

    if not DelegatedTokenContext.has_grant():
        return headers

    token = await get_delegated_token_provider().get_token_async()
    if not token:
        return headers

    headers["Authorization"] = f"Bearer {token}"

    context = BlocksContextManager.get_context()
    tenant_id = context.tenant_id if context else None
    if tenant_id and not any(key.lower() == BLOCKS_KEY_HEADER for key in headers):
        headers[BLOCKS_KEY_HEADER] = tenant_id

    return headers
