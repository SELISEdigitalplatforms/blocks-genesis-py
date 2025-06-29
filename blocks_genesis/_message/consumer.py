import json
from blocks_genesis._message.event_registry import EventRegistry


class Consumer:
    async def process_message(self, type: str, body: dict):
        handler = EventRegistry.resolve(type)

        if callable(handler):  # If it’s a function
            await handler(json.loads(body))
        elif hasattr(handler, "handle"):  # If it's a class with `handle`
            instance = handler()
            await instance.handle(json.loads(body))
        else:
            raise TypeError(f"Handler for type '{type}' is not callable or doesn't implement `handle()`")
