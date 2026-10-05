from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.money import FinancialResponseStatus


class AdviceItem(BaseModel):
    key: str
    tone: Literal["positive", "neutral", "warning"]
    # Frontend-translated by key — same key+params pattern as FinancialAlert,
    # Decimal money serializes as strings and carries an explicit currency
    # param; percentages/counts stay dimensionless numbers.
    params: dict[str, str | int | Decimal | float]


class AdviceResponse(FinancialResponseStatus):
    items: list[AdviceItem]
