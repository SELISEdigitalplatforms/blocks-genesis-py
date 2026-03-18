from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from blocks_lmt.transport_helper import is_rabbitmq
from blocks_lmt.azure_servicebus_trace_exporter import AzureServiceBusTraceExporter
from blocks_lmt.rabbitmq_trace_exporter import RabbitMqTraceExporter


def configure_tracing(
    x_blocks_key: str,
    blocks_service_id: str,
    connection_string: str,
    batch_size: int = 1000,
    flush_interval: float = 5.0,
    max_retries: int = 3,
    max_failed_batches: int = 100
) -> None:
    """
    Configures OpenTelemetry tracing to send spans to Azure Service Bus or RabbitMQ.
    Transport is auto-detected from the connection string:
    - amqp:// or amqps:// → RabbitMQ
    - Otherwise → Azure Service Bus

    Args:
        x_blocks_key: Tenant ID for trace isolation
        blocks_service_id: Service identifier used for SERVICE_NAME resource attribute
        connection_string: Azure Service Bus or RabbitMQ connection string
        batch_size: Number of traces to batch before sending (default: 1000)
        flush_interval: Interval in seconds to flush traces (default: 5.0)
        max_retries: Maximum number of retries for failed batches (default: 3)
        max_failed_batches: Maximum number of failed batches to queue (default: 100)
    """
    trace.set_tracer_provider(
        TracerProvider(
            resource=Resource.create({SERVICE_NAME: blocks_service_id})
        )
    )

    use_rabbitmq = is_rabbitmq(connection_string)

    if use_rabbitmq:
        exporter = RabbitMqTraceExporter(
            x_blocks_key=x_blocks_key,
            service_name=blocks_service_id,
            connection_string=connection_string,
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )
    else:
        exporter = AzureServiceBusTraceExporter(
            x_blocks_key=x_blocks_key,
            service_name=blocks_service_id,
            connection_string=connection_string,
            batch_size=batch_size,
            flush_interval=flush_interval,
            max_retries=max_retries,
            max_failed_batches=max_failed_batches
        )

    processor = BatchSpanProcessor(exporter)
    trace.get_tracer_provider().add_span_processor(processor)

    transport_name = "RabbitMQ" if use_rabbitmq else "Azure Service Bus"
    target_name = f"Exchange: lmt-{blocks_service_id}" if use_rabbitmq else f"Topic: lmt-{blocks_service_id}"
    print(f"Tracing configured with {transport_name} exporter ({target_name})")
    print(f"FastAPI instrumentation enabled - all HTTP requests will be traced")
