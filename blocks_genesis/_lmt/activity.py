from typing import Dict, Mapping, Optional
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
        self._parent_span = get_current_span()
        self._root_attributes = self._find_root_attributes(self._parent_span)

        self._span = _tracer.start_span(name, context=self._context)

        if self._span.is_recording() and self._root_attributes:
            for k, v in self._root_attributes.items():
                self._span.set_attribute(f"baggage.{k}", v)

        self._span_context = trace.set_span_in_context(self._span, self._context)
        self._token = attach(self._span_context)
        
    def _find_root_attributes(self, span: Optional[Span]) -> Dict[str, object]:
        """Find root attributes in the span or its parent."""
        if span is None or not hasattr(span, 'attributes'):
            return {}

        root_attrs = {}
        for k, v in span.attributes.items():
            if k.startswith("baggage."):
                root_attrs[k[8:]] = v

        if root_attrs:
            return root_attrs

        return dict(span.attributes)

    def get_root_attribute(self, key: str) -> Optional[object]:
        """Get a single attribute from the root span attributes."""
        if self._root_attributes and key in self._root_attributes:
            return self._root_attributes[key]
        return None

    def get_all_root_attributes(self) -> Dict[str, object]:
        """Get all attributes from the root span."""
        return self._root_attributes or {}

    def set_property(self, key: str, value):
        if self._span.is_recording():
            self._span.set_attribute(key, value)

    def set_properties(self, props: dict):
        if self._span.is_recording():
            for k, v in props.items():
                self._span.set_attribute(k, v)

    def set_status(self, status_code: StatusCode, description: str = ""):
        self._span.set_status(status_code, description)

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
    def set_current_status(status_code: StatusCode, description: str = ""):
        span = Activity.current()
        if span and span.is_recording():
            span.set_status(status_code, description)