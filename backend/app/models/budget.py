"""A monthly spending limit for one expense category — compared against
actual spend for whichever month is being viewed (services/budget_service.py).
Not month-scoped itself: one limit per category, in effect until changed."""
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Budget(Base, TimestampMixin):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(20, 6, asdecimal=True), nullable=False)

    category: Mapped["Category"] = relationship()
