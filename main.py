from contextlib import asynccontextmanager
import logging
from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.cache.redis_client import RedisClient
from blocks_genesis.core.secret_loader import SecretLoader
from fastapi import FastAPI
import uvicorn

from blocks_genesis.database.db_context import DbContext
from blocks_genesis.database.mongo_context import MongoDbContextProvider
from blocks_genesis.lmt.log_config import configure_logger
from blocks_genesis.lmt.mongo_log_exporter import MongoHandler
from blocks_genesis.lmt.tracing import enable_tracing
from blocks_genesis.tenant.tenant_service import TenantService

# Global variable to store secrets
secrets_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifespan - startup and shutdown"""
    global secrets_instance
    
    # Startup
    try:
        print("🚀 Starting up - Loading secrets...")
        secret_loader = SecretLoader("blocks_ai_api")
        await secret_loader.load_secrets()
        secrets_instance = secret_loader
        print("✅ Secrets loaded successfully!")
    except Exception as e:
        print(f"❌ Failed to load secrets: {e}")
        # Uncomment to prevent startup on secret loading failure
        # raise e
    
    yield  # App is running
    
    # Shutdown
    print("🛑 Shutting down...")
    secrets_instance = None

app = FastAPI(lifespan=lifespan)

configure_logger()
logger = logging.getLogger(__name__)
logger.info("Logger started")
MongoHandler._mongo_logger.stop()

enable_tracing(app)

TenantService.initialize()
CacheProvider.set_client(RedisClient())
DbContext.set_provider(MongoDbContextProvider())
 





@app.get("/")
async def root():
    return {"message": "Hello World", "secrets_loaded": secrets_instance is not None}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "secrets_status": "loaded" if secrets_instance else "not_loaded"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)