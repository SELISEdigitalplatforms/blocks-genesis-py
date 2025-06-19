# event_registry.py

_event_handlers = {}

def register_event_handler(event_type: str):
    def wrapper(cls_or_func):
        _event_handlers[event_type] = cls_or_func
        return cls_or_func
    return wrapper

def get_event_handler(event_type: str):
    handler = _event_handlers.get(event_type)
    if not handler:
        raise ValueError(f"No handler registered for event type: {event_type}")
    return handler
