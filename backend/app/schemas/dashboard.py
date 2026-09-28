from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas.money import FinancialResponseStatus


class CategoryBreakdownChildItem(BaseModel):
    """One subcategory's (or the parent's own direct, un-subcategorized)
    share of a CategoryBreakdownItem's total — see
    services/category_rollup.py's CategoryRollupChildItem."""

    category_id: int
    name: str
    color: str
    icon: str | None
    amount: Decimal


class CategoryBreakdownItem(BaseModel):
    category_id: int | None
    name: str
    color: str
    icon: str | None
    amount: Decimal
    percent: float
    # Populated only when this slice's spend came from more than one
    # distinct category (subcategories, or a mix of the parent itself and
    # its children) — e.g. a receipt split across "Groceries" subcategories.
    children: list[CategoryBreakdownChildItem] = Field(default_factory=list)


class DashboardSummary(FinancialResponseStatus):
    year: int
    month: int
    real_income: Decimal | None
    spent: Decimal | None
    net: Decimal | None
    transferred_out: Decimal | None
    spending_by_category: list[CategoryBreakdownItem]
