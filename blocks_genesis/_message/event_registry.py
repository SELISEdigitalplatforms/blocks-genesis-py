from typing import Callable, Any, Type, Dict, Union

class EventRegistry:
    """
    Registry for event handlers, allowing registration and retrieval by event type.
    """
    _handlers: Dict[str, Union[Callable[..., Any], Type[Any]]] = {}

    @classmethod
    def register(cls, event_type: str):
        """
        Decorator to register a handler for a given event type.
        """
        if not isinstance(event_type, str) or not event_type:
            raise ValueError("event_type must be a non-empty string.")

        def decorator(handler: Union[Callable[..., Any], Type[Any]]):
            if event_type in cls._handlers:
                raise KeyError(f"Handler already registered for event type: {event_type}")
            cls._handlers[event_type] = handler
            return handler
        return decorator

    @classmethod
    def resolve(cls, event_type: str) -> Union[Callable[..., Any], Type[Any]]:
        """
        Retrieve the handler registered for the given event type.
        """
        if event_type not in cls._handlers:
            raise ValueError(f"No handler registered for event type: {event_type}")
        return cls._handlers[event_type]

