"""A manually-tracked net-worth asset (investments, crypto, property, ...).

Cash is intentionally NOT an Asset — it's derived live from Account/Transaction
balances so there's a single source of truth for money that already flows
through the Transactions feature. See services/net_worth_service.py.
"""
from datetime import date as date_
from decimal import Decimal

from sqlalchemy import Boolean, Date, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AssetClass, CapitalRole, RiskLevel
from app.models.mixins import TimestampMixin


class Asset(Base, TimestampMixin):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, name="asset_class", native_enum=False, length=20), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # User-assigned, not inferred (see CapitalRole docstring).
    capital_role: Mapped[CapitalRole] = mapped_column(
        Enum(CapitalRole, name="capital_role", native_enum=False, length=10),
        nullable=False,
        default=CapitalRole.NEUTRAL,
    )
    # A rough, self-reported monthly net cash flow (rent collected, upkeep
    # paid, ...). Informational only — real-world cash flow for an asset
    # varies month to month, so this is never treated as ground truth the
    # way Transaction amounts are.
    monthly_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(20, 6, asdecimal=True), nullable=True)

    # User-assigned, not inferred (see RiskLevel docstring) — how likely
    # this asset is to lose value, independent of asset_class or capital_role.
    risk_level: Mapped[RiskLevel] = mapped_column(
        Enum(RiskLevel, name="risk_level", native_enum=False, length=10),
        nullable=False,
        default=RiskLevel.MEDIUM,
    )

    valuations: Mapped[list["AssetValuation"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="AssetValuation.as_of_date"
    )


class AssetValuation(Base):
    """One point-in-time value for an asset. The latest row (by as_of_date) is
    the asset's current value; the full history drives the net-worth chart."""

    __tablename__ = "asset_valuations"
    __table_args__ = (UniqueConstraint("asset_id", "as_of_date", name="uq_asset_valuation_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(38, 18, asdecimal=True), nullable=False)
    as_of_date: Mapped[date_] = mapped_column(Date, nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    currency_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    asset: Mapped["Asset"] = relationship(back_populates="valuations")
