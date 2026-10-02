"""Aggregation logic behind the Overview dashboard."""
import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.category import Category
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.dashboard import CategoryBreakdownChildItem, CategoryBreakdownItem, DashboardSummary
from app.schemas.money import FinancialDataStatus, MissingRate
from app.services.category_rollup import rollup_category_contributions
from app.services.financial_data_service import merge_statuses
from app.services.fx_service import ResolvedRate, convert, resolve_rate
from app.services.settings_service import get_or_create_app_settings

# Categorical slots are capped at 8 (dataviz skill: a 9th series folds into "Other",
# never a generated hue) — this is also the exact size of the default category set.
MAX_CHART_SLICES = 8
OTHER_SLICE_COLOR = "#898781"  # muted ink, reserved for the non-categorical rollup


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


class _FlowValues:
    """Small request-local conversion cache; no stock or aggregation policy."""

    def __init__(self, session: AsyncSession, currency: str, max_age_days: int):
        self.session, self.currency, self.max_age_days = session, currency, max_age_days
        self.rates: dict[tuple[str, str, date, int], ResolvedRate | MissingRate] = {}

    async def value(self, amount: Decimal, native: str, day: date) -> tuple[Decimal | None, FinancialDataStatus]:
        if amount == 0:
            return Decimal("0"), FinancialDataStatus()
        key = (native, self.currency, day, self.max_age_days)
        if key not in self.rates:
            self.rates[key] = await resolve_rate(self.session, *key)
        rate = self.rates[key]
        if isinstance(rate, MissingRate):
            return None, FinancialDataStatus(complete=False, missing_rates=[rate])
        return convert(amount, rate), FinancialDataStatus()


async def get_dashboard_summary(session: AsyncSession, year: int, month: int) -> DashboardSummary:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _dated_summary(session, year, month)


async def _dated_summary(session: AsyncSession, year: int, month: int) -> DashboardSummary:
    start, end = _month_bounds(year, month)
    settings = await get_or_create_app_settings(session)
    fx = _FlowValues(session, settings.currency, settings.fx_max_age_days)
    metric_for = {TransactionType.INCOME: "real_income", TransactionType.EXPENSE: "spent",
                  TransactionType.TRANSFER: "transferred_out"}
    inputs: dict[str, list[FinancialDataStatus]] = {name: [] for name in metric_for.values()}
    totals = {name: Decimal("0") for name in metric_for.values()}
    contributions: list[tuple[int, int, Decimal]] = []
    transactions = (await session.execute(
        select(Transaction, Account.currency).join(Account, Account.id == Transaction.account_id)
        .where(Transaction.date >= start, Transaction.date <= end)
        .options(selectinload(Transaction.splits)).order_by(Transaction.id)
    )).all()
    for transaction, native in transactions:
        metric = metric_for[transaction.type]
        value, status = await fx.value(transaction.amount, native, transaction.date)
        inputs[metric].append(status)
        if value is None:
            continue  # incomplete metrics are suppressed below, never partial totals
        totals[metric] += value
        if transaction.type != TransactionType.EXPENSE:
            continue
        if transaction.splits:
            allocated = Decimal("0")
            for index, split in enumerate(transaction.splits):
                if index == len(transaction.splits) - 1:
                    # Reconcile the final Decimal-context residual deterministically
                    # to the last stored split. No cents or money-scale rounding.
                    converted = value - allocated
                else:
                    converted, _ = await fx.value(split.amount, native, transaction.date)
                    allocated += converted
                if split.category_id is not None:
                    contributions.append((transaction.id, split.category_id, converted))
        elif transaction.category_id is not None:
            contributions.append((transaction.id, transaction.category_id, value))

    statuses = {name: merge_statuses(*states) for name, states in inputs.items()}
    statuses["net"] = merge_statuses(statuses["real_income"], statuses["spent"])
    statuses["spending_by_category"] = statuses["spent"]
    status = merge_statuses(*statuses.values())
    real_income, spent, transferred_out = (totals[name] for name in ("real_income", "spent", "transferred_out"))
    categories = {c.id: c for c in (await session.scalars(select(Category))).all()}
    rows = rollup_category_contributions(contributions, categories) if statuses["spent"].complete else []

    top_rows, rest_rows = rows[:MAX_CHART_SLICES], rows[MAX_CHART_SLICES:]

    def _percent(amount: Decimal) -> float:
        return float(amount / spent * 100) if spent else 0.0

    spending_by_category = [
        CategoryBreakdownItem(
            category_id=row.category_id, name=row.name, color=row.color, icon=row.icon,
            amount=row.amount, percent=_percent(row.amount),
            children=[
                CategoryBreakdownChildItem(
                    category_id=child.category_id, name=child.name, color=child.color, icon=child.icon,
                    amount=child.amount,
                )
                for child in row.children
            ],
        )
        for row in top_rows
    ]

    if rest_rows:
        other_amount = sum((row.amount for row in rest_rows), Decimal("0"))
        spending_by_category.append(
            CategoryBreakdownItem(
                category_id=None, name="Other", color=OTHER_SLICE_COLOR, icon="more-horizontal",
                amount=other_amount, percent=_percent(other_amount),
            )
        )

    return DashboardSummary(
        currency=settings.currency, metric_status=statuses, **status.model_dump(),
        year=year,
        month=month,
        real_income=real_income if statuses["real_income"].complete else None,
        spent=spent if statuses["spent"].complete else None,
        net=real_income - spent if statuses["net"].complete else None,
        transferred_out=transferred_out if statuses["transferred_out"].complete else None,
        spending_by_category=spending_by_category if statuses["spent"].complete else [],
    )
