# tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from blocks_genesis._lmt.mongo_trace_exporter import MongoDBTraceExporter
from blocks_genesis._core.secret_loader import get_blocks_secret

def configure_tracing() -> None:
    """Configures OpenTelemetry tracing for the FastAPI application."""
    trace.set_tracer_provider(
        TracerProvider(
            resource=Resource.create({SERVICE_NAME: get_blocks_secret().ServiceName})
        )
    )

    exporter = MongoDBTraceExporter()

    processor = BatchSpanProcessor(exporter)
    trace.get_tracer_provider().add_span_processor(processor)

