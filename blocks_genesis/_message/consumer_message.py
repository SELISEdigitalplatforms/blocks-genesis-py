from typing import Dict, Optional

from pydantic import BaseModel

class ConsumerMessage(BaseModel):
    consumer_name: str
    payload: Dict
    payload_type: str
    context: Optional[str] = None
    routing_key: str = ""

