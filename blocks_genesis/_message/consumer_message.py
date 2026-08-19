from typing import Dict, Optional

from pydantic import BaseModel

class ConsumerMessage(BaseModel):
    consumer_name: str
    payload: Dict
    payload_type: str
    context: Optional[str] = None
    routing_key: str = ""

    # Optional absolute lifetime for this message's delegation grant, in seconds. Defaults to two
    # days. Set it to cover the longest the job may legitimately take, including retries.
    delegation_ttl_seconds: Optional[int] = None

