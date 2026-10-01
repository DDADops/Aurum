"""Validated direct FX observations, independent of any provider."""
from datetime import date
from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from app.core.currency import CurrencyCode


def require_decimal(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise ValueError("FX values must be Decimal, never float")
    return value


FxDecimal = Annotated[
    Decimal, BeforeValidator(require_decimal),
    Field(max_digits=38, decimal_places=18, allow_inf_nan=False, gt=0,
          le=Decimal("99999999999999999999.999999999999999999")),
]


class FxObservation(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)

    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: FxDecimal
    as_of_date: date
    source: str = Field(min_length=1, max_length=100)
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def distinct_currencies(self) -> "FxObservation":
        if self.base_currency == self.quote_currency:
            raise ValueError("FX observation currencies must differ")
        return self
