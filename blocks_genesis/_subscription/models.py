from pydantic import BaseModel


class UsageResult(BaseModel):
    """Quantities are floats -- the meter stores them as Decimal128 and they may be fractional."""

    allowed: bool
    meter_key: str
    used: float
    remaining: float
    overage: float
    replayed: bool
