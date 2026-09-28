"""API completeness vocabulary; no conversion or monetary precision policy."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field

CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


class MissingRate(BaseModel):
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    requested_date: date
    reason: Literal["absent", "stale", "precision_unrepresentable"]
    max_age_days: int = Field(ge=0)
    latest_available_date: date | None = None


class FinancialDataIssue(BaseModel):
    # Open vocabulary, matching the frontend's forward-compatible fallback.
    # Current codes: transfer_amount_unresolved, transfer_destination_missing,
    # crypto_quote_currency_unknown, valuation_currency_unconfirmed,
    # valuation_missing, price_missing.
    code: str = Field(min_length=1)


class FinancialDataStatus(BaseModel):
    complete: bool = True
    missing_rates: list[MissingRate] = Field(default_factory=list)
    data_issues: list[FinancialDataIssue] = Field(default_factory=list)


# Metric and response statuses have identical semantics, not separate models.
MetricStatus = FinancialDataStatus


class FinancialResponseStatus(FinancialDataStatus):
    currency: CurrencyCode
    metric_status: dict[str, MetricStatus] = Field(default_factory=dict)
