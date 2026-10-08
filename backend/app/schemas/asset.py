from datetime import date as date_
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.core.currency import CurrencyCode, CurrencyUpdate
from app.core.money import Money, Valuation
from app.models.enums import AssetClass, CapitalRole, RiskLevel


class AssetValuationCreate(BaseModel):
    expected_currency: CurrencyCode
    value: Valuation = Field(ge=0)
    as_of_date: date_ = Field(default_factory=date_.today)


class AssetValuationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    value: Decimal
    as_of_date: date_
    currency: str | None
    currency_confirmed: bool


class AssetBase(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    asset_class: AssetClass
    currency: CurrencyCode = "USD"
    notes: str | None = Field(default=None, max_length=2000)
    capital_role: CapitalRole = CapitalRole.NEUTRAL
    # Rough self-reported monthly net cash flow — informational, not tracked
    # transactions (see Asset.monthly_cash_flow).
    monthly_cash_flow: Money | None = None
    risk_level: RiskLevel = RiskLevel.MEDIUM


class AssetCreate(AssetBase):
    value: Valuation = Field(ge=0)
    as_of_date: date_ = Field(default_factory=date_.today)


class AssetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    asset_class: AssetClass | None = None
    currency: CurrencyUpdate = None
    notes: str | None = Field(default=None, max_length=2000)
    capital_role: CapitalRole | None = None
    monthly_cash_flow: Money | None = None
    risk_level: RiskLevel | None = None


class AssetRead(AssetBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    has_monetary_history: bool
    current_value: Decimal
    as_of_date: date_
    valuation_currency: str | None
    valuation_currency_confirmed: bool
