import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
import logging
from typing import Any, Dict, Type, Union

from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._cache.redis_client import RedisClient
from blocks_genesis._core.secret_loader import SecretLoader, get_blocks_secret
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._database.mongo_context import MongoDbContextProvider
from blocks_genesis._message.azure.azure_message_client import AzureMessageClient
from blocks_genesis._message.azure.azure_message_worker import AzureMessageWorker
from blocks_genesis._message.azure.config_azure_service_bus import ConfigAzureServiceBus
from blocks_genesis._message.event_registry import EventRegistry
from blocks_genesis._message.message_configuration import MessageConfiguration
from blocks_genesis._lmt.log_config import configure_logger
from blocks_genesis._lmt.mongo_log_exporter import MongoHandler
from blocks_genesis._lmt.tracing import configure_tracing
from blocks_genesis._tenant.tenant_service import initialize_tenant_service


class WorkerConsoleApp:
    def __init__(self, name: str, message_config: MessageConfiguration, register_consumer: Dict[str, Union[Callable[..., Any], Type[Any]]] = None):
        """
        Initializes the WorkerConsoleApp.

        Args:
            name (str): The name of the worker application.
            message_config (MessageConfiguration): Configuration for message bus connectivity.
            register_consumer (Dict[str, Union[Callable[..., Any], Type[Any]]], optional):
                A dictionary mapping event types (strings) to their corresponding handlers.
                Handlers can be callables or classes with a 'handle' method.
                Defaults to an empty dictionary if not provided.
        """
        self.message_worker: AzureMessageWorker = None
        self.name = name
        self.logger = logging.getLogger(__name__)
        self.message_config = message_config
        self.register_consumer = register_consumer if register_consumer is not None else {}

    @asynccontextmanager
    async def setup_services(self):
        """
        An asynchronous context manager to set up all necessary services for the worker.
        It handles initialization of secrets, logging, tracing, caching, database context,
        message bus configuration, and registers event handlers.
        """
        self.logger.info("🚀 Starting Blocks AI Worker Console App...")

        try:
            self.logger.info("🔐 Loading secrets...")
            await SecretLoader(self.name).load_secrets()
            self.logger.info("✅ Secrets loaded successfully")
            
            configure_logger()
            self.logger.info("📝 Logger configured")

            configure_tracing()
            self.logger.info("🔍 Tracing configured")

            CacheProvider.set_client(RedisClient())
            await initialize_tenant_service()
            DbContext.set_provider(MongoDbContextProvider())
            self.logger.info("✅ Cache, TenantService, and Mongo Context initialized")

            for event_type, handler in self.register_consumer.items():
                if isinstance(event_type, str) and event_type and (callable(handler) or hasattr(handler, "handle")):
                    if event_type not in EventRegistry._handlers:
                        EventRegistry.register(event_type)(handler)
                        self.logger.info(f"Handler registered for event type: {event_type}")
                    else:
                        self.logger.error(f"Handler already registered for event type: {event_type}")
                else:
                    self.logger.error(f"Invalid event_type or handler for: {event_type} (Expected non-empty string event_type and callable/handle-method handler).")

            
            self.message_config.connection = self.message_config.connection or get_blocks_secret().MessageConnectionString
            ConfigAzureServiceBus().configure_queue_and_topic(self.message_config)
            AzureMessageClient.initialize(self.message_config)

            self.message_worker = AzureMessageWorker(self.message_config)
            self.message_worker.initialize()

            self.logger.info("✅ Azure Message Worker initialized and ready")
            yield self.message_worker

        except Exception as ex:
            self.logger.error(f"❌ Startup failed: {ex}", exc_info=True)
            raise 

        finally:
            await self.cleanup()

    async def cleanup(self):
        """
        Performs asynchronous cleanup operations before the application exits.
        This includes stopping the message worker and potentially the Mongo logger.
        """
        self.logger.info("🛑 Cleaning up services...")

        if self.message_worker:
            self.logger.info("Stopping Azure Message Worker...")
            await self.message_worker.stop()
            self.logger.info("Azure Message Worker stopped.")

        if hasattr(MongoHandler, '_mongo_logger') and MongoHandler._mongo_logger:
            self.logger.info("Stopping Mongo log exporter...")
            MongoHandler._mongo_logger.stop()
            self.logger.info("Mongo log exporter stopped.")

        self.logger.info("✅ Shutdown complete")

    async def run(self, callback: Union[Callable[..., Any], Type[Any]]):
        """
        Runs the worker application using the setup_services context manager.
        It handles graceful shutdown on cancellation or keyboard interrupt.
        """
        async with self.setup_services() as worker:
            self.logger.info("🔄 Worker running... Press Ctrl+C to stop")
            if callback and callable(callback):
                await callback()
            try:
                await worker.run() 
            except asyncio.CancelledError:
                self.logger.info("🛑 Received cancellation signal (e.g., from task.cancel()).")
            except KeyboardInterrupt:
                self.logger.info("⏹️ Received interrupt signal (Ctrl+C). Initiating graceful shutdown.")
            finally:
                self.logger.info("Worker run loop exited.")

