from opentelemetry import trace, baggage
from opentelemetry.trace import (
    get_current_span,
    Span,
    Status,
    StatusCode
)
from opentelemetry.context import get_current, attach, detach, set_value


_tracer = trace.get_tracer("blocks.activity")


class Activity:
    def __init__(self, name: str):
        self._context = get_current()
        self._span = _tracer.start_span(name, context=self._context)
        self._token = attach(trace.set_span_in_context(self._span))

    def set_property(self, key: str, value):
        """Set a single attribute/tag on the span."""
        if self._span.is_recording():
            self._span.set_attribute(key, value)

    def set_properties(self, props: dict):
        """Set multiple attributes/tags on the span."""
        if self._span.is_recording():
            for k, v in props.items():
                self._span.set_attribute(k, v)

    def add_event(self, name: str, attributes: dict = None):
        """Add a single event to the span."""
        self._span.add_event(name, attributes or {})

    def add_events(self, events: list[dict]):
        """
        Add multiple events.
        Each event should be a dict with keys:
          - name (str)
          - attributes (dict, optional)
        """
        for event in events:
            name = event.get("name")
            attrs = event.get("attributes", {})
            if name:
                self.add_event(name, attrs)

    def set_status(self, status_code: StatusCode, description: str = ""):
        """Set span status."""
        self._span.set_status(Status(status_code, description))

    def set_baggage(self, key: str, value: str):
        """Set a baggage item in the current context."""
        ctx = baggage.set_baggage(key, value, context=get_current())
        set_value("context", ctx)

    def set_baggage_items(self, items: dict):
        """Set multiple baggage items at once."""
        ctx = get_current()
        for k, v in items.items():
            ctx = baggage.set_baggage(k, v, context=ctx)
        set_value("context", ctx)

    def get_baggage(self, key: str):
        """Get a baggage item by key."""
        return baggage.get_baggage(key, context=get_current())

    def get_all_baggage(self):
        """Return all baggage items as a dict."""
        # OpenTelemetry Python doesn't provide direct API to get all baggage keys,
        # but we can implement if needed by iterating context
        # For now, return empty or None
        return None

    def stop(self):
        """End the span and detach the context token."""
        self._span.end()
        detach(self._token)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    @staticmethod
    def current() -> Span:
        """Get the current active span."""
        return get_current_span()

    @staticmethod
    def set_current_property(key: str, value):
        span = Activity.current()
        if span and span.is_recording():
            span.set_attribute(key, value)

    @staticmethod
    def start(name: str) -> "Activity":
        return Activity(name)

    @staticmethod
    def get_trace_id() -> str:
        span = Activity.current()
        if span:
            return format(span.get_span_context().trace_id, "032x")
        return ""

    @staticmethod
    def get_span_id() -> str:
        span = Activity.current()
        if span:
            return format(span.get_span_context().span_id, "016x")
        return ""
