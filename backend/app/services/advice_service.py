"""Personalized, read-only financial advice — a curated set of insights
computed from data that already exists (transactions, budgets, category
totals). Unlike insights_service.py's proactive alerts (urgent, shown as a
banner on every page), these are advisory notes shown only on the dedicated
Advice page — a mix of positive, neutral, and cautionary tone, not just
warnings, and not something the user needs to act on immediately.
"""
import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import CategoryKind, TransactionType
from app.models.transaction import Transaction
from app.schemas.advice import AdviceItem, AdviceResponse
from app.schemas.money import FinancialDataStatus
from app.services.dashboard_service import get_dashboard_summary
from app.services.financial_data_service import merge_statuses
from app.services.reports_service import _DatedValues, _category_contributions
from app.services.settings_service import get_or_create_app_settings

TRAILING_MONTHS = 3
RISING_CATEGORY_THRESHOLD_PERCENT = 25
SAVINGS_RATE_TREND_THRESHOLD_POINTS = 5


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


async def _category_expense_totals(
    session: AsyncSession, year: int, month: int, statuses: list[FinancialDataStatus],
    category_ids: set[int] | None = None, excluded_category_ids: set[int] | None = None,
) -> dict[int, Decimal] | None:
    start, end = _month_bounds(year, month)
    # Preserve the existing direct-category scope (split-only transactions were
    # not inputs to these rules). Read IDs only; Reports owns money conversion.
    direct_ids = set((await session.scalars(select(Transaction.id).where(
        Transaction.type == TransactionType.EXPENSE, Transaction.category_id.is_not(None),
        Transaction.date >= start, Transaction.date <= end,
    ))).all())
    settings = await get_or_create_app_settings(session)
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        contributions = await _category_contributions(
            session, _DatedValues(session, settings.currency, settings.fx_max_age_days),
            CategoryKind.EXPENSE, start, end, category_ids,
        )
        contributions = [item for item in contributions if item.transaction_id in direct_ids
                         and item.category_id not in (excluded_category_ids or set())]
        status = merge_statuses(*(merge_statuses(item.status, FinancialDataStatus(complete=item.amount is not None))
                                  for item in contributions))
        statuses.append(status)
        if not status.complete:
            return None
        totals: dict[int, Decimal] = defaultdict(Decimal)
        for item in contributions:
            totals[item.category_id] += item.amount
        return dict(totals)


async def _rising_category_advice(
    session: AsyncSession, year: int, month: int, statuses: list[FinancialDataStatus] | None = None,
) -> AdviceItem | None:
    """The expense category furthest above its own trailing-3-month average,
    if that's at least RISING_CATEGORY_THRESHOLD_PERCENT higher."""
    statuses = statuses if statuses is not None else []
    current_totals = await _category_expense_totals(session, year, month, statuses)
    if not current_totals:
        return None

    trailing_totals: dict[int, Decimal] = defaultdict(Decimal)
    y, m = year, month
    for _ in range(TRAILING_MONTHS):
        y, m = _previous_month(y, m)
        totals = await _category_expense_totals(session, y, m, statuses, set(current_totals))
        if totals is None:
            return None
        for cat_id, amount in totals.items():
            trailing_totals[cat_id] += amount

    best: tuple[Decimal, int, Decimal, Decimal] | None = None
    for cat_id, current in current_totals.items():
        average = trailing_totals.get(cat_id, Decimal("0")) / TRAILING_MONTHS
        if average <= 0:
            continue
        increase_percent = (current - average) / average * 100
        if increase_percent >= RISING_CATEGORY_THRESHOLD_PERCENT and (best is None or increase_percent > best[0]):
            best = (increase_percent, cat_id, current, average)

    if best is None:
        return None

    increase_percent, cat_id, current, average = best
    category = await session.get(Category, cat_id)
    if category is None:
        return None

    return AdviceItem(
        key="rising_category",
        tone="warning",
        params={
            "category": category.name,
            "percent": round(increase_percent),
            "current": current,
            "average": average,
            "currency": (await get_or_create_app_settings(session)).currency,
        },
    )


async def _unbudgeted_top_category_advice(
    session: AsyncSession, year: int, month: int, statuses: list[FinancialDataStatus] | None = None,
) -> AdviceItem | None:
    """This month's highest-spending expense category that has no budget."""
    statuses = statuses if statuses is not None else []
    budgeted_ids = set((await session.scalars(select(Budget.category_id))).all())
    current_totals = await _category_expense_totals(
        session, year, month, statuses, excluded_category_ids=budgeted_ids,
    )
    if not current_totals:
        return None

    for cat_id, amount in sorted(current_totals.items(), key=lambda item: item[1], reverse=True):
        category = await session.get(Category, cat_id)
        if category is None:
            continue
        return AdviceItem(
            key="unbudgeted_top_category",
            tone="neutral",
            params={"category": category.name, "amount": amount,
                    "currency": (await get_or_create_app_settings(session)).currency},
        )

    return None


async def _savings_rate_trend_advice(
    session: AsyncSession, year: int, month: int, statuses: list[FinancialDataStatus] | None = None,
) -> AdviceItem | None:
    """This month's savings rate (net / real_income) vs. last month's, when
    the swing is large enough to be worth mentioning."""
    current = await get_dashboard_summary(session, year, month)
    prev_year, prev_month = _previous_month(year, month)
    previous = await get_dashboard_summary(session, prev_year, prev_month)

    statuses = statuses if statuses is not None else []
    status = merge_statuses(*(merge_statuses(
        item.metric_status.get("real_income", item), item.metric_status.get("net", item),
        FinancialDataStatus(complete=item.real_income is not None and item.net is not None),
    ) for item in (current, previous)))
    statuses.append(status)
    if not status.complete:
        return None
    if current.real_income <= 0 or previous.real_income <= 0:
        return None

    current_rate = current.net / current.real_income * 100
    previous_rate = previous.net / previous.real_income * 100
    diff = current_rate - previous_rate

    if abs(diff) < SAVINGS_RATE_TREND_THRESHOLD_POINTS:
        return None

    return AdviceItem(
        key="savings_rate_trend",
        tone="positive" if diff > 0 else "warning",
        params={"rate": round(current_rate), "diff": round(diff)},
    )


async def get_advice(session: AsyncSession) -> AdviceResponse:
    today = date.today()
    currency = (await get_or_create_app_settings(session)).currency
    generators = (_rising_category_advice, _unbudgeted_top_category_advice, _savings_rate_trend_advice)
    metrics: dict[str, FinancialDataStatus] = {}
    items: list[AdviceItem] = []
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        for key, generate in zip(("rising_category", "unbudgeted_top_category", "savings_rate_trend"), generators):
            inputs: list[FinancialDataStatus] = []
            item = await generate(session, today.year, today.month, inputs)
            metrics[key] = merge_statuses(*inputs)
            if item is not None:
                items.append(item)
    return AdviceResponse(currency=currency, items=items, metric_status=metrics,
                          **merge_statuses(*metrics.values()).model_dump())
