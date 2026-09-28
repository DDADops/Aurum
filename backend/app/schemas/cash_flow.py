from datetime import date as date_
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.money import FinancialResponseStatus


class CashFlowPoint(BaseModel):
    year: int
    month: int
    income: Decimal
    expense: Decimal
    net: Decimal


class CashFlowResponse(FinancialResponseStatus):
    start_date: date_ | None
    end_date: date_ | None
    points: list[CashFlowPoint]
    total_income: Decimal | None
    total_expense: Decimal | None
    total_net: Decimal | None
