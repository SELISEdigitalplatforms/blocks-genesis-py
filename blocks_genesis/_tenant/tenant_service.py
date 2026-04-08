import asyncio
import logging
import re
from typing import Dict, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field

from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._tenant.tenant import Tenant

_logger = logging.getLogger(__name__)


class TenantCacheUpdateMessage(BaseModel):
    tenant: Optional[Tenant] = Field(alias="Tenant", default=None)
    tenant_id: Optional[str] = Field(alias="TenantId", default=None)
    action: str = Field(alias="Action", default="upsert")

    class Config:
        extra = "ignore"
        validate_by_name = True


class TenantService:
    """Manages tenant configuration with caching and real-time updates"""
    TENANT_CACHE_UPDATE_ACTION_UPSERT = "upsert"
    TENANT_CACHE_UPDATE_ACTION_REMOVE = "remove"

    def __init__(self):
        self._blocks_secret = get_blocks_secret()
        self.cache: CacheClient = CacheProvider.get_client()
        if not self.cache:
            raise RuntimeError("Cache client not initialized")

        self.client = AsyncIOMotorClient(self._blocks_secret.DatabaseConnectionString)
        self.database = self.client[self._blocks_secret.RootDatabaseName]

        self._tenant_cache: Dict[str, Tenant] = {}
        self._tenant_load_in_progress: Dict[str, asyncio.Task] = {}
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
        if not tenant_id:
            return None
        tenant = self._tenant_cache.get(tenant_id)
        if tenant:
            return tenant

        # Deduplicate concurrent DB lookups for the same tenant id.
        async with self._tenant_load_lock:
            tenant_task = self._tenant_load_in_progress.get(tenant_id)
            if tenant_task is None:
                tenant_task = asyncio.create_task(self._load_tenant_from_db(tenant_id))
                self._tenant_load_in_progress[tenant_id] = tenant_task

        try:
            tenant = await tenant_task
        finally:
            async with self._tenant_load_lock:
                if self._tenant_load_in_progress.get(tenant_id) is tenant_task:
                    self._tenant_load_in_progress.pop(tenant_id, None)

        if tenant:
            self._tenant_cache[tenant.tenant_id] = tenant
        return tenant

    async def get_tenant_by_domain(self, domain: str) -> Optional[Tenant]:
        if not domain:
            return None
        try:
            tenant_dict = await self.database[self._collection_name].find_one({
                "$or": [
                    {"ApplicationDomain": domain},
                    {"ApplicationDomain": {"$regex": re.compile(domain)}},
                    {"AllowedDomains": {"$in": [domain]}}
                ]
            })
            if tenant_dict:
                tenant = Tenant(**tenant_dict)
                self._tenant_cache[tenant.tenant_id] = tenant
                return tenant
        except Exception as e:
            _logger.exception(f"Error getting tenant by domain {domain}: {e}")
        return None

    async def get_db_connection(self, tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
        tenant = await self.get_tenant(tenant_id)
        if tenant:
            return tenant.db_name, tenant.db_connection_string
        return None, None

    async def _load_tenant_from_db(self, tenant_id: str) -> Optional[Tenant]:
        try:
            tenant_dict = await self.database[self._collection_name].find_one({
                "$or": [
                    {"_id": tenant_id},
                    {"TenantId": tenant_id}
                ]
            })
            if tenant_dict:
                return Tenant(**tenant_dict)
        except Exception as e:
            _logger.exception(f"Error loading tenant {tenant_id}: {e}")
        return None
    
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
        tenant = update.tenant
        tenant_id = update.tenant_id

        if action == self.TENANT_CACHE_UPDATE_ACTION_REMOVE:
            if isinstance(tenant_id, str) and tenant_id:
                self._tenant_cache.pop(tenant_id, None)
                _logger.info(f"Tenant {tenant_id} removed from cache")
            return

        if tenant is None or not tenant.tenant_id:
            return

        if tenant.is_disabled:
            self._tenant_cache.pop(tenant.tenant_id, None)
            _logger.info(f"Disabled tenant {tenant.tenant_id} removed from cache")
            return

        self._tenant_cache[tenant.tenant_id] = tenant
        _logger.info(f"Tenant {tenant.tenant_id} upserted in cache")

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
            asyncio.create_task(self._handle_update(channel, message))
        except Exception as e:
            _logger.exception(f"Error creating tenant update task: {e}")

    async def _handle_update(self, channel: str, message: str):
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