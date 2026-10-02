from datetime import date as date_
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.money import FinancialDataStatus, FinancialResponseStatus


class CategoryRankingChildItem(BaseModel):
    """One subcategory's (or the parent's own direct, un-subcategorized)
    share of a CategoryRankingItem's total — see
    services/category_rollup.py's CategoryRollupChildItem."""

    category_id: int
    name: str
    color: str
    icon: str | None
    amount: Decimal


class CategorySpendingPoint(FinancialDataStatus):
    year: int
    month: int
    amount: Decimal | None


class CategorySpendingReport(FinancialResponseStatus):
    category_id: int
    category_name: str
    category_color: str
    category_icon: str | None
    start_date: date_ | None
    end_date: date_ | None
    total_amount: Decimal | None
    transaction_count: int
    average_per_month: Decimal | None
    series: list[CategorySpendingPoint]


class CategoryRankingItem(BaseModel):
    category_id: int
    name: str
    color: str
    icon: str | None
    amount: Decimal
    percent: float
    transaction_count: int
    # Populated only when this category's spend came from more than one
    # distinct category (subcategories, or a mix of itself and its
    # children) — e.g. a receipt split across "Groceries" subcategories.
    children: list[CategoryRankingChildItem] = Field(default_factory=list)


class CategoryRankingReport(FinancialResponseStatus):
    start_date: date_ | None
    end_date: date_ | None
    total_amount: Decimal | None
    items: list[CategoryRankingItem]
