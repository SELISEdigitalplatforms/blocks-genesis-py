from dataclasses import dataclass
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from blocks_genesis.auth.auth import authorize
from blocks_genesis.core.api import close_lifespan, configure_lifespan, configure_middlewares
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.message.azure.azure_message_client import AzureMessageClient
from blocks_genesis.message.consumer_message import ConsumerMessage



logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_lifespan("blocks_ai_api")
    logger.info("✅ All services initialized!")

    yield  # app running here

    await close_lifespan()
    logger.info("🛑 App shutting down...")



app = FastAPI(lifespan=lifespan, debug=True)

# Add middleware in order
configure_middlewares(app);






@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    client = AzureMessageClient.get_instance()
    await client.send_to_consumer_async(ConsumerMessage(
        consumer_name="ai_queue",
        payload= AiMessage("Hello from AI API!"),
    ))
    db = DbContext.get_provider()
    return {"message": "Hello World", "secrets_loaded": True}



@app.get("/health", dependencies=[authorize(bypass_authorization=True)])
async def health():
    return {
        "status": "healthy",
        "secrets_status": "loaded" ,
    }
    
  
    
    
@dataclass
class AiMessage:
    message: str

