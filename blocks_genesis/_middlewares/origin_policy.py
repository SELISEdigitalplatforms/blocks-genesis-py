"""Which browser origins Genesis trusts: CORS, the /api origin check and cookie tokens."""
import logging
import time
from collections import OrderedDict
from typing import Callable, Iterable, Optional, Set, Tuple
from urllib.parse import urlparse

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core.configuration import get_setting_list, is_local_deployment
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import get_tenant_service

_logger = logging.getLogger(__name__)

CORS_ALLOWED_ORIGINS_KEY = "BLOCKS_CORS_ALLOWED_ORIGINS"

_DOMAIN_CACHE_SECONDS = 60
# A failed database read also comes back as "no tenant", so a refusal is kept only briefly.
_DOMAIN_NEGATIVE_CACHE_SECONDS = 10
_DOMAIN_CACHE_MAX_ENTRIES = 1024
# Uncached domain lookups allowed per second across the process, so a flood of distinct
# origins cannot become a flood of database queries.
_DOMAIN_LOOKUPS_PER_SECOND = 20
_THROTTLE_LOG_SECONDS = 60
_lookup_throttle = {"window_start": None, "count": 0, "logged_at": None}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_origin(value: Optional[str]) -> Optional[str]:
    """`scheme://host[:port]` for a well-formed http(s) origin, else None."""
    if not value or value.strip().lower() == "null":
        return None
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    if host.endswith("."):
        host = host[:-1]
    if (
        scheme not in _DEFAULT_PORTS
        or not host
        or parsed.username is not None
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    if ":" in host:
        host = f"[{host}]"
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def application_hosts(tenant: Tenant) -> Set[str]:
    return {
        BlocksContextManager.normalize_domain(app.domain)
        for app in (tenant.applications or [])
        if app.domain
    }


def is_tenant_origin_host(host: str, tenant: Tenant) -> bool:
    """True for one of the tenant's application hosts, or for localhost in a local deployment."""
    if BlocksContextManager.is_localhost_host(host):
        return is_local_deployment()
    return host in application_hosts(tenant)


def _take_lookup_slot(now: float) -> bool:
    """False when the process already made its uncached lookups for this second."""
    state = _lookup_throttle
    if state["window_start"] is None or not 0 <= now - state["window_start"] < 1:
        state["window_start"] = now
        state["count"] = 0
    if state["count"] < _DOMAIN_LOOKUPS_PER_SECOND:
        state["count"] += 1
        return True
    if state["logged_at"] is None or not 0 <= now - state["logged_at"] < _THROTTLE_LOG_SECONDS:
        state["logged_at"] = now
        _logger.warning(
            "CORS origin lookups exceeded %s per second; refusing unknown origins until the next second",
            _DOMAIN_LOOKUPS_PER_SECOND,
        )
    return False


class OriginPolicy:
    """Decides whether CORS may echo an origin with credentials."""

    def __init__(self, configured_origins: Iterable[str] = (), time_func: Callable[[], float] = time.monotonic):
        self._configured_origins = list(configured_origins)
        self._time = time_func
        # Resolved on first use: Starlette builds the middleware before a consumer's lifespan
        # has loaded its configuration.
        self._allow_list: Optional[Set[str]] = None
        self._cache: "OrderedDict[str, Tuple[bool, float]]" = OrderedDict()

    def _allowed_origins(self) -> Set[str]:
        if self._allow_list is None:
            candidates = self._configured_origins + get_setting_list(CORS_ALLOWED_ORIGINS_KEY)
            self._allow_list = {o for o in map(normalize_origin, candidates) if o}
        return self._allow_list

    async def is_allowed(self, origin: str) -> bool:
        normalized = normalize_origin(origin)
        if normalized is None:
            return False
        if normalized in self._allowed_origins():
            return True

        host = urlparse(normalized).hostname or ""
        if BlocksContextManager.is_localhost_host(host):
            return is_local_deployment()

        now = self._time()
        cached = self._cache.get(host)
        if cached is not None and cached[1] > now:
            return cached[0]

        try:
            tenant_service = get_tenant_service()
        except RuntimeError:
            return False
        if not _take_lookup_slot(now):
            return False
        tenant = await tenant_service.get_tenant_by_domain(host)
        allowed = tenant is not None and not tenant.is_disabled
        self._store(host, allowed, now)
        return allowed

    def _store(self, host: str, allowed: bool, now: float) -> None:
        self._cache.pop(host, None)
        if len(self._cache) >= _DOMAIN_CACHE_MAX_ENTRIES:
            for expired in [h for h, (_, expires_at) in self._cache.items() if expires_at <= now]:
                del self._cache[expired]
        while len(self._cache) >= _DOMAIN_CACHE_MAX_ENTRIES:
            self._cache.popitem(last=False)
        ttl = _DOMAIN_CACHE_SECONDS if allowed else _DOMAIN_NEGATIVE_CACHE_SECONDS
        self._cache[host] = (allowed, now + ttl)
