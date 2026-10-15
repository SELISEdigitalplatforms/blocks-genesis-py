import logging
from fastapi import FastAPI, HTTPException, Request, logger
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.routing import APIRoute
from starlette.routing import Route
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
import os
from pathlib import Path
from typing import List, Optional, Sequence, Union
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._cache.redis_client import RedisClient
from blocks_genesis._core.configuration import get_setting_list, is_local_deployment
from blocks_genesis._core.secret_loader import SecretLoader, get_blocks_secret
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._database.mongo_context import MongoDbContextProvider
from blocks_genesis._lmt.log_config import configure_logger
from blocks_genesis._lmt.mongo_log_exporter import MongoHandler
from blocks_genesis._lmt.tracing import configure_tracing
from blocks_genesis._message.azure.azure_message_client import AzureMessageClient
from blocks_genesis._message.rabbit_mq.rabbit_message_client import RabbitMessageClient
from blocks_genesis._message.message_configuration import MessageConfiguration
from blocks_genesis._middlewares.cors_middleware import GenesisCORSMiddleware
from blocks_genesis._middlewares.global_exception_middleware import GlobalExceptionHandlerMiddleware
from blocks_genesis._middlewares.tenant_middleware import TenantValidationMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from blocks_genesis._delegation.endpoint_resolver import get_endpoint_resolver
from blocks_genesis._auth.third_party_provider_store import (
    initialize_third_party_provider_store,
)
from blocks_genesis._tenant.tenant_service import initialize_tenant_service
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

TRUSTED_PROXY_HOSTS_KEY = "BLOCKS_TRUSTED_PROXY_HOSTS"

async def configure_lifespan(name: str, message_config: MessageConfiguration):
    logger.info("Initializing services...")
    logger.info("Loading secrets before app creation...")
    secret_loader = SecretLoader(name)
    await secret_loader.load_secrets()
    logger.info("Secrets loaded successfully!")
    
    configure_logger()
    logger.info("Logger started")

    # Enable tracing after secrets are loaded
    configure_tracing()
    logger.info("Tracing enabled successfully!")

    CacheProvider.set_client(RedisClient())
    await initialize_tenant_service()
    initialize_third_party_provider_store()
    DbContext.set_provider(MongoDbContextProvider())

    # Fails fast when neither BLOCKS_IAM_BASE_URL nor BLOCKS_IAM_TOKEN_ENDPOINT is configured.
    # The token endpoint is never guessed, so an unconfigured deployment must not start.
    get_endpoint_resolver().ensure_configured()
    logger.info("Delegated access configured; the IAM token endpoint resolves by discovery per tenant")

    if message_config is not None:
        message_config.connection = message_config.connection or get_blocks_secret().MessageConnectionString
        message_config.resolve_provider()
        if message_config.rabbit_mq_configuration is not None:
            RabbitMessageClient.initialize(message_config)
        if message_config.azure_service_bus_configuration is not None:
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
        # root_path removed to allow FE/static at /
        **kwargs
    )
    
    return app
   
    
async def close_lifespan():
    logger.info("Shutting down services...")

    try:
        await RabbitMessageClient.get_instance().close()
    except RuntimeError:
        pass
    try:
        await AzureMessageClient.get_instance().close()
    except RuntimeError:
        pass
    # Shutdown logic
    if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
        MongoHandler._mongo_logger.stop()
        
def _trusted_proxy_hosts() -> Union[str, List[str]]:
    """Hosts whose X-Forwarded-For and X-Forwarded-Proto are trusted. Defaults to any host."""
    hosts = get_setting_list(TRUSTED_PROXY_HOSTS_KEY)
    if hosts and "*" not in hosts:
        return hosts
    if not is_local_deployment():
        logger.warning(
            "%s is unset or '*': X-Forwarded-For and X-Forwarded-Proto are trusted from any host. "
            "Set it to the addresses of the proxies in front of this service.",
            TRUSTED_PROXY_HOSTS_KEY,
        )
    return "*"


def _remove_docs_routes(app: FastAPI) -> None:
    """Remove the docs routes FastAPI registers when the app is created, matched by the app's own paths."""
    if app.openapi_url:
        docs_paths = {app.openapi_url, app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url} - {None}
        # FastAPI adds these as plain starlette Routes; a consumer's own APIRoute or Mount at the
        # same path is left alone.
        app.router.routes[:] = [
            r for r in app.router.routes
            if isinstance(r, APIRoute) or type(r) is not Route or r.path not in docs_paths
        ]
    app.openapi_url = app.docs_url = app.redoc_url = None


def configure_genesis(app: FastAPI, show_docs: bool = False, serve_static: bool = False, static_mount_path: str = "/", static_dir: str = "", cors_origins: Optional[Sequence[str]] = None):
    """
    Configure all genesis middlewares and optionally mount static files.
    Args:
        app: FastAPI app instance
        show_docs: Serve the API docs; when False, FastAPI's docs routes are removed and the Genesis ones answer 404
        serve_static: If True, mount static files
        static_mount_path: URL path to mount static files (default: "/")
        static_dir: Directory to serve static files from (default: ./static in user project root)
        cors_origins: Origins allowed by CORS in addition to BLOCKS_CORS_ALLOWED_ORIGINS and the tenants' application domains
    """
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_trusted_proxy_hosts())
    app.add_middleware(GZipMiddleware)
    app.add_middleware(TenantValidationMiddleware, included_paths=["/api"])
    app.add_middleware(GlobalExceptionHandlerMiddleware)
    FastAPIInstrumentor.instrument_app(app)
    app.add_middleware(GenesisCORSMiddleware, allowed_origins=list(cors_origins or []))
    if not show_docs:
        _remove_docs_routes(app)
    if serve_static:
        # Determine the static directory in a robust, pythonic way
        if static_dir:
            resolved_static_dir = Path(static_dir).expanduser().resolve()
        else:
            # Default to ./static in the user's current working directory
            resolved_static_dir = Path.cwd() / "static"

        if not resolved_static_dir.exists() or not resolved_static_dir.is_dir():
            raise FileNotFoundError(f"Static directory '{resolved_static_dir}' does not exist. Please create it or specify a valid static_dir.")

        app.mount(
            static_mount_path,
            StaticFiles(directory=str(resolved_static_dir), html=True),
            name="static"
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
        logger.debug("Serving the Swagger UI page")
        if show_docs:
            return get_swagger_ui_html(openapi_url=openapi_url, title="Swagger")
        raise HTTPException(status_code=404)
    @app.get("/openapi.json", include_in_schema=False)
    async def openapi():
        if show_docs:
            return get_openapi(title=app.title, version=app.version, routes=app.routes)
        raise HTTPException(status_code=404)
