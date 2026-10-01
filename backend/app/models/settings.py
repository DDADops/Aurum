"""App-wide configuration that isn't tied to any single account/asset — a
single-row table (id is always 1). Primary display currency (see UPDATES.md
for why this doesn't do currency conversion) and the proactive-alert
thresholds consumed by services/insights_service.py."""
from decimal import Decimal

from sqlalchemy import CheckConstraint, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AppSettings(Base):
    __tablename__ = "app_settings"

    __table_args__ = (CheckConstraint("fx_max_age_days >= 0", name="ck_app_settings_fx_max_age_days"),)

    fx_max_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7, server_default="7")
    id: Mapped[int] = mapped_column(primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    # Consecutive complete months of negative cash flow / declining net worth
    # before insights_service.py raises the corresponding alert.
    negative_cash_flow_threshold_months: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    net_worth_decline_threshold_months: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # Max % of total capital (cash + assets) allowed in medium/high risk
    # tiers before insights_service.py raises risky_allocation_exceeded —
    # the classic "80% at zero risk, 20% at most exposed" rule.
    risky_allocation_threshold_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    # A depository account (checking/savings/cash) sitting at or above this
    # balance with no transaction touching it in idle_cash_threshold_days
    # raises insights_service.py's idle_cash alert — money that isn't
    # working. In the app's display currency (see `currency` above).
    idle_cash_threshold_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6, asdecimal=True), nullable=False, default=Decimal("1000"))
    idle_cash_threshold_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
