# Selise Blocks Genesis

A comprehensive FastAPI utilities library providing reusable components for building robust, scalable web applications with enterprise-grade features.

## 🚀 Features

- **Authentication & Authorization**: Built-in JWT token validation and role-based access control
- **Azure Integration**: Seamless integration with Azure Key Vault and Service Bus
- **Message Queue System**: Async message handling with consumer/producer patterns
- **Worker Applications**: Background task processing with graceful shutdown
- **Middleware Collection**: Pre-configured middlewares for logging, CORS, and monitoring
- **Lifecycle Management**: Automated service initialization and cleanup
- **Structured Logging**: Enhanced logging with structured output
- **OpenTelemetry Support**: Built-in observability and tracing

## 📦 Installation

```bash
uv add seliseblocks-genesis
```

## 🏃 Quick Start

### FastAPI Application

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from blocks_genesis.auth.auth import authorize
from blocks_genesis.core.api import close_lifespan, configure_lifespan, configure_middlewares

@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_lifespan("your_app_name")
    yield
    await close_lifespan()

app = FastAPI(lifespan=lifespan)
configure_middlewares(app)

@app.get("/health", dependencies=[authorize(bypass_authorization=True)])
async def health():
    return {"status": "healthy"}
```

### Worker Application

```python
import asyncio
from blocks_genesis.core.worker import WorkerConsoleApp

def main():
    app = WorkerConsoleApp("your_worker_name")
    asyncio.run(app.run())

if __name__ == "__main__":
    main()
```

### Message Handling

```python
from blocks_genesis.message.azure.azure_message_client import AzureMessageClient
from blocks_genesis.message.consumer_message import ConsumerMessage

# Send message
client = AzureMessageClient.get_instance()
await client.send_to_consumer_async(ConsumerMessage(
    consumer_name="your_queue",
    payload={"message": "Hello World!"}
))
```

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in your project root:

```env
# Azure Configuration
KEYVAULT__CLIENTID=your_client_id
KEYVAULT__CLIENTSECRET=your_client_secret
KEYVAULT__TENANTID=your_tenant_id
KEYVAULT__KEYVAULTURL=https://your-keyvault.vault.azure.net/
```

## 🛠️ Development Setup

### Prerequisites

- Python 3.9+
- Azure CLI (for local development)
- Docker (optional)

### Setup Instructions

1. **Clone the repository**
   ```bash
   git clone https://github.com/selisegroup/seliseblocks-genesis.git
   cd seliseblocks-genesis
   ```

2. **Create virtual environment**
   ```bash
   uv venv
   
   # Windows
   source .venv/Scripts/activate
   
   # Linux/Mac
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   uv sync
   ```

4. **Run the application**
   ```bash
   # API Server
   uvicorn api:app --reload
   
   # Worker
   python worker.py
   ```
5. **.env setup**
   ```bash
   APP_ENV=dev
   ```

## 📚 Components Overview

### Core Modules

- **`blocks_genesis.core.api`**: FastAPI application configuration and lifecycle management
- **`blocks_genesis.core.worker`**: Background worker application framework
- **`blocks_genesis.auth`**: Authentication and authorization utilities
- **`blocks_genesis.message`**: Message queue handling and Azure Service Bus integration
- **`blocks_genesis.cache`**: Redis caching utilities
- **`blocks_genesis.database`**: MongoDB connection and utilities

### Middleware

- CORS configuration
- Request/Response logging
- Authentication middleware
- Error handling
- OpenTelemetry tracing

## 🔧 Advanced Usage

### Custom Message Consumers

```python
from blocks_genesis.message.consumer_message import ConsumerMessage

class CustomConsumer:
    async def process_message(self, message: ConsumerMessage):
        # Your message processing logic
        print(f"Processing: {message.payload}")
```

### Custom Authentication

```python
from blocks_genesis.auth.auth import authorize

@app.get("/protected", dependencies=[authorize(roles=["admin"])])
async def protected_endpoint():
    return {"message": "Only admins can see this"}
```

## 🧪 Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=blocks_genesis

# Run specific test file
pytest tests/test_auth.py
```

## 📈 Monitoring & Observability

The library includes built-in support for:

- **Structured Logging**: JSON formatted logs with correlation IDs
- **OpenTelemetry**: Automatic tracing for FastAPI routes and dependencies
- **Health Checks**: Built-in health check endpoints
- **Metrics**: Custom metrics collection

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Issues**: [GitHub Issues](https://github.com/selisegroup/seliseblocks-genesis/issues)
- **Email**: mostafizur.rahman@selisegroup.com

---

**Built with ❤️ by [Selise Group](https://selisegroup.com)**