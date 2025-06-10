import asyncio
import logging
from contextlib import asynccontextmanager
from blocks_genesis.core.secret_loader import SecretLoader
from blocks_genesis.message.azure.azure_message_worker import AzureMessageWorker
from blocks_genesis.message.message_configuration import (
    AzureServiceBusConfiguration,
    MessageConfiguration,
)
from blocks_genesis.lmt.log_config import configure_logger
from blocks_genesis.lmt.mongo_log_exporter import MongoHandler
from blocks_genesis.lmt.tracing import configure_tracing

logger = logging.getLogger(__name__)
secret_loader = SecretLoader("blocks_ai_worker")

# Create your message config (can be loaded from env/secrets too)
message_config = MessageConfiguration(
    connection="your-service-bus-connection-string",  # Replace with secret_loader.get("...")
    azure_service_bus_configuration=AzureServiceBusConfiguration(
        queues=["queue1", "queue2"],
        topics=["topic1"]
    )
)

message_worker = AzureMessageWorker(message_config)

@asynccontextmanager
async def worker_lifecycle():
    logger.info("🔐 Loading secrets...")
    await secret_loader.load_secrets()
    logger.info("✅ Secrets loaded!")

    configure_logger()
    logger.info("📝 Logging configured")

    configure_tracing()
    logger.info("📡 Tracing initialized")

    logger.info("⚙️ Initializing Azure Message Worker")
    message_worker.initialize()

    yield  # Worker running

    logger.info("🛑 Stopping Azure Message Worker")
    await message_worker.stop()

    if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
        MongoHandler._mongo_logger.stop()
    logger.info("🚫 Shutdown complete")

async def main():
    async with worker_lifecycle():
        await message_worker.run()

if __name__ == "__main__":
    asyncio.run(main())
