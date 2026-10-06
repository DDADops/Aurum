"""Shape of a full-database backup file (see services/backup_service.py)."""
from copy import deepcopy
from datetime import date as date_
from datetime import datetime
from decimal import Decimal, localcontext
from typing import Annotated, get_args

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.core.currency import CurrencyCode
from app.core.money import Money, Valuation
from app.models.enums import (
    AccountType,
    AssetClass,
    CapitalRole,
    CategoryKind,
    CryptoTransactionType,
    RecurringFrequency,
    RiskLevel,
    TransactionType,
)


CachePercent = Annotated[Decimal, Field(max_digits=10, decimal_places=4, allow_inf_nan=False)]


class AccountBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str
    type: AccountType
    currency: CurrencyCode
    color: str | None
    is_archived: bool


class CategoryBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str
    kind: CategoryKind
    icon: str | None
    color: str
    sort_order: int
    is_default: bool
    # Defaulted so a backup exported before subcategories existed still
    # imports cleanly through v1 normalization.
    parent_id: StrictInt | None = None


class TagBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str


class TransactionBackup(BaseModel):
    transfer_amount: Money | None = None

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    account_id: StrictInt
    category_id: StrictInt | None
    transfer_account_id: StrictInt | None
    type: TransactionType
    amount: Money
    description: str
    merchant: str | None
    notes: str | None
    date: date_
    # Defaulted so a backup exported before tags existed still imports
    # cleanly through v1 normalization. Not a plain column — populated
    # explicitly in build_backup() from the `tags` relationship, since
    # from_attributes can't map a `tags` relationship to a `tag_ids` field.
    tag_ids: list[StrictInt] = Field(default_factory=list)


class TransactionSplitBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    transaction_id: StrictInt
    category_id: StrictInt | None
    amount: Money
    note: str | None


class AssetBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str
    asset_class: AssetClass
    currency: CurrencyCode
    notes: str | None
    # Defaulted so a backup exported before capital_role/monthly_cash_flow
    # existed still imports cleanly through v1 normalization.
    capital_role: CapitalRole = CapitalRole.NEUTRAL
    monthly_cash_flow: Money | None = None
    # Defaulted so a backup exported before risk_level existed still
    # imports cleanly through v1 normalization.
    risk_level: RiskLevel = RiskLevel.MEDIUM


class AssetValuationBackup(BaseModel):
    currency: CurrencyCode | None = None
    currency_confirmed: bool = False

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    asset_id: StrictInt
    value: Valuation
    as_of_date: date_


class CryptoPortfolioBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str
    color: str | None
    is_archived: bool


class CryptoHoldingBackup(BaseModel):
    network: str | None = None
    last_price_currency: CurrencyCode | None = None
    last_price_as_of_date: date_ | None = None

    model_config = ConfigDict(from_attributes=True)

    # asset_id doubles as this row's own primary key (see
    # models/crypto.py's CryptoHolding) — there's no separate `id`. No
    # quantity here — it's derived from crypto_transactions below, not
    # stored.
    asset_id: StrictInt
    # Only v1 NULL/omitted portfolios use the historical fallback.
    # Non-NULL dangling IDs are rejected, never repaired.
    portfolio_id: StrictInt | None = None
    coingecko_id: str
    symbol: str
    name: str
    thumb_url: str | None
    last_price: Valuation | None = None
    price_change_1h: CachePercent | None = None
    price_change_24h: CachePercent | None = None
    price_change_7d: CachePercent | None = None
    # Defaulted so a backup exported before 30d/1y existed still imports
    # cleanly through v1 normalization.
    price_change_30d: CachePercent | None = None
    price_change_1y: CachePercent | None = None


class CryptoTransactionBackup(BaseModel):
    quote_currency: CurrencyCode | None = None

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    asset_id: StrictInt
    type: CryptoTransactionType
    quantity: Valuation
    price_per_unit: Valuation
    date: date_
    note: str | None


class BudgetBackup(BaseModel):
    currency: CurrencyCode | None = None

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    category_id: StrictInt
    monthly_limit: Money


class GoalBackup(BaseModel):
    currency: CurrencyCode | None = None

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    name: str
    target_amount: Money
    target_date: date_ | None


class GoalContributionBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    goal_id: StrictInt
    amount: Money
    date: date_
    note: str | None


class RecurringTransactionBackup(BaseModel):
    amount_currency: CurrencyCode | None = None
    transfer_amount: Money | None = None
    transfer_amount_currency: CurrencyCode | None = None

    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    account_id: StrictInt
    category_id: StrictInt | None
    transfer_account_id: StrictInt | None
    type: TransactionType
    amount: Money
    description: str
    merchant: str | None
    notes: str | None
    frequency: RecurringFrequency
    anchor_date: date_
    last_posted_date: date_ | None
    is_active: bool


class AppSettingsBackup(BaseModel):
    id: StrictInt = Field(default=1, ge=1, le=1)
    fx_max_age_days: int = Field(default=7, ge=0, le=2147483647)

    model_config = ConfigDict(from_attributes=True)

    currency: CurrencyCode
    # Defaulted so a backup exported before these thresholds existed still
    # imports cleanly through v1 normalization.
    negative_cash_flow_threshold_months: int = Field(default=2, ge=1, le=24)
    net_worth_decline_threshold_months: int = Field(default=2, ge=1, le=24)
    # Defaulted so a backup exported before this threshold existed still
    # imports cleanly through v1 normalization.
    risky_allocation_threshold_percent: int = Field(default=20, ge=1, le=100)
    # Defaulted so a backup exported before idle-cash thresholds existed
    # still imports cleanly through v1 normalization.
    idle_cash_threshold_amount: Money = Field(default=Decimal("1000"), ge=0)
    idle_cash_threshold_days: int = Field(default=60, ge=1, le=365)


class FxRateBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt
    base_currency: CurrencyCode
    quote_currency: CurrencyCode
    rate: Valuation = Field(gt=0)
    as_of_date: date_
    source: str = Field(min_length=1, max_length=100)
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def distinct(self):
        if self.base_currency == self.quote_currency:
            raise ValueError("FX currencies must differ")
        return self


class CryptoSyncStateBackup(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: StrictInt = Field(ge=1, le=1)
    last_synced_at: AwareDatetime | None


class BackupPayload(BaseModel):
    """A full, portable snapshot of every table. `aurum_backup_version` is
    checked on import so an incompatible/future file is rejected cleanly
    instead of half-applied."""

    fx_rates: list[FxRateBackup] = Field(default_factory=list)
    crypto_sync_state: CryptoSyncStateBackup | None = None
    aurum_backup_version: int
    exported_at: datetime
    app_version: str
    accounts: list[AccountBackup]
    categories: list[CategoryBackup]
    # Defaulted so a backup exported before tags existed still imports
    # cleanly through v1 normalization.
    tags: list[TagBackup] = Field(default_factory=list)
    transactions: list[TransactionBackup]
    # Defaulted so a backup exported before transaction splitting existed
    # still imports cleanly through v1 normalization.
    transaction_splits: list[TransactionSplitBackup] = Field(default_factory=list)
    assets: list[AssetBackup]
    asset_valuations: list[AssetValuationBackup]
    # Defaulted so a backup exported before crypto holdings/portfolios
    # existed still imports cleanly through v1 normalization.
    crypto_portfolios: list[CryptoPortfolioBackup] = Field(default_factory=list)
    crypto_holdings: list[CryptoHoldingBackup] = Field(default_factory=list)
    crypto_transactions: list[CryptoTransactionBackup] = Field(default_factory=list)
    # Defaulted so a backup exported before budgets existed still imports
    # cleanly through v1 normalization.
    budgets: list[BudgetBackup] = Field(default_factory=list)
    # Defaulted so a backup exported before goals existed still imports
    # cleanly through v1 normalization.
    goals: list[GoalBackup] = Field(default_factory=list)
    goal_contributions: list[GoalContributionBackup] = Field(default_factory=list)
    # Defaulted so a backup exported before recurring transactions existed
    # still imports cleanly through v1 normalization.
    recurring_transactions: list[RecurringTransactionBackup] = Field(default_factory=list)
    # Defaulted so a backup exported before the currency setting existed
    # still imports cleanly through v1 normalization.
    app_settings: AppSettingsBackup = Field(default_factory=lambda: AppSettingsBackup(currency="USD"))

    @model_validator(mode="wrap")
    @classmethod
    def versioned(cls, value, handler):
        # Pydantic Decimal bounds must also be independent of ambient precision.
        with localcontext() as context:
            context.prec = 96
            if isinstance(value, dict):
                version = value.get("aurum_backup_version")
                if type(version) is not int or version not in (1, 2):
                    raise ValueError("Unsupported backup version; expected 1 or 2")
                if version == 1:
                    try:
                        value = normalize_v1(value)
                    except (TypeError, KeyError, AttributeError) as exc:
                        raise ValueError("Malformed v1 backup structure") from exc
                else:
                    missing = cls.model_fields.keys() - value.keys()
                    if missing:
                        raise ValueError(f"Missing v2 fields: {sorted(missing)}")
                    extra = value.keys() - cls.model_fields.keys()
                    if extra:
                        raise ValueError(f"Unknown v2 fields: {sorted(extra)}")
                    for key, row_type in BACKUP_ROWS.items():
                        if not isinstance(value[key], list):
                            raise ValueError(f"{key} must be an array")
                        for row in value[key]:
                            _require_v2_row(row, row_type)
                    _require_v2_row(value.get("app_settings"), AppSettingsBackup)
                    if value.get("crypto_sync_state") is not None:
                        _require_v2_row(value["crypto_sync_state"], CryptoSyncStateBackup)
            return handler(value)


BACKUP_ROWS = {
    "accounts": AccountBackup, "categories": CategoryBackup, "tags": TagBackup,
    "transactions": TransactionBackup, "transaction_splits": TransactionSplitBackup,
    "assets": AssetBackup, "asset_valuations": AssetValuationBackup,
    "crypto_portfolios": CryptoPortfolioBackup, "crypto_holdings": CryptoHoldingBackup,
    "crypto_transactions": CryptoTransactionBackup, "budgets": BudgetBackup,
    "goals": GoalBackup, "goal_contributions": GoalContributionBackup,
    "recurring_transactions": RecurringTransactionBackup, "fx_rates": FxRateBackup,
}


def _require_v2_row(row, row_type):
    # ORM export passes typed rows. Imported JSON must contain every field,
    # including explicit NULLs, so omission cannot silently erase provenance.
    if isinstance(row, row_type):
        return
    if not isinstance(row, dict):
        raise ValueError(f"Expected object for {row_type.__name__}")
    extra = row.keys() - row_type.model_fields.keys()
    if extra:
        raise ValueError(f"Unknown {row_type.__name__} fields: {sorted(extra)}")
    missing = row_type.model_fields.keys() - row.keys()
    if missing:
        raise ValueError(f"Missing {row_type.__name__} fields: {sorted(missing)}")
    for name, field in row_type.model_fields.items():
        if ((field.annotation is Decimal or Decimal in get_args(field.annotation))
                and row[name] is not None and not isinstance(row[name], (str, Decimal))):
            raise ValueError(f"{name} must be a decimal string")


def normalize_v1(source: dict) -> dict:
    """Pure compatibility conversion: no database, environment or FX access."""
    value = deepcopy(source)
    settings = value.get("app_settings", {})
    if not isinstance(settings, dict):
        raise ValueError("app_settings must be an object")
    settings.setdefault("currency", "USD")
    settings = AppSettingsBackup.model_validate(settings)
    value["app_settings"] = settings.model_dump()
    accounts = {row["id"]: AccountBackup.model_validate(row) for row in value.get("accounts", [])}
    for key in ("transactions", "recurring_transactions"):
        for row in value.get(key, []):
            source_account = accounts.get(row.get("account_id"))
            destination_id = row.get("transfer_account_id")
            destination = accounts.get(destination_id)
            if source_account is None or (destination_id is not None and destination is None):
                raise ValueError(f"{key}: dangling Account reference")
            if key == "recurring_transactions":
                row["amount_currency"] = source_account.currency
                row["transfer_amount_currency"] = destination.currency if destination and row["type"] == "transfer" else None
            if "transfer_amount" not in row:
                row["transfer_amount"] = (row["amount"] if row["type"] == "transfer" and destination
                                          and source_account.currency == destination.currency else None)
    for key in ("budgets", "goals"):
        for row in value.get(key, []):
            if row.get("currency") is None:
                row["currency"] = settings.currency
    for row in value.get("asset_valuations", []):
        row.update(currency=None, currency_confirmed=False)
    for row in value.get("crypto_transactions", []):
        row["quote_currency"] = None
    for row in value.get("crypto_holdings", []):
        row.update(last_price_currency=None, last_price_as_of_date=None)
    # The pre-portfolios format explicitly supported a missing portfolio.
    # Resolve only NULL; a non-NULL dangling FK must still fail validation.
    portfolios = value.setdefault("crypto_portfolios", [])
    if any(row.get("portfolio_id") is None for row in value.get("crypto_holdings", [])):
        fallback_id = max((row["id"] for row in portfolios), default=0) + 1
        portfolios.append(dict(id=fallback_id, name="Main Portfolio", color="#2a78d6", is_archived=False))
        for row in value["crypto_holdings"]:
            if row.get("portfolio_id") is None:
                row["portfolio_id"] = fallback_id
    value["fx_rates"] = []
    value["crypto_sync_state"] = None
    value["aurum_backup_version"] = 2
    return value
