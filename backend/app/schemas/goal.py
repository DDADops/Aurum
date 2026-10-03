from datetime import date as date_
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.core.currency import CurrencyCode, CurrencyUpdate
from app.core.money import Money


class GoalCreate(BaseModel):
    currency: CurrencyCode | None = None
    name: str = Field(min_length=1, max_length=150)
    target_amount: Money = Field(gt=0)
    target_date: date_ | None = None


class GoalUpdate(BaseModel):
    """Currency changes require an empty contribution log and an explicit target."""

    currency: CurrencyUpdate = None
    name: str | None = Field(default=None, min_length=1, max_length=150)
    target_amount: Money | None = Field(default=None, gt=0)
    target_date: date_ | None = None

    @field_validator("target_amount")
    @classmethod
    def _target_cannot_be_cleared(cls, value: Decimal | None) -> Decimal:
        if value is None:
            raise ValueError("target_amount must not be null")
        return value


class GoalContributionCreate(BaseModel):
    """Manual nominal delta in Goal.currency; no account or reference FX source."""

    # Negative allowed — a withdrawal from the goal is still a contribution
    # to its running total, just in the other direction. Zero is pointless.
    amount: Money
    date: date_
    note: str | None = Field(default=None, max_length=200)

    @field_validator("amount")
    @classmethod
    def _reject_zero(cls, value: Decimal) -> Decimal:
        # Has to be a validator: pydantic has no "not equal" constraint, so
        # the Field(ne=0) this replaces was parsed as an unknown keyword,
        # stored as schema metadata, and never actually checked anything.
        if value == 0:
            raise ValueError("amount must not be zero")
        return value


class GoalRead(BaseModel):
    currency: CurrencyCode
    id: int
    name: str
    target_amount: Decimal
    target_date: date_ | None
    current_amount: Decimal
    remaining: Decimal
    percent: float
    is_reached: bool
