import logging
from fastapi import FastAPI, Request, logger
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.routing import APIRoute
from starlette.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._cache.redis_client import RedisClient
from blocks_genesis._core.secret_loader import SecretLoader
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._database.mongo_context import MongoDbContextProvider
from blocks_genesis._lmt.log_config import configure_logger
from blocks_genesis._lmt.mongo_log_exporter import MongoHandler
from blocks_genesis._lmt.tracing import configure_tracing
from blocks_genesis._message.azure.azure_message_client import AzureMessageClient
from blocks_genesis._message.message_configuration import MessageConfiguration
from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware
from blocks_genesis._middlewares.tenant_middleware import TenantValidationMiddleware
from blocks_genesis._tenant.tenant_service import initialize_tenant_service
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi

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



def custom_generate_unique_id(route: APIRoute):
    """
    Custom function to generate unique IDs for routes.
    This is useful for debugging and logging purposes.
    """
    return f"{route.name}-{route.path.replace('/', '_')}"

def fast_api_app(lifespan, **kwargs: FastAPI) -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        generate_unique_id_function=custom_generate_unique_id,
        **kwargs
    )
    
    return app
   
    
async def close_lifespan():
    logger.info("🛑 Shutting down services...")
    
    await AzureMessageClient.get_instance().close()
    # Shutdown logic
    if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
        MongoHandler._mongo_logger.stop()
        
def configure_middlewares(app: FastAPI, is_local: bool = False, show_docs: bool = False):
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
    
    @app.get("/ping", include_in_schema=False)
    async def health():
        return {
            "status": "healthy",
            "message": "pong",
        }
        
    @app.get("/swagger/index.html", include_in_schema=False)
    async def get_documentation(request:Request):
        root_path = request.scope.get("root_path", None)
        openapi_url = f"{root_path}/openapi.json" if root_path else "/openapi.json"
        print(openapi_url)
        if show_docs:
            return get_swagger_ui_html(openapi_url=openapi_url, title="Swagger")
        else:
            return "NOT_ALLOWED"

    @app.get("/openapi.json", include_in_schema=False)
    async def openapi():
        if show_docs:
            return get_openapi(title=app.title, version=app.version, routes=app.routes)
        return {}
    