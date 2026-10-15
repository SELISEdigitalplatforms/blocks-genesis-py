import asyncio
import hashlib
import logging
import time
from typing import Dict, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._tenant.tenant import Tenant

_logger = logging.getLogger(__name__)

# The interval blocks-genesis-net uses (Tenants.cs TokenParameterRevalidationInterval).
TENANT_REVALIDATION_INTERVAL_SECONDS = 60
UNKNOWN_TENANT_TTL_SECONDS = 30
UNKNOWN_TENANT_CACHE_MAX_ENTRIES = 4096


def _unknown_key(value: str) -> bytes:
    # A tenant key or host comes from the request, so only a fixed-size digest is kept.
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).digest()


def _normalize_domain(d: str) -> str:
    d = d.strip().lower()
    if d.startswith("http://"):
        d = d[7:]
    elif d.startswith("https://"):
        d = d[8:]
    return d


class TenantCacheUpdateMessage(BaseModel):
    tenant: Optional[Tenant] = Field(alias="Tenant", default=None)
    tenant_id: Optional[str] = Field(alias="TenantId", default=None)
    action: str = Field(alias="Action", default="upsert")

    class Config:
        extra = "ignore"
        validate_by_name = True


class TenantReadError(Exception):
    """The root database could not be read and this process has no copy of the tenant."""


class TenantService:
    """Manages tenant configuration with caching and real-time updates"""
    TENANT_CACHE_UPDATE_ACTION_UPSERT = "upsert"
    TENANT_CACHE_UPDATE_ACTION_REMOVE = "remove"
    _clock = staticmethod(time.monotonic)

    def __init__(self):
        self._blocks_secret = get_blocks_secret()
        self.cache: CacheClient = CacheProvider.get_client()
        if not self.cache:
            raise RuntimeError("Cache client not initialized")

        self.client = AsyncIOMotorClient(self._blocks_secret.DatabaseConnectionString)
        self.database = self.client[self._blocks_secret.RootDatabaseName]

        self._tenant_cache: Dict[str, Tenant] = {}
        self._root_tenant_id: Optional[str] = None
        self._tenant_load_in_progress: Dict[str, asyncio.Task] = {}
        self._tenant_loaded_at: Dict[str, float] = {}
        self._unknown_tenants: Dict[bytes, float] = {}
        self._unknown_domains: Dict[bytes, float] = {}
        self._update_channel = "tenant::updates"
        self._collection_name = "Tenants"

        self._initialized = False
        self._is_subscribed = False
        self._disposed = False
        self._initialize_lock = asyncio.Lock()
        self._tenant_load_lock = asyncio.Lock()

    async def initialize(self):
        """Explicit initializer for async setup"""
        async with self._initialize_lock:
            if self._initialized:
                return

            asyncio.create_task(self._subscribe_to_updates())
            await self._load_root_tenant_id()
            self._initialized = True
            _logger.info("TenantService initialized successfully")

    async def dispose_async(self):
        """Release subscription resources for tenant updates."""
        if self._disposed:
            return

        if self._is_subscribed:
            try:
                await self.cache.unsubscribe_async(self._update_channel)
            except Exception as e:
                _logger.exception(f"Error unsubscribing from tenant updates channel: {e}")

        self._disposed = True

    
    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """The tenant if it exists and is enabled; None otherwise, including when it cannot be read."""
        try:
            return await self.resolve_tenant(tenant_id)
        except TenantReadError:
            return None

    async def resolve_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Like get_tenant, but raises TenantReadError when the root database cannot be read
        and this process has no copy of the tenant."""
        if not tenant_id:
            return None
        tenant = self._tenant_cache.get(tenant_id)
        if tenant is not None and not self._is_stale(tenant_id):
            return tenant
        if tenant is None and self._is_known_unknown(self._unknown_tenants, tenant_id):
            return None

        # One database read per tenant at a time, for a cold load and a revalidation alike.
        async with self._tenant_load_lock:
            tenant_task = self._tenant_load_in_progress.get(tenant_id)
            if tenant_task is None:
                tenant_task = asyncio.create_task(self._refresh_tenant(tenant_id))
                self._tenant_load_in_progress[tenant_id] = tenant_task
        try:
            # Shielded: a cancelled caller must not cancel the read the other callers share.
            return await asyncio.shield(tenant_task)
        finally:
            async with self._tenant_load_lock:
                if self._tenant_load_in_progress.get(tenant_id) is tenant_task:
                    self._tenant_load_in_progress.pop(tenant_id, None)

    async def _refresh_tenant(self, tenant_id: str) -> Optional[Tenant]:
        try:
            tenant = await self._find_tenant(tenant_id)
        except Exception as e:
            cached = self._tenant_cache.get(tenant_id)
            _logger.warning(
                "Tenant %r could not be read (%s)%s",
                tenant_id[:64], type(e).__name__, "; keeping the cached copy" if cached else "",
            )
            if cached is None:
                raise TenantReadError(tenant_id[:64]) from None
            # Try again one interval later rather than on every request while the database is down.
            self._tenant_loaded_at[tenant_id] = self._clock()
            return cached
        if tenant is None or tenant.is_disabled:
            self._drop_tenant(tenant_id, *([tenant.tenant_id] if tenant else []))
            self._remember_unknown(self._unknown_tenants, tenant_id)
            return None
        self._cache_tenant(tenant)
        return tenant

    async def get_tenant_by_domain(self, domain: str) -> Optional[Tenant]:
        """Get tenant by application domain (matches .NET logic)."""
        if not domain or not domain.strip():
            return None

        normalized = _normalize_domain(domain)
        domains = [f"http://{normalized}", f"https://{normalized}"]

        match = self._match_domain_in_cache(normalized)
        if match is not None and self._is_stale(match.tenant_id):
            # Re-read by id: afterwards it is fresh, or gone if missing or disabled.
            await self.get_tenant(match.tenant_id)
            match = self._match_domain_in_cache(normalized)
        if match is not None:
            return match
        if self._is_known_unknown(self._unknown_domains, normalized):
            return None

        # Query database if not found in cache, verified owner first
        try:
            collection = self.database[self._collection_name]
            tenant_dict = await collection.find_one({
                "Applications": {
                    "$elemMatch": {"Domain": {"$in": domains}, "IsDomainVerified": True}
                }
            }) or await collection.find_one({
                "Applications": {
                    "$elemMatch": {"Domain": {"$in": domains}}
                }
            })
            if tenant_dict:
                tenant = Tenant(**tenant_dict)
                if not tenant.is_disabled:
                    self._cache_tenant(tenant)
                    return tenant
        except Exception as e:
            _logger.exception(f"Error getting tenant by domain {domain}: {e}")
            return None
        self._remember_unknown(self._unknown_domains, normalized)
        return None

    def _match_domain_in_cache(self, normalized: str) -> Optional[Tenant]:
        # A tenant that verified the domain wins over one that only claims it.
        matches = []
        for tenant in self._tenant_cache.values():
            apps = [
                app for app in (tenant.applications or [])
                if app.domain and _normalize_domain(app.domain) == normalized
            ]
            if apps:
                matches.append((tenant, any(app.is_domain_verified for app in apps)))
        if matches:
            return next((t for t, verified in matches if verified), matches[0][0])
        return None

    async def get_db_connection(self, tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
        tenant = await self.get_tenant(tenant_id)
        if tenant:
            return tenant.db_name, tenant.db_connection_string
        return None, None

    def get_root_tenant_id(self) -> Optional[str]:
        """The root tenant's id, as loaded from the root database at startup.

        Falls back to scanning the cache only when that load has not succeeded.
        """
        if self._root_tenant_id:
            return self._root_tenant_id
        return next(
            (t.tenant_id for t in self._tenant_cache.values() if t.is_root_tenant),
            None,
        )

    async def get_root_tenant_id_async(self) -> Optional[str]:
        """The root tenant's id, loading it from the root database if it is not known yet."""
        if not self._root_tenant_id:
            await self._load_root_tenant_id()
        return self._root_tenant_id

    async def _load_root_tenant_id(self) -> None:
        # Read only from the root database: a tenant update arriving on the pub/sub
        # channel must never be able to make itself the root tenant.
        try:
            tenant_dict = await self.database[self._collection_name].find_one(
                {"IsRootTenant": True}, sort=[("IsDisabled", 1)]
            )
            if tenant_dict:
                self._root_tenant_id = Tenant(**tenant_dict).tenant_id
        except Exception as e:
            _logger.exception(f"Error loading the root tenant: {e}")

    def get_tenant_database_connection_strings(self) -> Dict[str, Tuple[str, str]]:
        """
        Get all tenant database connection strings from cache.
        Returns dictionary mapping tenant IDs to (db_name, connection_string) tuples.
        
        Matches .NET Tenants.GetTenantDatabaseConnectionStrings()
        """
        return {
            tenant_id: (tenant.db_name, tenant.db_connection_string)
            for tenant_id, tenant in self._tenant_cache.items()
        }

    async def _find_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """The tenant's record in the root database, or None. Raises if the read fails."""
        tenant_dict = await self.database[self._collection_name].find_one({
            "$or": [
                {"_id": tenant_id},
                {"TenantId": tenant_id}
            ]
        })
        return Tenant(**tenant_dict) if tenant_dict else None

    def _is_stale(self, tenant_id: str) -> bool:
        loaded = self._tenant_loaded_at.get(tenant_id)
        return loaded is not None and self._clock() - loaded >= TENANT_REVALIDATION_INTERVAL_SECONDS

    def _cache_tenant(self, tenant: Tenant) -> None:
        self._tenant_cache[tenant.tenant_id] = tenant
        self._tenant_loaded_at[tenant.tenant_id] = self._clock()
        self._unknown_tenants.pop(_unknown_key(tenant.tenant_id), None)

    def _drop_tenant(self, *tenant_ids: str) -> None:
        for tenant_id in tenant_ids:
            self._tenant_cache.pop(tenant_id, None)
            self._tenant_loaded_at.pop(tenant_id, None)

    def _remember_unknown(self, entries: Dict[bytes, float], value: str) -> None:
        if len(entries) >= UNKNOWN_TENANT_CACHE_MAX_ENTRIES:
            entries.clear()
        entries[_unknown_key(value)] = self._clock() + UNKNOWN_TENANT_TTL_SECONDS

    def _is_known_unknown(self, entries: Dict[bytes, float], value: str) -> bool:
        key = _unknown_key(value)
        expires = entries.get(key)
        if expires is None:
            return False
        if self._clock() >= expires:
            del entries[key]
            return False
        return True
    
    async def update_tenant_version_async(self, cache_update: TenantCacheUpdateMessage) -> None:
        if cache_update is None:
            raise ValueError("cache_update cannot be None")

        try:
            normalized_update = self._normalize_cache_update(cache_update)
            if normalized_update is None:
                _logger.warning("Skipping invalid tenant cache update payload")
                return

            await self.cache.publish_async(
                self._update_channel,
                normalized_update.model_dump_json(by_alias=True)
            )
            _logger.info(
                "Tenant cache update published. TenantId: %s, Action: %s",
                self._resolve_tenant_id(normalized_update),
                normalized_update.action,
            )
        except Exception as e:
            _logger.exception(f"Failed to update tenant version: {e}")

    def _parse_tenant_cache_update(self, message: str) -> Optional[TenantCacheUpdateMessage]:
        try:
            return TenantCacheUpdateMessage.model_validate_json(message)
        except Exception:
            return None

    def _resolve_tenant_id(self, update: TenantCacheUpdateMessage) -> Optional[str]:
        if update.tenant_id and update.tenant_id.strip():
            return update.tenant_id
        if update.tenant and update.tenant.tenant_id and update.tenant.tenant_id.strip():
            return update.tenant.tenant_id
        return None

    def _normalize_cache_update(self, update: TenantCacheUpdateMessage) -> Optional[TenantCacheUpdateMessage]:
        action = (update.action or "").strip().lower()
        if action not in {
            self.TENANT_CACHE_UPDATE_ACTION_REMOVE,
            self.TENANT_CACHE_UPDATE_ACTION_UPSERT,
        }:
            return None

        if action == self.TENANT_CACHE_UPDATE_ACTION_REMOVE:
            tenant_id = self._resolve_tenant_id(update)
            if not tenant_id:
                return None

            return update.model_copy(
                update={
                    "action": self.TENANT_CACHE_UPDATE_ACTION_REMOVE,
                    "tenant_id": tenant_id,
                }
            )

        if update.tenant is None or not update.tenant.tenant_id:
            return None

        return update.model_copy(
            update={
                "action": self.TENANT_CACHE_UPDATE_ACTION_UPSERT,
                "tenant_id": update.tenant.tenant_id,
            }
        )

    async def _apply_update_message(self, update: TenantCacheUpdateMessage) -> None:
        action = (update.action or "").strip().lower()
        tenant_id = self._resolve_tenant_id(update)
        if not tenant_id:
            return

        if action == self.TENANT_CACHE_UPDATE_ACTION_REMOVE:
            self._drop_tenant(tenant_id)
            _logger.info("Tenant %r removed from cache", tenant_id[:64])
            return

        # The message only names the tenant. Anyone who can publish on this channel
        # could otherwise set its connection string, root flag, certificate or salt,
        # so the root database is re-read and the message's own fields are ignored.
        try:
            tenant = await self._find_tenant(tenant_id)
        except Exception as e:
            # Keep serving the copy we have rather than dropping a working tenant
            # because the database is briefly unreachable.
            _logger.warning(
                "Tenant %r could not be re-read (%s); keeping the cached copy",
                tenant_id[:64], type(e).__name__,
            )
            return

        if tenant is None or tenant.is_disabled:
            self._drop_tenant(tenant_id, *([tenant.tenant_id] if tenant else []))
            self._remember_unknown(self._unknown_tenants, tenant_id)
            _logger.info("Tenant %r removed from cache (missing or disabled)", tenant_id[:64])
            return

        self._cache_tenant(tenant)
        # The requested id may differ from the stored TenantId (matched on _id).
        self._unknown_tenants.pop(_unknown_key(tenant_id), None)
        # A new or changed tenant may own a domain that was unknown.
        self._unknown_domains.clear()
        _logger.info("Tenant %r refreshed from the database", tenant_id[:64])

    async def _subscribe_to_updates(self):
        if self._is_subscribed:
            return

        try:
            await self.cache.subscribe_async(
                self._update_channel,
                self._handle_update_wrapper
            )
            self._is_subscribed = True
            _logger.info("Subscribed to tenant updates")
        except Exception as e:
            _logger.exception(f"Failed to subscribe to updates: {e}")

    def _handle_update_wrapper(self, channel: str, message: str):
        """Sync wrapper for pub/sub clients that invoke callbacks without await."""
        try:
            _logger.info(f"Received update message on channel '{channel}'. Scheduling task.")
            asyncio.create_task(self._handle_update(message))
        except Exception as e:
            _logger.exception(f"Error creating tenant update task: {e}")

    async def _handle_update(self, message: str):
        """Process tenant update notifications and keep cache in sync."""
        try:
            _logger.info(f"Processing tenant update from message: {message}")
            cache_update = self._parse_tenant_cache_update(message)
            if cache_update is None:
                _logger.warning("Received invalid tenant update payload")
                return

            normalized_update = self._normalize_cache_update(cache_update)
            if normalized_update is None:
                _logger.warning("Skipping tenant update due to missing action/tenant details")
                return

            _logger.info(
                "Received tenant update notification. TenantId: %s, Action: %s",
                self._resolve_tenant_id(normalized_update),
                normalized_update.action,
            )

            await self._apply_update_message(normalized_update)
        except Exception as e:
            _logger.exception(f"Error handling tenant update notification: {e}")

# Global tenant service singleton instance
_tenant_service: Optional[TenantService] = None

def get_tenant_service() -> TenantService:
    if _tenant_service is None:
        raise RuntimeError("TenantService not initialized. Call initialize_tenant_service() first.")
    return _tenant_service

async def initialize_tenant_service() -> TenantService:
    global _tenant_service
    if _tenant_service is None:
        _tenant_service = TenantService()
    await _tenant_service.initialize()
    return _tenant_service