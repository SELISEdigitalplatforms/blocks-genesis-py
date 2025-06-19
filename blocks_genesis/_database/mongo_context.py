import logging
from typing import Dict, Optional
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.monitoring import register
from contextvars import ContextVar

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._database.mongo_event_subscriber import MongoEventSubscriber
from blocks_genesis._tenant.tenant_service import get_tenant_service
from blocks_genesis._database.db_context_provider import DbContextProvider

_db_cache: ContextVar[Dict[str, Database]] = ContextVar("_db_cache", default={})
_client_cache: ContextVar[Dict[str, MongoClient]] = ContextVar("_client_cache", default={})

_logger = logging.getLogger(__name__)


class MongoDbContextProvider(DbContextProvider):
    def __init__(self):
        self._logger = _logger
        self._tenants = get_tenant_service()
        register(MongoEventSubscriber())

    async def get_database(self, tenant_id: Optional[str] = None) -> Optional[Database]:
        tenant_id = tenant_id or getattr(BlocksContextManager.get_context(), 'tenant_id', None)
        if not tenant_id:
            self._logger.warning("Tenant ID is missing in context")
            return None

        dbs = _db_cache.get()
        clients = _client_cache.get()

        if tenant_id in dbs:
            return dbs[tenant_id]

        db_name, connection_string = await self._tenants.get_db_connection(tenant_id)
        if not connection_string or not db_name:
            raise ValueError(f"Missing connection info for tenant {tenant_id}")

        if connection_string not in clients:
            clients[connection_string] = self._create_mongo_client(connection_string)

        db = clients[connection_string][db_name]

        # Update context vars with new copy
        new_dbs = dict(dbs)
        new_dbs[tenant_id] = db
        _db_cache.set(new_dbs)
        _client_cache.set(dict(clients))

        return db

    def get_database_by_connection(self, connection_string: str, database_name: str) -> Database:
        if not connection_string:
            raise ValueError("Connection string cannot be empty or None.")
        if not database_name:
            raise ValueError("Database name cannot be empty or None.")

        db_key = database_name.lower()
        dbs = _db_cache.get()
        clients = _client_cache.get()

        if db_key in dbs:
            return dbs[db_key]

        if connection_string not in clients:
            clients[connection_string] = self._create_mongo_client(connection_string)

        db = clients[connection_string][database_name]

        new_dbs = dict(dbs)
        new_dbs[db_key] = db
        _db_cache.set(new_dbs)
        _client_cache.set(dict(clients))

        return db

    async def get_collection(self, collection_name: str, tenant_id: Optional[str] = None) -> Collection:
        db = await self.get_database(tenant_id)
        if not db:
            raise RuntimeError("No database found for tenant")
        return db[collection_name]

    def _create_mongo_client(self, connection_string: str) -> MongoClient:
        self._logger.info("Creating new MongoClient for connection string.")
        return MongoClient(connection_string)
