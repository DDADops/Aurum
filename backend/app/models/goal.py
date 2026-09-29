"""A savings goal, tracked by a manual contribution log (GoalContribution) —
same "you record it, we sum it" shape as Asset/AssetValuation, except
contributions are deltas added together, not point-in-time snapshots."""
from datetime import date as date_
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.mixins import TimestampMixin


class Goal(Base, TimestampMixin):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6, asdecimal=True), nullable=False)
    target_date: Mapped[date_ | None] = mapped_column(Date, nullable=True)

    contributions: Mapped[list["GoalContribution"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", order_by="GoalContribution.date"
    )


class GoalContribution(Base):
    """One deposit (or, with a negative amount, a withdrawal) toward a goal.
    A goal's current amount is the sum of all its contributions."""

    __tablename__ = "goal_contributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6, asdecimal=True), nullable=False)
    date: Mapped[date_] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    goal: Mapped["Goal"] = relationship(back_populates="contributions")
