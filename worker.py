import asyncio
import logging
import sys

from blocks_genesis.core.secret_loader import get_blocks_secret
from blocks_genesis.core.worker import WorkerConsoleApp
from blocks_genesis.message.message_configuration import AzureServiceBusConfiguration, MessageConfiguration


logger = logging.getLogger(__name__)
message_config = MessageConfiguration(
    azure_service_bus_configuration=AzureServiceBusConfiguration(
        queues=["ai_queue", "ai_queue_2nd"],
        topics=[]
    )
)


def main():
    app = WorkerConsoleApp("blocks_ai_worker", message_config)

    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        logger.exception("\n👋 Graceful shutdown by user")
    except Exception as e:
        logger.exception(f"💥 Fatal error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
