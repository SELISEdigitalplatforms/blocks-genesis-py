from typing import Mapping
from opentelemetry import trace, baggage
from opentelemetry.trace import (
    get_current_span,
    Span,
    Status,
    StatusCode
)
from opentelemetry.context import get_current, attach, detach

_tracer = trace.get_tracer("blocks.activity")


class Activity:
    def __init__(self, name: str):
        self._context = get_current()
        self._span = _tracer.start_span(name, context=self._context)
        self._token = attach(trace.set_span_in_context(self._span, self._context))

    def set_property(self, key: str, value):
        if self._span.is_recording():
            self._span.set_attribute(key, value)

    def set_properties(self, props: dict):
        if self._span.is_recording():
            for k, v in props.items():
                self._span.set_attribute(k, v)

    def add_event(self, name: str, attributes: dict = None):
        if self._span.is_recording():
            self._span.add_event(name, attributes or {})

    def add_events(self, events: list[dict]):
        for event in events:
            name = event.get("name")
            attrs = event.get("attributes", {})
            if name:
                self.add_event(name, attrs)

    def set_status(self, status_code: StatusCode, description: str = ""):
        self._span.set_status(Status(status_code, description))

    def set_baggage(self, key: str, value: str):
        self._context = baggage.set_baggage(key, value, context=self._context)

    def set_baggage_items(self, items: dict):
        for k, v in items.items():
            self._context = baggage.set_baggage(k, v, context=self._context)

    def get_baggage(self, key: str):
        return baggage.get_baggage(key, context=self._context)

    def stop(self):
        self._span.end()
        detach(self._token)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self._span.record_exception(exc_val)
            self.set_status(StatusCode.ERROR, str(exc_val))
        self.stop()

    @staticmethod
    def current() -> Span:
        return get_current_span()

    @staticmethod
    def start(name: str) -> "Activity":
        return Activity(name)

    @staticmethod
    def get_trace_id() -> str:
        span = Activity.current()
        return format(span.get_span_context().trace_id, "032x") if span else ""

    @staticmethod
    def get_span_id() -> str:
        span = Activity.current()
        return format(span.get_span_context().span_id, "016x") if span else ""


    @staticmethod
    def set_current_property(key: str, value):
        span = Activity.current()
        if span and span.is_recording():
            span.set_attribute(key, value)

    @staticmethod
    def set_current_properties(props: dict):
        span = Activity.current()
        if span and span.is_recording():
            for k, v in props.items():
                span.set_attribute(k, v)

    @staticmethod
    def add_current_event(name: str, attributes: dict = None):
        span = Activity.current()
        if span and span.is_recording():
            span.add_event(name, attributes or {})

    @staticmethod
    def add_current_events(events: list[dict]):
        span = Activity.current()
        if span and span.is_recording():
            for event in events:
                name = event.get("name")
                attrs = event.get("attributes", {})
                if name:
                    span.add_event(name, attrs)

    @staticmethod
    def set_baggage_item(key: str, value: str):
        ctx = get_current()
        n_ctx = baggage.set_baggage(key, value, context=ctx)
        attach(n_ctx)

    @staticmethod
    def set_baggage_items_global(items: dict):
        ctx = get_current()
        for k, v in items.items():
            n_ctx = baggage.set_baggage(k, v, context=ctx)
        attach(n_ctx)
        
    @staticmethod
    def get_baggage_item(key: str) -> str | None:
        return baggage.get_baggage(key, context=get_current())
    
    @staticmethod
    def get_baggages() -> Mapping[str, object]:
        return baggage.get_all(context=get_current())
