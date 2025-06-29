from api import AiMessage
from blocks_genesis._message.event_registry import register_consumer

@register_consumer("AiMessage")
async def handle_user_created_event(event_data: AiMessage):
    print(f"Handling user created event: {event_data}")
