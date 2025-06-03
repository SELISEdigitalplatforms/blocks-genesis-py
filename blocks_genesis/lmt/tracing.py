# tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from blocks_genesis.lmt.mongo_trace_exporter import MongoDBTraceExporter
from blocks_genesis.core.blocks_secret import blocks_secret_instance

def enable_tracing(app):
    trace.set_tracer_provider(
        TracerProvider(
            resource=Resource.create({SERVICE_NAME: blocks_secret_instance.ServiceName})
        )
    )

    exporter = MongoDBTraceExporter()

    processor = BatchSpanProcessor(exporter)
    trace.get_tracer_provider().add_span_processor(processor)

    FastAPIInstrumentor.instrument_app(app)
