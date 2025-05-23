import structlog
import logging
from logging import StreamHandler
from mongo_dynamic_sink import MongoDBDynamicSink

sink = MongoDBDynamicSink(
    service_name="my-fastapi-service",
    mongo_uri="mongodb://localhost:27017",
    db_name="logs_db",
    batch_size=10,
    flush_interval=5
)

class MongoSinkProcessor:
    def __init__(self, sink: MongoDBDynamicSink):
        self.sink = sink

    def __call__(self, logger, method_name, event_dict):
        doc = self.sink.create_log_document(event_dict)
        asyncio.create_task(self.sink.emit(doc))
        return event_dict

def configure_logger():
    logging.basicConfig(handlers=[StreamHandler()], level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            MongoSinkProcessor(sink),
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
