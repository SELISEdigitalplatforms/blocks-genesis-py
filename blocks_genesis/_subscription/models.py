from pydantic import BaseModel


class UsageResult(BaseModel):
    allowed: bool
    meter_key: str
    used: int
    remaining: int
    overage: int
    replayed: bool
