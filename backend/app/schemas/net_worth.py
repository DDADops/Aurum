from datetime import date as date_
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.money import FinancialResponseStatus, FinancialDataStatus


class NetWorthPoint(FinancialDataStatus):
    date: date_
    value: Decimal | None


class NetWorthBreakdownItem(BaseModel):
    key: str
    name: str
    color: str
    icon: str
    amount: Decimal
    percent: float


class CapitalRoleSummary(BaseModel):
    """Assets grouped by how they behave month to month (see CapitalRole) —
    a cross-cut of the asset-class breakdown, not a replacement for it."""

    role: str
    label: str
    color: str
    total_value: Decimal
    monthly_cash_flow: Decimal
    count: int


class RiskLevelItem(BaseModel):
    """One holding within a risk tier — Cash (key="cash") or a single asset
    (key=f"asset:{id}"). This list IS the diversification view: a tier with
    one item at 100% is concentrated, several items with even shares aren't,
    no separate score needed."""

    key: str
    name: str
    amount: Decimal
    percent: float  # share of this tier, not of total capital


class RiskLevelSummary(BaseModel):
    """Cash + assets grouped by user-tagged risk of loss (see RiskLevel) —
    another cross-cut, like capital_roles, but Cash participates here since
    it's the zero-risk anchor an 80/20-style allocation rule needs."""

    risk_level: str
    label: str
    color: str
    total_value: Decimal
    percent: float  # share of total capital (cash + assets)
    items: list[RiskLevelItem]


class NetWorthSummary(FinancialResponseStatus):
    """Top-level status describes today; series and metric_status are independent.

    Empty breakdown/roles/risk lists with incomplete metric_status are unavailable,
    not zero allocations. Change metrics require both comparison endpoints.
    """

    range: str
    current: Decimal | None
    change_amount: Decimal | None
    change_percent: float | None
    series: list[NetWorthPoint]
    breakdown: list[NetWorthBreakdownItem]
    capital_roles: list[CapitalRoleSummary]
    risk_levels: list[RiskLevelSummary]
