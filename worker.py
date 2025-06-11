import asyncio
import logging
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

logger = logging.getLogger(__name__)
secret_loader = SecretLoader("blocks_ai_worker")

@asynccontextmanager
async def worker_lifecycle():
    logger.info("🚀 Initializing services...")
    logger.info("🔐 Loading secrets before app creation...")
    await secret_loader.load_secrets()
    logger.info("✅ Secrets loaded successfully!")

    configure_logger()
    logger.info("Logger started")

    # Enable tracing after secrets are loaded
    configure_tracing()
    logger.info("🔍 Tracing enabled successfully!")

    CacheProvider.set_client(RedisClient())
    await initialize_tenant_service()
    DbContext.set_provider(MongoDbContextProvider())

    logger.info("⚙️ Initializing Azure Message Worker")

    message_config = MessageConfiguration(
        connection=get_blocks_secret().MessageConnectionString,
        azure_service_bus_configuration=AzureServiceBusConfiguration(
            queues=["ai_queue"],
            topics=[]
        )
    )

    # ⚠️ Await configuration if it is async
    await ConfigAzureServiceBus().configure_queue_and_topic_async(message_config)

    await AzureMessageClient.initialize(message_config)

    message_worker = AzureMessageWorker(message_config)
    message_worker.initialize()

    try:
        yield message_worker  # Yield the worker to main()
    finally:
        logger.info("🛑 Stopping Azure Message Worker")
        await message_worker.stop()

        if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
            MongoHandler._mongo_logger.stop()

        logger.info("🚫 Shutdown complete")


async def main():
    async with worker_lifecycle() as message_worker:
        await message_worker.run()


if __name__ == "__main__":
    asyncio.run(main())
