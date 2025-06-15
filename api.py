from dataclasses import dataclass
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from blocks_genesis.auth.auth import authorize
from blocks_genesis.core.secret_loader import SecretLoader, get_blocks_secret
from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.cache.redis_client import RedisClient
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.database.mongo_context import MongoDbContextProvider
from blocks_genesis.message.azure.azure_message_client import AzureMessageClient
from blocks_genesis.message.consumer_message import ConsumerMessage
from blocks_genesis.message.message_configuration import AzureServiceBusConfiguration, MessageConfiguration
from blocks_genesis.middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware
from blocks_genesis.middlewares.tenant_middleware import TenantValidationMiddleware
from blocks_genesis.middlewares.trace_middleware import TraceContextMiddleware
from blocks_genesis.tenant.tenant_service import initialize_tenant_service
from blocks_genesis.lmt.log_config import configure_logger
from blocks_genesis.lmt.mongo_log_exporter import MongoHandler
from blocks_genesis.lmt.tracing import configure_tracing

logger = logging.getLogger(__name__)
secret_loader = SecretLoader("blocks_ai_api")


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    
    message_config = MessageConfiguration(
        connection=get_blocks_secret().MessageConnectionString,
        azure_service_bus_configuration=AzureServiceBusConfiguration(
            queues=["ai_queue"],
            topics=[]
        )
    )
    AzureMessageClient.initialize(message_config)
    
    logger.info("✅ All services initialized!")

    yield  # app running here

    logger.info("🛑 Shutting down services...")
    
    await AzureMessageClient.get_instance().close()
    # Shutdown logic
    if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
        MongoHandler._mongo_logger.stop()
    logger.info("🛑 App shutting down...")



app = FastAPI(lifespan=lifespan, debug=True)

# Add middleware in order
app.add_middleware(GlobalExceptionHandlerMiddleware)
app.add_middleware(TraceContextMiddleware)
app.add_middleware(TenantValidationMiddleware)


FastAPIInstrumentor.instrument_app(app)  ### Instrument FastAPI for OpenTelemetry


@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    client = AzureMessageClient.get_instance()
    await client.send_to_consumer_async(ConsumerMessage(
        consumer_name="ai_queue",
        payload= AiMessage("Hello from AI API!"),
    ))
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

