import logging
import threading
from typing import Dict, Optional, Tuple

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.monitoring import register

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._database.mongo_event_subscriber import MongoEventSubscriber
from blocks_genesis._tenant.tenant_service import get_tenant_service

_logger = logging.getLogger(__name__)

# Keyed by connection AND database name together. Two clusters may each hold a database
# of the same name, and a key made of the name alone hands one cluster's handle to the
# other -- a write that lands in the wrong cluster with no error and nothing in the log.
#
# Process-wide rather than context-local: a MongoClient is a connection pool, and one per
# async context multiplies pools without bound.
_databases: Dict[Tuple[str, str], Database] = {}
_clients: Dict[str, MongoClient] = {}
_lock = threading.Lock()


class MongoDbContextProvider:
    def __init__(self):
        self._logger = _logger
        self._tenants = get_tenant_service()
        register(MongoEventSubscriber())

    async def get_database(self, tenant_id: Optional[str] = None) -> Optional[Database]:
        tenant_id = tenant_id or getattr(BlocksContextManager.get_context(), 'tenant_id', None)
        if not tenant_id:
            self._logger.warning("Tenant ID is missing in context")
            return None

        # Resolved on every call, so a placement change takes effect without a restart.
        # Only the lookup repeats -- the handle itself is still cached below.
        db_name, connection_string = await self._tenants.get_db_connection(tenant_id)
        if not connection_string or not db_name:
            raise ValueError(f"Missing connection info for tenant {tenant_id}")

        return self.get_database_by_connection(connection_string, db_name)

    def get_database_by_connection(self, connection_string: str, database_name: str) -> Database:
        if not connection_string:
            raise ValueError("Connection string cannot be empty or None.")
        if not database_name:
            raise ValueError("Database name cannot be empty or None.")

        key = (connection_string, database_name)
        database = _databases.get(key)
        if database is not None:
            return database

        with _lock:
            database = _databases.get(key)
            if database is None:
                database = self._get_client(connection_string)[database_name]
                _databases[key] = database

        return database

    async def get_collection(self, collection_name: str, tenant_id: Optional[str] = None) -> Collection:
        db = await self.get_database(tenant_id)
        if db is None:
            raise RuntimeError("No database found for tenant")
        return db[collection_name]

    def _get_client(self, connection_string: str) -> MongoClient:
        """One client per connection. Callers hold `_lock`, so no two are ever created."""
        client = _clients.get(connection_string)
        if client is None:
            # Never log the connection string itself.
            self._logger.info("Creating new MongoClient.")
            client = MongoClient(
                connection_string,
                retryReads=True,
                retryWrites=True,
                serverSelectionTimeoutMS=15000,
                connectTimeoutMS=10000,
            )
            _clients[connection_string] = client
        return client
