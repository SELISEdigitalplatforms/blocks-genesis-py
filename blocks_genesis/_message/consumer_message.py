from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class ConsumerMessage:
    consumer_name: str
    payload: Any  
    context: Optional[str] = None
    routing_key: str = ""

