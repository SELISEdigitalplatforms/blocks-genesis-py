from dataclasses import dataclass
from typing import Generic, TypeVar, Optional

T = TypeVar('T')

@dataclass
class ConsumerMessage(Generic[T]):
    consumer_name: str
    payload: T
    context: Optional[str] = None
    routing_key: str = ""
