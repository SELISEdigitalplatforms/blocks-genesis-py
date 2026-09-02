from pydantic import BaseModel


class UsageResult(BaseModel):
    """Quantities are floats: the meter may store them as Int64, Double or Decimal128."""

    allowed: bool
    meter_key: str
    used: float
    remaining: float
    overage: float
    replayed: bool
