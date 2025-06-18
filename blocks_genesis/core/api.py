import logging
from fastapi import FastAPI, logger
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.cache.redis_client import RedisClient
from blocks_genesis.core.secret_loader import SecretLoader
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.database.mongo_context import MongoDbContextProvider
from blocks_genesis.lmt.log_config import configure_logger
from blocks_genesis.lmt.mongo_log_exporter import MongoHandler
from blocks_genesis.lmt.tracing import configure_tracing
from blocks_genesis.message.azure.azure_message_client import AzureMessageClient
from blocks_genesis.message.message_configuration import MessageConfiguration
from blocks_genesis.middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware
from blocks_genesis.middlewares.tenant_middleware import TenantValidationMiddleware
from blocks_genesis.tenant.tenant_service import initialize_tenant_service

logger = logging.getLogger(__name__)

async def configure_lifespan(name: str, message_config: MessageConfiguration):
    logger.info("🚀 Initializing services...")
    logger.info("🔐 Loading secrets before app creation...")
    secret_loader = SecretLoader(name)
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
    
    AzureMessageClient.initialize(message_config)
    
    
async def close_lifespan():
    logger.info("🛑 Shutting down services...")
    
    await AzureMessageClient.get_instance().close()
    # Shutdown logic
    if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
        MongoHandler._mongo_logger.stop()
        
def configure_middlewares(app: FastAPI, is_local: False):
    if not is_local:
        app.add_middleware(HTTPSRedirectMiddleware)
        
    app.add_middleware(GZipMiddleware)
    app.add_middleware(TenantValidationMiddleware)
    app.add_middleware(GlobalExceptionHandlerMiddleware)
    FastAPIInstrumentor.instrument_app(app)  ### Instrument FastAPI for OpenTelemetry
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    @app.get("/ping")
    async def health():
        return {
            "status": "healthy",
            "secrets_status": "loaded" ,
        }