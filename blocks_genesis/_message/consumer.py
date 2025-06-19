from blocks_genesis._message.event_registry import get_event_handler

class Consumer:
    async def process_message(self, type: str, body: dict):
        handler = get_event_handler(type)

        if callable(handler):  # If it’s a function
            await handler(body)
        elif hasattr(handler, "handle"):  # If it's a class with `handle`
            instance = handler()
            await instance.handle(body)
        else:
            raise TypeError(f"Handler for type '{type}' is not callable or doesn't implement `handle()`")
