from api import AiMessage


async def handle_user_created_event(event_data):
    ai_message = AiMessage(**event_data)
    print(f"Handling user created event: {ai_message.message}")
