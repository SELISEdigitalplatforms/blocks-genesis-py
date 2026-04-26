import asyncio
from dataclasses import dataclass
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from blocks_genesis._auth.auth import authorize
from blocks_genesis._core.api import close_lifespan, configure_lifespan, configure_genesis, fast_api_app
from blocks_genesis._core.configuration import get_configurations, load_configurations
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._message.azure.azure_message_client import AzureMessageClient
from blocks_genesis._message.consumer_message import ConsumerMessage
from blocks_genesis._message.message_configuration import AzureServiceBusConfiguration, MessageConfiguration



logger = logging.getLogger(__name__)
message_config = MessageConfiguration(
    azure_service_bus_configuration=AzureServiceBusConfiguration(
        queues=["ai_queue"],
        topics=[]
    )
)

config_dir = Path(__file__).resolve().parent / "config"
print(config_dir)
load_configurations(config_dir)
config = get_configurations()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_lifespan("blocks_ai_api", message_config)
    logger.info("✅ All services initialized!")

    yield  # app running here

    await close_lifespan()
    logger.info("🛑 App shutting down...")




# Serve static files (frontend) from / if desired, or let configure_genesis handle it
app = fast_api_app(lifespan=lifespan)

# Use the new configure_genesis function for all middleware and static setup
from blocks_genesis._core.api import configure_genesis
configure_genesis(app, show_docs=True, serve_static=True)

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    client = AzureMessageClient.get_instance()
    message = AiMessage(message="Hello from AI API!")
    await client.send_to_consumer_async(ConsumerMessage(
        consumer_name="ai_queue",
        payload=message.model_dump(),
        payload_type="AiMessage"
    ))
    return {"message": "Hello World", "secrets_loaded": True}



@app.get("/health", dependencies=[authorize(bypass_authorization=True)])
async def health():
    return {
        "status": "healthy",
        "secrets_status": "loaded" ,
    }
    
async def gen(message: str):
    for i in range(5):
        yield f"data: {message} {i}\n\n"
        await asyncio.sleep(1)
        
class Input(BaseModel):
    message: str

@app.post("/sse") #python -m uvicorn api:app --reload
async def sse(data: Input):
    return StreamingResponse(
        gen(data.message),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
 
    

class AiMessage(BaseModel):
    message: str

