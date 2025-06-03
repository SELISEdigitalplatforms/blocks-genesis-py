# tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from blocks_genesis.lmt.mongo_trace_exporter import MongoDBTraceExporter

def enable_tracing(app, service_name: str = "blocks-api", mongo_uri: str = "", db_name: str = "tracing"):
    trace.set_tracer_provider(
        TracerProvider(
            resource=Resource.create({SERVICE_NAME: service_name})
        )
    )

    exporter = MongoDBTraceExporter(
        service_name=service_name,
        mongo_uri=mongo_uri,
        database_name=db_name
    )

    processor = BatchSpanProcessor(exporter)
    trace.get_tracer_provider().add_span_processor(processor)

    FastAPIInstrumentor.instrument_app(app)
