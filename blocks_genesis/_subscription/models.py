from pydantic import BaseModel


class UsageResult(BaseModel):
    """Quantities are floats -- the meter stores them as Decimal128 and they may be fractional."""

    allowed: bool
    meter_key: str
    used: float
    remaining: float
    overage: float
    replayed: bool
    # Decimal places the meter accepts: 0 (the default) is whole numbers only, 2 allows 550.55.
    quantity_scale: int = 0
    # Derived from quantity_scale, for callers that only need the yes/no.
    is_fraction_allowed: bool = False
