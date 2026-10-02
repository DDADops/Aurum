from datetime import date as date_
from decimal import Decimal

from pydantic import Field

from app.schemas.money import FinancialDataStatus, FinancialResponseStatus


class CashFlowPoint(FinancialDataStatus):
    """Independent month and income/expense/net completeness."""

    metric_status: dict[str, FinancialDataStatus] = Field(default_factory=dict)
    year: int
    month: int
    income: Decimal | None
    expense: Decimal | None
    net: Decimal | None


class CashFlowResponse(FinancialResponseStatus):
    start_date: date_ | None
    end_date: date_ | None
    points: list[CashFlowPoint]
    total_income: Decimal | None
    total_expense: Decimal | None
    total_net: Decimal | None
