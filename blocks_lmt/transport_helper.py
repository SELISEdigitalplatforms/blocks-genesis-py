from urllib.parse import urlparse


def is_rabbitmq(connection_string: str) -> bool:
    """
    Determine if the connection string is for RabbitMQ.
    RabbitMQ connection strings use amqp:// or amqps:// URI scheme.
    """
    if not connection_string or not connection_string.strip():
        return False

    try:
        parsed = urlparse(connection_string)
        return parsed.scheme.lower() in ("amqp", "amqps")
    except Exception:
        return False
