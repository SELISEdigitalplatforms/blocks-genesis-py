import logging

from blocks_lmt.transport_helper import is_rabbitmq
from blocks_lmt.azure_servicebus_log_exporter import (
    AzureServiceBusHandler,
    TraceContextFilter
)
from blocks_lmt.rabbitmq_log_exporter import RabbitMqHandler


def configure_logger(
    x_blocks_key: str,
    blocks_service_id: str,
    connection_string: str,
    batch_size: int = 100,
    flush_interval_sec: float = 5.0,
    max_retries: int = 3,
    max_failed_batches: int = 100
):
    """
    Configure the logger to send logs to Azure Service Bus or RabbitMQ.
    Transport is auto-detected from the connection string:
    - amqp:// or amqps:// → RabbitMQ
    - Otherwise → Azure Service Bus

    Args:
        x_blocks_key: Tenant ID for log isolation
        blocks_service_id: Service identifier
        connection_string: Azure Service Bus or RabbitMQ connection string
        batch_size: Number of logs to batch before sending (default: 100)
        flush_interval_sec: Interval in seconds to flush logs (default: 5.0)
        max_retries: Maximum number of retries for failed batches (default: 3)
        max_failed_batches: Maximum number of failed batches to queue (default: 100)
    """
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(TenantId)s] [%(TraceId)s] %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    use_rabbitmq = is_rabbitmq(connection_string)

    if use_rabbitmq:
        message_handler = RabbitMqHandler(
            x_blocks_key=x_blocks_key,
            service_name=blocks_service_id,
            connection_string=connection_string,
            batch_size=batch_size,
            flush_interval_sec=flush_interval_sec,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )
    else:
        message_handler = AzureServiceBusHandler(
            x_blocks_key=x_blocks_key,
            service_name=blocks_service_id,
            connection_string=connection_string,
            batch_size=batch_size,
            flush_interval_sec=flush_interval_sec,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )

    context_filter = TraceContextFilter(x_blocks_key=x_blocks_key)
    console_handler.addFilter(context_filter)
    message_handler.addFilter(context_filter)

    logger.handlers.clear()
    logger.addHandler(console_handler)
    logger.addHandler(message_handler)

    transport_name = "RabbitMQ" if use_rabbitmq else "Azure Service Bus"
    target_name = f"Exchange: lmt-{blocks_service_id}" if use_rabbitmq else f"Topic: lmt-{blocks_service_id}"
    logger.info(f"Logger configured with {transport_name} handler ({target_name})")
