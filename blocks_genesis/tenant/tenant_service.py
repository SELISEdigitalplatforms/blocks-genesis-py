# services/tenant_service.py
import asyncio
import logging
from typing import Dict, Optional, Tuple
from pymongo import MongoClient
from pymongo.database import Database

from interfaces.cache_client import ICacheClient
from entities.tenant import Tenant


class TenantService:
    """Manages tenant configuration with caching and real-time updates"""
    
    def __init__(self, cache_client: ICacheClient, db_connection_string: str, root_db_name: str):
        self.cache = cache_client
        self.client = MongoClient(db_connection_string)
        self.database = self.client[root_db_name]
        self.logger = logging.getLogger(__name__)
        
        # In-memory cache
        self._tenant_cache: Dict[str, Tenant] = {}
        self._version_key = "tenant::version"
        self._update_channel = "tenant::updates"
        
        # Initialize
        self._load_tenants()
        asyncio.create_task(self._subscribe_to_updates())
    
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Get tenant by ID with caching"""
        if not tenant_id:
            return None
        
        # Check cache first
        if tenant_id in self._tenant_cache:
            return self._tenant_cache[tenant_id]
        
        # Load from database
        tenant = self._load_tenant_from_db(tenant_id)
        if tenant:
            self._tenant_cache[tenant_id] = tenant
        
        return tenant
    
    def get_tenant_by_domain(self, domain: str) -> Optional[Tenant]:
        """Get tenant by application domain"""
        if not domain:
            return None
        
        try:
            tenant_dict = self.database["tenants"].find_one({
                "$or": [
                    {"ApplicationDomain": domain},
                    {"AllowedDomains": {"$in": [domain]}}
                ]
            })
            
            if tenant_dict:
                tenant = Tenant(**tenant_dict)
                self._tenant_cache[tenant.tenant_id] = tenant
                return tenant
                
        except Exception as e:
            self.logger.error(f"Error getting tenant by domain {domain}: {e}")
        
        return None
    
    def get_db_connection(self, tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
        """Get tenant database connection info"""
        tenant = self.get_tenant(tenant_id)
        if tenant:
            return tenant.db_name, tenant.db_connection_string
        return None, None
    
    async def invalidate_cache(self):
        """Invalidate tenant cache and notify other instances"""
        try:
            # Generate new version
            version = str(hash(f"{len(self._tenant_cache)}-{asyncio.get_event_loop().time()}"))
            
            # Update Redis
            await self.cache.add_string_value_async(self._version_key, version)
            
            # Publish update
            await self.cache.publish_async(self._update_channel, version)
            
            self.logger.info(f"Cache invalidated with version: {version}")
            
        except Exception as e:
            self.logger.error(f"Failed to invalidate cache: {e}")
    
    def _load_tenants(self):
        """Load all tenants into cache"""
        try:
            tenants = list(self.database["tenants"].find({}))
            self._tenant_cache.clear()
            
            for tenant_dict in tenants:
                tenant = Tenant(**tenant_dict)
                self._tenant_cache[tenant.tenant_id] = tenant
            
            self.logger.info(f"Loaded {len(tenants)} tenants into cache")
            
        except Exception as e:
            self.logger.error(f"Failed to load tenants: {e}")
    
    def _load_tenant_from_db(self, tenant_id: str) -> Optional[Tenant]:
        """Load single tenant from database"""
        try:
            tenant_dict = self.database["tenants"].find_one({
                "$or": [
                    {"_id": tenant_id},
                    {"TenantId": tenant_id}
                ]
            })
            
            if tenant_dict:
                return Tenant(**tenant_dict)
                
        except Exception as e:
            self.logger.error(f"Error loading tenant {tenant_id}: {e}")
        
        return None
    
    async def _subscribe_to_updates(self):
        """Subscribe to tenant update notifications"""
        try:
            await self.cache.subscribe_async(
                self._update_channel, 
                self._handle_update
            )
            self.logger.info("Subscribed to tenant updates")
            
        except Exception as e:
            self.logger.error(f"Failed to subscribe to updates: {e}")
    
    def _handle_update(self, channel: str, message: str):
        """Handle tenant cache update notification"""
        try:
            self.logger.info(f"Received tenant update: {message}")
            self._load_tenants()
        except Exception as e:
            self.logger.error(f"Error handling update: {e}")


# Global tenant service instance
_tenant_service: Optional[TenantService] = None

def get_tenant_service() -> TenantService:
    """Get global tenant service"""
    if _tenant_service is None:
        raise RuntimeError("Tenant service not initialized")
    return _tenant_service

def initialize_tenant_service(cache_client: ICacheClient, db_connection: str, root_db: str) -> TenantService:
    """Initialize global tenant service"""
    global _tenant_service
    _tenant_service = TenantService(cache_client, db_connection, root_db)
    return _tenant_service