import asyncio
import logging
import sys

from blocks_genesis._core.worker import WorkerConsoleApp
from blocks_genesis._message.message_configuration import AzureServiceBusConfiguration, MessageConfiguration
from test_consumer import handle_user_created_event



logger = logging.getLogger(__name__)
message_config = MessageConfiguration(
    azure_service_bus_configuration=AzureServiceBusConfiguration(
        queues=["ai_queue"],
        topics=[]
    )
)


async def callback():
    print("Callback called")



def main(): 
    app = WorkerConsoleApp("blocks_ai_worker", message_config, {
        "AiMessage": handle_user_created_event
    })
    
    try:
        asyncio.run(app.run(callback))
    except KeyboardInterrupt:
        logger.exception("\n👋 Graceful shutdown by user")
    except Exception as e:
        logger.exception(f"💥 Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
    
    

