import logging

from blocks_genesis._lmt.mongo_log_exporter import MongoHandler, TraceContextFilter


def configure_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(TenantId)s] [%(TraceId)s] %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    mongo_handler = MongoHandler()

    # Add context filter to enrich log records
    context_filter = TraceContextFilter()
    console_handler.addFilter(context_filter)
    mongo_handler.addFilter(context_filter)

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(mongo_handler)
