# Blocks Genesis
> Reusable FastAPI building blocks for multi-tenant services: auth context, tenant resolution, Redis cache, MongoDB access, message bus integration, and observability.

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Docker](https://img.shields.io/badge/docker-not%20bundled-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)
![AI Framework](https://img.shields.io/badge/ai-framework%20agnostic-lightgrey)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture Overview](#architecture-overview)
- [API / Endpoints](#api--endpoints)
- [Tech Stack](#tech-stack)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration - Environment Variables](#configuration---environment-variables)
- [Running the Project Locally](#running-the-project-locally)
- [Usage](#usage)
- [Contributing](#contributing)
- [License](#license)
- [Maintainers](#maintainers)

## Overview

SELISE Blocks Genesis is a Python package for bootstrapping production-oriented FastAPI services with consistent infrastructure patterns. It centralizes secret loading, tenant-aware context, authorization dependencies, cache/database providers, message bus clients, and telemetry setup.

This project is intended for platform teams and backend engineers building multi-tenant APIs and asynchronous worker services in the SELISE Blocks ecosystem.

Key use cases:

- Rapidly spin up a FastAPI service with shared middlewares and lifecycle wiring.
- Add tenant-aware authorization and context propagation across requests.
- Integrate Redis, MongoDB, and either Azure Service Bus or RabbitMQ with minimal boilerplate.
- Standardize tracing and log export behavior across services.

## Features

- **FastAPI bootstrap utilities** - Creates app instances with consistent startup/shutdown lifecycle handling.
- **Multi-tenant request middleware** - Resolves tenant from API key or domain and injects request context.
- **Authorization dependency** - Provides JWT authentication and permission checks with root-tenant access logic.
- **Tenant context switching** - Supports project-level context switching for shared-access scenarios.
- **Azure Key Vault secret loading** - Loads service secrets into a typed secret model at startup.
- **Redis cache provider** - Unified sync/async cache API with pub/sub and tracing metadata.
- **MongoDB context provider** - Tenant-aware database/collection resolution with connection caching.
- **Message bus abstraction** - Auto-resolves provider and supports Azure Service Bus or RabbitMQ.
- **Worker runtime** - Runs event consumers with managed service initialization and graceful shutdown.
- **Observability baseline** - OpenTelemetry tracing plus MongoDB log/trace exporters.
- **Project configuration loader** - Environment-based JSON config loading via APP_ENV.

## Architecture Overview

```text
+--------------------------------------------------------------+
|                    Client / Upstream Apps                    |
+--------------------------------------------------------------+
															|
															v
+--------------------------------------------------------------+
|                 FastAPI Service (api.py, /api)               |
|  Routes: / /health /sse /ping /swagger/index.html /openapi   |
+--------------------------------------------------------------+
								|                     |                    |
								v                     v                    v
+---------------------------+ +-------------------+ +--------------------+
| Tenant + Auth Pipeline    | | Message Client    | | Observability      |
| x-blocks-key + JWT checks | | Azure or RabbitMQ | | OTel + Mongo export|
+---------------------------+ +-------------------+ +--------------------+
								|                     |                    |
								v                     v                    v
+---------------------------+ +-------------------+ +--------------------+
| Redis Cache               | | Broker Infra      | | Mongo Logs/Traces  |
| CacheConnectionString     | | Service Bus/RMQ   | | Log/Trace DB        |
+---------------------------+ +-------------------+ +--------------------+
								|
								v
+--------------------------------------------------------------+
|           MongoDB Tenant Databases + Root Tenant DB          |
|         DatabaseConnectionString + RootDatabaseName          |
+--------------------------------------------------------------+
															|
															v
+--------------------------------------------------------------+
|                   Worker Service (worker.py)                 |
|        Event consumers via WorkerConsoleApp lifecycle        |
+--------------------------------------------------------------+
```

Internal package layout:

- `_core` contains app/worker bootstrapping, configuration loading, secret loading, and context switching.
- `_auth`, `_middlewares`, and `_tenant` implement context propagation and authorization policies.
- `_cache`, `_database`, and `_message` provide infrastructure adapters.
- `_lmt` implements logging, tracing, and telemetry exporters.

## API / Endpoints

Auth legend:

```text
Public   = No explicit authorize() dependency on the endpoint
Bearer   = Authorization: Bearer <jwt> supported by auth pipeline
API-Key  = x-blocks-key tenant key required by tenant middleware for tenant resolution
```

### API Application Router - /api/

#### Core

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/ | Public + API-Key middleware path | Sample root endpoint; publishes an AiMessage to ai_queue and returns status payload. |

#### Health

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/health | Public + API-Key middleware path | Health endpoint with authorize bypass enabled. |
| GET | /api/ping | Public + API-Key middleware path | Internal health check endpoint added by shared middleware setup. |

#### Streaming

| Method | Path | Auth | Description |
|---|---|---|---|
| POST | /api/sse | Public + API-Key middleware path | Server-sent events stream endpoint that emits five message chunks. |

#### Documentation

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/swagger/index.html | Public + API-Key middleware path | Swagger UI endpoint (enabled when show_docs is true). |
| GET | /api/openapi.json | Public + API-Key middleware path | OpenAPI schema endpoint (enabled when show_docs is true). |

### Shared Middleware Router - /api/

This router is injected by the shared `configure_middlewares` helper and contributes the following endpoints to any app that uses it.

#### Health and Metadata

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | /api/ping | Public + API-Key middleware path | Returns `{"status": "healthy", "message": "pong"}`. |
| GET | /api/swagger/index.html | Public + API-Key middleware path | Returns Swagger UI HTML or `NOT_ALLOWED` depending on docs visibility. |
| GET | /api/openapi.json | Public + API-Key middleware path | Returns OpenAPI JSON or empty object depending on docs visibility. |

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | CPython 3.9+ |
| Language | Python |
| AI/ML Framework | Framework-agnostic (no direct PyTorch/TensorFlow/LangChain dependency) |
| Vector Store | Not bundled in this repository |
| Database(s) | MongoDB (`pymongo`, `motor`) |
| Cache | Redis (`redis`, `redis.asyncio`) |
| Message Broker | Azure Service Bus (`azure-servicebus`) and RabbitMQ (`aio-pika`) |
| Observability | OpenTelemetry API/SDK + custom MongoDB log/trace exporters |
| Secret Management | Azure Key Vault (`azure-identity`, `azure-keyvault-secrets`) + dotenv bootstrap |
| Auth Standard | JWT Bearer + tenant API key (`x-blocks-key`) |
| API Docs | FastAPI OpenAPI + Swagger UI |

## Prerequisites

| Tool | Minimum Version | Notes |
|---|---|---|
| Python | 3.9 | Required by `pyproject.toml`. |
| pip | 23+ | Dependency installation for pip workflow. |
| uv (optional) | Latest | Faster package installation workflow. |
| Poetry (optional) | 1.6+ | Optional environment/dependency management. |
| Redis | 6+ | Required for cache provider and tenant update pub/sub. |
| MongoDB | 5+ | Required for tenant lookup, app data access, logs, and trace exports. |
| Azure Key Vault access | N/A | Required in current implementation to load runtime secrets. |
| Message broker | N/A | Azure Service Bus namespace or RabbitMQ instance depending on configuration. |
| Docker (optional) | 24+ | No Dockerfile currently committed; optional for containerized local runs. |

## Installation

### 1) Clone

```bash
git clone https://github.com/SELISEdigitalplatforms/blocks-genesis-py.git
cd blocks-genesis-py
```

### 2) Create Virtual Environment

```bash
python -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3) Install Dependencies (pip)

```bash
pip install --upgrade pip
pip install -e .
```

### 4) Install Dependencies (uv)

```bash
pip install uv
uv pip install -e .
```

### 5) Install Dependencies (Poetry, optional)

```bash
pip install poetry
poetry install
poetry shell
```

## Configuration - Environment Variables

This project supports two operational patterns for configuration bootstrap, and only one should be active for runtime secret ownership at a time.

- Option A: local `.env` centric setup for local development.
- Option B: cloud secret manager centric setup (Azure Key Vault) for staging/production.

In this repository, `python-dotenv` is used to load local environment values, and secret loading is implemented through Azure Key Vault in the startup lifecycle.

### Option A - Local .env file (Development)

Create a `.env` file in the project root.

```env
# Cache
CACHE_CONNECTION_STRING=redis://localhost:6379
CacheConnectionString=localhost:6379

# Message Broker
MESSAGE_BROKER_URL=amqp://guest:guest@localhost:5672/
MessageConnectionString=amqp://guest:guest@localhost:5672/

# Observability
LogConnectionString=mongodb://localhost:27017
MetricConnectionString=mongodb://localhost:27017
TraceConnectionString=mongodb://localhost:27017
LogDatabaseName=blocks_logs
MetricDatabaseName=blocks_metrics
TraceDatabaseName=blocks_traces
ServiceName=blocks_ai_api

# Database
DATABASE_URL=mongodb://localhost:27017
DatabaseConnectionString=mongodb://localhost:27017
RootDatabaseName=blocks_root
APP_ENV=dev

# Vector Store
VECTOR_STORE_URL=http://localhost:6333
VECTOR_STORE_API_KEY=

# Model Config
MODEL_PROVIDER=openai
MODEL_NAME=gpt-4o
MODEL_API_KEY=sk-...
EMBEDDING_MODEL=text-embedding-3-small

# Security
SECRET_KEY=change-me
ENABLE_HTTPS=false

# Azure Key Vault bootstrap (required by current secret loader implementation)
KEYVAULT__CLIENTID=
KEYVAULT__CLIENTSECRET=
KEYVAULT__KEYVAULTURL=https://your-vault-name.vault.azure.net/
KEYVAULT__TENANTID=
```

### Option B - Cloud Secret Manager (Production / Staging)

The service loads secrets through Azure Key Vault using `KEYVAULT__CLIENTID`, `KEYVAULT__CLIENTSECRET`, `KEYVAULT__KEYVAULTURL`, and `KEYVAULT__TENANTID` to authenticate and retrieve secret values.

Flat secret names used by the application:

```text
CacheConnectionString
MessageConnectionString
LogConnectionString
MetricConnectionString
TraceConnectionString
LogDatabaseName
MetricDatabaseName
TraceDatabaseName
ServiceName
DatabaseConnectionString
RootDatabaseName
```

No code changes are required to switch approaches; switching is environment-driven. In practice for this codebase, ensure local bootstrap variables are present when using Key Vault-backed mode.

### Variable Reference

| Variable | Purpose |
|---|---|
| CACHE_CONNECTION_STRING | Redis connection string for response and session caching. |
| DATABASE_URL | Primary relational or document database connection string. |
| VECTOR_STORE_URL | Vector database endpoint for embedding storage and retrieval. |
| VECTOR_STORE_API_KEY | API key for the vector store (if required). |
| MODEL_PROVIDER | AI model backend: openai, huggingface, ollama, etc. |
| MODEL_NAME | Model identifier or checkpoint name. |
| MODEL_API_KEY | API key for the model provider. |
| EMBEDDING_MODEL | Embedding model name used for vectorization. |
| MESSAGE_BROKER_URL | Message broker endpoint for async task publishing. |
| SECRET_KEY | Application secret used for token signing and encryption. |
| ENABLE_HTTPS | Enables HTTPS/HSTS - true in production, false locally. |
| APP_ENV | Selects config file under `config/<APP_ENV>.json` (default: `dev`). |
| KEYVAULT__CLIENTID | Azure AD app/client ID used for Key Vault authentication. |
| KEYVAULT__CLIENTSECRET | Azure AD app client secret used for Key Vault authentication. |
| KEYVAULT__KEYVAULTURL | Azure Key Vault URL used to resolve runtime secrets. |
| KEYVAULT__TENANTID | Azure AD tenant ID used for Key Vault authentication. |
| CacheConnectionString | Runtime Redis connection string loaded into `BlocksSecret`. |
| MessageConnectionString | Runtime broker connection string used by Azure Service Bus or RabbitMQ clients. |
| LogConnectionString | MongoDB connection string used by log exporter. |
| MetricConnectionString | Reserved metric exporter connection string in `BlocksSecret`. |
| TraceConnectionString | MongoDB connection string used by trace exporter. |
| LogDatabaseName | MongoDB database name for logs. |
| MetricDatabaseName | Reserved metric database name in `BlocksSecret`. |
| TraceDatabaseName | MongoDB database name for traces. |
| ServiceName | Service identifier used in telemetry resources and collection naming. |
| DatabaseConnectionString | Root tenant metadata database connection string. |
| RootDatabaseName | Root database name containing tenant and authorization metadata. |

## Running the Project Locally

### Step 1 - Set environment variables

Bash (Linux/macOS):

```bash
export APP_ENV=dev
export KEYVAULT__CLIENTID="<client-id>"
export KEYVAULT__CLIENTSECRET="<client-secret>"
export KEYVAULT__KEYVAULTURL="https://<vault-name>.vault.azure.net/"
export KEYVAULT__TENANTID="<tenant-id>"
```

PowerShell (Windows):

```powershell
$env:APP_ENV="dev"
$env:KEYVAULT__CLIENTID="<client-id>"
$env:KEYVAULT__CLIENTSECRET="<client-secret>"
$env:KEYVAULT__KEYVAULTURL="https://<vault-name>.vault.azure.net/"
$env:KEYVAULT__TENANTID="<tenant-id>"
```

### Step 2 - Run services in separate terminals

Terminal 1 (API service):

```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

Terminal 2 (Worker service):

```bash
python worker.py
```

Default local URLs:

- API base: `http://localhost:8000/api`
- Health: `http://localhost:8000/api/health`
- Ping: `http://localhost:8000/api/ping`
- Swagger UI: `http://localhost:8000/api/docs` and `http://localhost:8000/api/swagger/index.html`
- OpenAPI JSON: `http://localhost:8000/api/openapi.json`

### Option 2 - Docker

No Dockerfile is currently committed in this repository. If you add one at the repository root, use:

```bash
docker build -t seliseblocks-genesis:local .
docker run --rm -p 8000:8000 \
	-e APP_ENV=dev \
	-e KEYVAULT__CLIENTID=<client-id> \
	-e KEYVAULT__CLIENTSECRET=<client-secret> \
	-e KEYVAULT__KEYVAULTURL=https://<vault-name>.vault.azure.net/ \
	-e KEYVAULT__TENANTID=<tenant-id> \
	seliseblocks-genesis:local
```

Or with an env file:

```bash
docker run --rm -p 8000:8000 --env-file .env seliseblocks-genesis:local
```

## Usage

| Surface | Local URL |
|---|---|
| API Base | `http://localhost:8000/api` |
| Swagger UI | `http://localhost:8000/api/docs` or `http://localhost:8000/api/swagger/index.html` |
| OpenAPI JSON | `http://localhost:8000/api/openapi.json` |
| Health Check | `http://localhost:8000/api/health` |
| Ping | `http://localhost:8000/api/ping` |
| Model Info | Not exposed in current API |

Refer to the interactive API docs (`/docs`) for full request/response schemas, required fields, and live endpoint testing.

## Contributing

Contributions are welcome. Please follow these steps:

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes using [Conventional Commits](https://www.conventionalcommits.org/)
4. Push your branch and open a Pull Request against `dev`
5. Ensure all tests pass before submitting: `pytest`

Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before submitting a PR.

---

## License

This project is licensed under the terms of the [MIT License](LICENSE).

---

## Maintainers

For questions, issues, or security concerns, please open a [GitHub Issue](https://github.com/SELISEdigitalplatforms/blocks-genesis-py/issues) or review [SECURITY.md](SECURITY.md) for responsible disclosure guidelines.
