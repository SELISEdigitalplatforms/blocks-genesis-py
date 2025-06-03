from contextlib import asynccontextmanager
from blocks_genesis.core.secret_loader import SecretLoader
from fastapi import FastAPI
import uvicorn

# Global variable to store secrets
secrets_instance = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage app lifespan - startup and shutdown"""
    global secrets_instance
    
    # Startup
    try:
        print("🚀 Starting up - Loading secrets...")
        secret_loader = SecretLoader()
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

# configure_logger()

# logger = logging.getLogger(__name__)

# logger.info("Application started")

# # When app shuts down cleanly, optionally stop background thread
# from log_persist import MongoHandler

# MongoHandler._mongo_logger.stop()

# enable_tracing(
#     app,
#     service_name="my-blocks-service",
#     mongo_uri="mongodb://localhost:27017",
#     db_name="traces"
# )



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