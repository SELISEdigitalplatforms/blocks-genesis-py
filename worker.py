import asyncio
import logging
import sys
from contextlib import asynccontextmanager

from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.cache.redis_client import RedisClient
from blocks_genesis.core.secret_loader import SecretLoader, get_blocks_secret
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.database.mongo_context import MongoDbContextProvider
from blocks_genesis.message.azure.azure_message_client import AzureMessageClient
from blocks_genesis.message.azure.azure_message_worker import AzureMessageWorker
from blocks_genesis.message.azure.config_azure_service_bus import ConfigAzureServiceBus
from blocks_genesis.message.message_configuration import (
    AzureServiceBusConfiguration,
    MessageConfiguration,
)
from blocks_genesis.lmt.log_config import configure_logger
from blocks_genesis.lmt.mongo_log_exporter import MongoHandler
from blocks_genesis.lmt.tracing import configure_tracing
from blocks_genesis.tenant.tenant_service import initialize_tenant_service


class WorkerConsoleApp:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.secret_loader = SecretLoader("blocks_ai_worker")
        self.message_worker: AzureMessageWorker = None

    @asynccontextmanager
    async def setup_services(self):
        self.logger.info("🚀 Starting Blocks AI Worker Console App...")

        try:
            self.logger.info("🔐 Loading secrets...")
            await self.secret_loader.load_secrets()
            self.logger.info("✅ Secrets loaded successfully")

            configure_logger()
            self.logger.info("📝 Logger configured")

            configure_tracing()
            self.logger.info("🔍 Tracing configured")

            CacheProvider.set_client(RedisClient())
            await initialize_tenant_service()
            DbContext.set_provider(MongoDbContextProvider())
            self.logger.info("✅ Cache, TenantService, and Mongo Context initialized")

            message_config = MessageConfiguration(
                connection=get_blocks_secret().MessageConnectionString,
                azure_service_bus_configuration=AzureServiceBusConfiguration(
                    queues=["ai_queue", "ai_queue_2nd"],
                    topics=[]
                )
            )

            ConfigAzureServiceBus().configure_queue_and_topic(message_config)
            AzureMessageClient.initialize(message_config)

            self.message_worker = AzureMessageWorker(message_config)
            self.message_worker.initialize()

            self.logger.info("✅ Azure Message Worker initialized and ready")
            yield self.message_worker

        except Exception as ex:
            self.logger.error(f"❌ Startup failed: {ex}", exc_info=True)
            raise

        finally:
            await self.cleanup()

    async def cleanup(self):
        self.logger.info("🛑 Cleaning up services...")

        if self.message_worker:
            await self.message_worker.stop()

        if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
            MongoHandler._mongo_logger.stop()

        self.logger.info("✅ Shutdown complete")

    async def run(self):
        async with self.setup_services() as worker:
            self.logger.info("🔄 Worker running... Press Ctrl+C to stop")
            try:
                await worker.run()
            except asyncio.CancelledError:
                self.logger.info("🛑 Received cancellation signal")
            except KeyboardInterrupt:
                self.logger.info("⏹️ Received interrupt signal")


def main():
    app = WorkerConsoleApp()

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\n👋 Graceful shutdown by user")
    except Exception as e:
        print(f"💥 Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
