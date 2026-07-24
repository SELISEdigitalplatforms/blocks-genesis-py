# Blocks Genesis

> Reusable FastAPI building blocks for multi-tenant services: tenant resolution, auth context, Redis cache, MongoDB access, Azure Service Bus / RabbitMQ messaging, and OpenTelemetry-based observability.

![PyPI](https://img.shields.io/pypi/v/blocks-genesis)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quickstart](#quickstart)
- [Public API](#public-api)
- [Configuration](#configuration)
- [Endpoints and Middleware Added by configure_genesis](#endpoints-and-middleware-added-by-configure_genesis)
- [Sample Application](#sample-application)
- [Versioning and Compatibility](#versioning-and-compatibility)
- [Testing](#testing)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [Maintainers](#maintainers)

## Overview

`blocks-genesis` is the shared Python foundation of the SELISE Blocks platform. It bootstraps production FastAPI services and background workers with consistent infrastructure wiring: secret loading from Azure Key Vault, tenant-aware request context, JWT authorization dependencies, Redis cache and MongoDB providers, message bus clients for Azure Service Bus and RabbitMQ, and OpenTelemetry tracing with MongoDB log/trace exporters.

It is consumed by the SELISE Blocks service repositories, so its exported names and signatures form a stable contract. See [Versioning and Compatibility](#versioning-and-compatibility).

Key use cases:

- Spin up a FastAPI service with shared middlewares and lifecycle wiring.
- Add tenant-aware authorization and context propagation across requests.
- Integrate Redis, MongoDB, and either Azure Service Bus or RabbitMQ with minimal boilerplate.
- Standardize tracing and log export behavior across services.

## Features

- **FastAPI bootstrap utilities**: `fast_api_app`, `configure_lifespan`, `configure_genesis`, and `close_lifespan` wire the full service lifecycle.
- **Multi-tenant request middleware**: resolves the tenant from the `x-blocks-key` header (or query parameter) or the request domain and injects request context.
- **Authorization dependency**: `authorize()` provides JWT authentication (tenant certificate, JWKS, or third-party public certificate) plus role and permission checks.
- **Azure Key Vault secret loading**: loads service secrets into the typed `BlocksSecret` model at startup using `DefaultAzureCredential`.
- **Redis cache provider**: unified sync/async cache API with pub/sub and tracing metadata.
- **MongoDB context provider**: tenant-aware database and collection resolution with connection caching.
- **Message bus abstraction**: auto-detects the provider from the connection string (`amqp://`/`amqps://` selects RabbitMQ, anything else Azure Service Bus).
- **Worker runtime**: `WorkerConsoleApp` runs event consumers with managed service initialization and graceful shutdown.
- **Observability baseline**: OpenTelemetry tracing plus MongoDB log and trace exporters.
- **Project configuration loader**: environment-keyed JSON config loading via `APP_ENV`.

## Requirements

| Dependency | Notes |
|---|---|
| Python 3.12+ | Declared in `pyproject.toml` (`requires-python = ">=3.12"`). |
| Redis | Cache provider and tenant update pub/sub. |
| MongoDB | Tenant lookup, application data, log and trace export. |
| Azure Key Vault | Runtime secret source; required by the current secret loader. |
| Message broker | Azure Service Bus namespace or RabbitMQ instance, depending on configuration. |

The backing services are needed at service startup (`configure_lifespan` / `WorkerConsoleApp`), not at import time.

## Installation

From PyPI:

```bash
pip install blocks-genesis
```

With uv:

```bash
uv add blocks-genesis
```

From source:

```bash
git clone https://github.com/SELISEdigitalplatforms/blocks-genesis-py.git
cd blocks-genesis-py
uv sync
```

## Quickstart

### API service

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from blocks_genesis import (
    AzureServiceBusConfiguration,
    MessageConfiguration,
    authorize,
    close_lifespan,
    configure_genesis,
    configure_lifespan,
    fast_api_app,
)

message_config = MessageConfiguration(
    azure_service_bus_configuration=AzureServiceBusConfiguration(
        queues=["demo_queue"],
        topics=[],
    )
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_lifespan("my_service", message_config)
    yield
    await close_lifespan()


app = fast_api_app(lifespan=lifespan)
configure_genesis(app, show_docs=True)


@app.get("/api/health", dependencies=[authorize(bypass_authorization=True)])
async def health():
    return {"status": "healthy"}
```

`configure_lifespan` loads secrets from Azure Key Vault, configures logging and tracing, initializes the Redis cache, tenant service, and MongoDB provider, and initializes the message client for the configured broker. It runs at application startup, so the backing services and the Key Vault environment variables described under [Configuration](#configuration) must be reachable when the server starts.

### Worker service

```python
from blocks_genesis import (
    ConsumerSubscription,
    MessageConfiguration,
    RabbitMqConfiguration,
    WorkerConsoleApp,
)


async def handle_demo_event(event_data):
    print("received:", event_data)


message_config = MessageConfiguration(
    rabbit_mq_configuration=RabbitMqConfiguration(
        consumer_subscriptions=[ConsumerSubscription.bind_to_queue("demo_queue")]
    )
)

app = WorkerConsoleApp("my_worker", message_config, {"DemoEvent": handle_demo_event})
```

Start the consume loop with `asyncio.run(app.run(callback))`, where `callback` is an async function invoked once the worker is ready. The mapping passed as the third argument registers one handler per payload type.

### Publishing a message

```python
from blocks_genesis import AzureMessageClient, ConsumerMessage

async def publish_demo():
    client = AzureMessageClient.get_instance()
    await client.send_to_consumer_async(ConsumerMessage(
        consumer_name="demo_queue",
        payload={"message": "hello"},
        payload_type="DemoEvent",
    ))
```

`AzureMessageClient.get_instance()` (or `RabbitMessageClient.get_instance()`) is available after `configure_lifespan` has initialized the client for the configured broker.

## Public API

Everything below is importable from the top-level `blocks_genesis` package.

| Group | Exports |
|---|---|
| App and worker bootstrap | `fast_api_app`, `configure_lifespan`, `configure_genesis`, `close_lifespan`, `WorkerConsoleApp` |
| Configuration | `load_configurations`, `get_configurations` |
| Auth and context | `authorize`, `BlocksContext`, `BlocksContextManager` |
| Tenancy | `Tenant`, `TenantService`, `get_tenant_service` |
| Cache | `CacheClient`, `CacheProvider` |
| Database | `DbContext`, `BaseEntity` |
| Messaging | `MessageClient`, `AzureMessageClient`, `RabbitMessageClient`, `ConsumerMessage`, `MessageConfiguration`, `AzureServiceBusConfiguration`, `RabbitMqConfiguration`, `ConsumerSubscription` |
| Secrets | `AzureKeyVault` |
| Observability | `Activity` |
| Utilities | `CryptoService` |

Notes:

- `authorize(resource_name, bypass_authorization=False)` returns a FastAPI dependency. `resource_name` is required unless `bypass_authorization=True`; a missing name on a protected endpoint raises `ValueError` at route definition time.
- `MessageConfiguration.resolve_provider()` auto-selects RabbitMQ for `amqp://`/`amqps://` connection strings and Azure Service Bus otherwise, when neither sub-configuration is set explicitly.

## Configuration

### Application configuration files

`load_configurations(config_dir)` loads `<config_dir>/<APP_ENV>.json` into an in-process dictionary; `APP_ENV` defaults to `dev`. `get_configurations()` returns the loaded dictionary and raises `RuntimeError` if nothing was loaded.

```python
from blocks_genesis import load_configurations, get_configurations

load_configurations("config")  # loads config/<APP_ENV>.json, APP_ENV defaults to dev
settings = get_configurations()
```

### Secrets (Azure Key Vault)

At startup, the secret loader fetches the fields of the `BlocksSecret` model from Azure Key Vault. Two things must be in place:

1. **Vault URL**: the environment variable `KEYVAULT__KEYVAULTURL` must point at the vault (a local `.env` file is honored via `python-dotenv`).
2. **Credentials**: authentication uses `DefaultAzureCredential` from `azure-identity`. Locally that typically means `az login`; on servers, a managed identity; or the standard `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and `AZURE_CLIENT_SECRET` environment variables for a service principal.

Example `.env` for local development:

```env
APP_ENV=dev
KEYVAULT__KEYVAULTURL=https://your-vault-name.vault.azure.net/
```

Secret names resolved from the vault (the fields of `BlocksSecret`):

| Secret | Purpose |
|---|---|
| `CacheConnectionString` | Redis connection string used by the cache provider. |
| `MessageConnectionString` | Broker connection string used by the Azure Service Bus or RabbitMQ clients. |
| `LogConnectionString` | MongoDB connection string used by the log exporter. |
| `MetricConnectionString` | Reserved metric exporter connection string. |
| `TraceConnectionString` | MongoDB connection string used by the trace exporter. |
| `LogDatabaseName` | MongoDB database name for logs. |
| `MetricDatabaseName` | Reserved metric database name. |
| `TraceDatabaseName` | MongoDB database name for traces. |
| `ServiceName` | Service identifier; overwritten at load time with the name passed to `configure_lifespan` or `WorkerConsoleApp`. |
| `DatabaseConnectionString` | Root tenant metadata database connection string. |
| `RootDatabaseName` | Root database containing tenant and authorization metadata. |

## Endpoints and Middleware Added by configure_genesis

`configure_genesis(app, show_docs=False, serve_static=False, static_mount_path="/", static_dir="")` installs, in order: proxy header handling, gzip compression, tenant validation on paths under `/api`, a global exception handler, OpenTelemetry FastAPI instrumentation, and CORS. With `serve_static=True` it also mounts a static directory (default `./static` in the working directory) at `static_mount_path`.

It registers these routes on the app:

| Method | Path | Description |
|---|---|---|
| GET | `/ping` | Returns `{"status": "healthy", "message": "pong"}`. |
| GET | `/swagger/index.html` | Swagger UI when `show_docs=True`, otherwise the string `NOT_ALLOWED`. |
| GET | `/openapi.json` | OpenAPI schema when `show_docs=True`, otherwise an empty object. |

Tenant validation applies to requests whose path starts with one of the `included_paths` prefixes (default `/api`). The tenant is resolved from the `x-blocks-key` header, the `x-blocks-key` or `tenant_id` query parameter, or the request domain. Requests with an unknown or disabled tenant are rejected, as are requests whose `Origin`/`Referer` domain is not registered for the tenant.

## Sample Application

The repository root contains a small reference wiring (not part of the published package):

- `api.py`: a FastAPI service using `configure_lifespan`, `configure_genesis`, `authorize`, and the Azure message client, including a server-sent events endpoint.
- `worker.py` and `test_consumer.py`: a `WorkerConsoleApp` consuming the message published by `api.py`.
- `config/dev.json` and `static/`: the configuration file and static directory the sample loads.

Starting the samples requires the backing services from [Requirements](#requirements) plus the Key Vault environment variables, because `configure_lifespan` runs at startup.

## Versioning and Compatibility

- The package is versioned in `pyproject.toml` and published to PyPI as [`blocks-genesis`](https://pypi.org/project/blocks-genesis/).
- The public API is the set of names exported from the top-level `blocks_genesis` package (its `__all__`). This package is consumed by the SELISE Blocks service repositories, so any change to an exported name, signature, type, or default value is treated as a breaking change and is coordinated across consumers.
- While the version is below 1.0.0, consumers should pin an exact version; minor releases may still contain breaking changes.
- Python 3.12 or newer is required.

## Testing

From the repository root:

```bash
uv run pytest
```

Coverage:

```bash
uv run pytest --cov=blocks_genesis
```

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before submitting a pull request. Note the branch model described there: work lands on `inception` and is merged into `main` via pull request.

## Security

See [SECURITY.md](SECURITY.md) for the supported versions and the private disclosure process. Do not report vulnerabilities through public GitHub issues.

## License

This project is licensed under the terms of the [MIT License](LICENSE).

## Maintainers

For questions or issues, open a [GitHub Issue](https://github.com/SELISEdigitalplatforms/blocks-genesis-py/issues). For security concerns, follow [SECURITY.md](SECURITY.md).
