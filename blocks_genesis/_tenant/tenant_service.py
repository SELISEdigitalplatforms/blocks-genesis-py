import asyncio
import logging
import re
from typing import Dict, Optional, Tuple
from motor.motor_asyncio import AsyncIOMotorClient

from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._core.secret_loader import get_blocks_secret
from blocks_genesis._tenant.tenant import Tenant

_logger = logging.getLogger(__name__)

class TenantService:
    """Manages tenant configuration with caching and real-time updates"""

    def __init__(self):
        self._blocks_secret = get_blocks_secret()
        self.cache: CacheClient = CacheProvider.get_client()
        if not self.cache:
            raise RuntimeError("Cache client not initialized")

        self.client = AsyncIOMotorClient(self._blocks_secret.DatabaseConnectionString)
        self.database = self.client[self._blocks_secret.RootDatabaseName]

        self._tenant_cache: Dict[str, Tenant] = {}
        self._update_channel = "tenant::updates"
        self._collection_name = "Tenants"

        self._initialized = False
        self._initialize_lock = asyncio.Lock()

    async def initialize(self):
        """Explicit initializer for async setup"""
        async with self._initialize_lock:
            if self._initialized:
                return

            await self._load_tenants()
            asyncio.create_task(self._subscribe_to_updates())
            self._initialized = True
            _logger.info("TenantService initialized successfully")

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        if not tenant_id:
            return None

        tenant = self._tenant_cache.get(tenant_id)
        if tenant:
            return tenant

        tenant = await self._load_tenant_from_db(tenant_id)
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

    async def _load_tenants(self):
        try:
            cursor = self.database[self._collection_name].find({})
            self._tenant_cache.clear()
            async for tenant_dict in cursor:
                tenant = Tenant(**tenant_dict)
                self._tenant_cache[tenant.tenant_id] = tenant
            _logger.info(f"Loaded {len(self._tenant_cache)} tenants into cache")
        except Exception as e:
            _logger.exception(f"Failed to load tenants: {e}")

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

    async def _subscribe_to_updates(self):
        try:
            await self.cache.subscribe_async(
                self._update_channel,
                self._handle_update
            )
            _logger.info("Subscribed to tenant updates")
        except Exception as e:
            _logger.exception(f"Failed to subscribe to updates: {e}")

    async def _handle_update(self, channel: str, message: str):
        try:
            _logger.info(f"Received tenant update: {message}")
            await self._load_tenants()
        except Exception as e:
            _logger.exception(f"Error handling update: {e}")


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
