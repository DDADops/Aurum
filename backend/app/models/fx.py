"""Accepted direct observations: quote units per one base unit."""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        CheckConstraint("base_currency <> quote_currency", name="ck_fx_rates_distinct_currencies"),
        CheckConstraint("rate > 0 AND rate < 'Infinity'::numeric AND rate <> 'NaN'::numeric", name="ck_fx_rates_positive_finite"),
        # This B-tree also supports backward scans for newest pair/date lookup.
        UniqueConstraint("base_currency", "quote_currency", "as_of_date", name="uq_fx_rates_pair_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
