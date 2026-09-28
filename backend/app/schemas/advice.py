from typing import Literal

from pydantic import BaseModel

from app.schemas.money import FinancialResponseStatus


class AdviceItem(BaseModel):
    key: str
    tone: Literal["positive", "neutral", "warning"]
    # Frontend-translated by key — same key+params pattern as FinancialAlert,
    # extended to allow string params (category names) alongside numbers.
    params: dict[str, str | int | float]


class AdviceResponse(FinancialResponseStatus):
    items: list[AdviceItem]
