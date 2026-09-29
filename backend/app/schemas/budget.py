from decimal import Decimal

from pydantic import BaseModel, Field

from app.core.currency import CurrencyCode, CurrencyUpdate
from app.core.money import Money


class BudgetCreate(BaseModel):
    currency: CurrencyCode | None = None
    category_id: int
    monthly_limit: Money = Field(gt=0)


class BudgetUpdate(BaseModel):
    currency: CurrencyUpdate = None
    monthly_limit: Money = Field(gt=0)


class BudgetRead(BaseModel):
    currency: CurrencyCode
    id: int
    category_id: int
    category_name: str
    category_color: str
    category_icon: str | None
    monthly_limit: Decimal


class BudgetStatus(BaseModel):
    """One budgeted category's actual spend for a given month, compared
    against its limit."""

    budget_id: int
    category_id: int
    category_name: str
    category_color: str
    category_icon: str | None
    monthly_limit: Decimal
    spent: Decimal
    remaining: Decimal
    # Can exceed 100 — the UI clamps the progress bar fill, not this number,
    # so "spent 150% of budget" is still visible in the raw value.
    percent: float
    is_over_budget: bool


class BudgetStatusResponse(BaseModel):
    year: int
    month: int
    items: list[BudgetStatus]
