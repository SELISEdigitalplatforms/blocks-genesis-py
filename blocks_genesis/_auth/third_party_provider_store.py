"""Supplies a tenant's active external identity providers to the authentication path.

Ports genesis-net `Auth/ThirdPartyJwtProviderStore.cs`. Reads `JwtThirdPartyProviders`
from the root database, beside `Tenants`.

Provider configuration used to ride along with the cached tenant, which cost nothing on
the authentication hot path. Its own collection means a database read per authentication
unless cached, so this caches per tenant with a short lifetime -- the same bounded
staleness the tenant cache already accepts.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorClient

from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._core.secret_loader import get_blocks_secret

_logger = logging.getLogger(__name__)

COLLECTION_NAME = "JwtThirdPartyProviders"

# How long a tenant's providers are served from memory. Deliberately short: a
# configuration change should take effect without a deploy, and the read is cheap.
CACHE_LIFETIME_SECONDS = 60


class ThirdPartyJwtProviderStore:
    def __init__(self, database=None):
        # Seam for tests. Otherwise resolved lazily: reading the vault here would make
        # this store's construction depend on secrets already being loaded, which is a
        # startup ordering trap for every host that builds it early.
        self._database = database
        self._cache: Dict[str, Tuple[List[ThirdPartyJwtProvider], float]] = {}
        self._loads: Dict[str, asyncio.Task] = {}
        self._load_lock = asyncio.Lock()

    async def get_active(self, tenant_id: str) -> List[ThirdPartyJwtProvider]:
        """Active providers for a tenant, newest read cached briefly.

        Never raises: an unreachable store yields an empty list, which rejects the
        request rather than failing it open.
        """
        if not tenant_id or not tenant_id.strip():
            return []

        cached = self._cache.get(tenant_id)
        if cached is not None and (time.monotonic() - cached[1]) <= CACHE_LIFETIME_SECONDS:
            return cached[0]

        # Deduplicate concurrent misses, so a burst against a cold cache produces one
        # query rather than one per request.
        async with self._load_lock:
            task = self._loads.get(tenant_id)
            if task is None:
                task = asyncio.create_task(self._load(tenant_id))
                self._loads[tenant_id] = task

        try:
            providers = await task
        finally:
            async with self._load_lock:
                if self._loads.get(tenant_id) is task:
                    self._loads.pop(tenant_id, None)

        self._cache[tenant_id] = (providers, time.monotonic())
        return providers

    def invalidate(self, tenant_id: str) -> None:
        """Drop a tenant's cached providers, so the next read reloads."""
        if tenant_id and tenant_id.strip():
            self._cache.pop(tenant_id, None)

    def _root_database(self):
        if self._database is None:
            secret = get_blocks_secret()
            self._database = AsyncIOMotorClient(secret.DatabaseConnectionString)[
                secret.RootDatabaseName
            ]
        return self._database

    async def _load(self, tenant_id: str) -> List[ThirdPartyJwtProvider]:
        try:
            cursor = self._root_database()[COLLECTION_NAME].find(
                {"TenantId": tenant_id, "IsActive": True}
            )
            rows = await cursor.to_list(length=None)
            return [ThirdPartyJwtProvider(**row) for row in rows]
        except Exception:
            # An empty list rejects the request. Returning nothing is the safe answer:
            # the alternative is falling through to some other validator on an outage.
            _logger.exception("[ThirdParty] Failed to load providers for tenant %s.", tenant_id)
            return []


_store: Optional[ThirdPartyJwtProviderStore] = None


def get_third_party_provider_store() -> Optional[ThirdPartyJwtProviderStore]:
    """The process-wide store, or None when this host never initialised one.

    None is not an error here -- the caller reports it and refuses the token, the way
    .NET refuses when the service is not registered.
    """
    return _store


def initialize_third_party_provider_store(database=None) -> ThirdPartyJwtProviderStore:
    global _store
    if _store is None:
        _store = ThirdPartyJwtProviderStore(database)
    return _store


def reset_third_party_provider_store() -> None:
    global _store
    _store = None
