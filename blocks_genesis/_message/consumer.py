import inspect
import json
from blocks_genesis._message.event_registry import EventRegistry


class Consumer:
    async def process_message(self, type: str, body: dict):
        handler = EventRegistry.resolve(type)
        data = json.loads(body)

        # A class is callable too, so it is checked first: it is built with no arguments and its
        # `handle` receives the data.
        if inspect.isclass(handler):
            if not hasattr(handler, "handle"):
                raise TypeError(f"Handler class for type '{type}' doesn't implement `handle()`")
            await handler().handle(data)
        elif callable(handler):
            await handler(data)
        else:
            raise TypeError(f"Handler for type '{type}' is not callable or doesn't implement `handle()`")
