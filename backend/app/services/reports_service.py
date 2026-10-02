"""Dated category flows: single-category monthly detail and category ranking."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.category import Category
from app.models.enums import CategoryKind, TransactionType
from app.models.transaction import Transaction, TransactionSplit
from app.schemas.money import FinancialDataStatus, MissingRate
from app.schemas.reports import (
    CategoryRankingChildItem, CategoryRankingItem, CategoryRankingReport,
    CategorySpendingPoint, CategorySpendingReport,
)
from app.services.category_rollup import rollup_category_contributions
from app.services.financial_data_service import merge_statuses
from app.services.fx_service import ResolvedRate, convert, resolve_rate
from app.services.settings_service import get_or_create_app_settings


_KIND_TO_TRANSACTION_TYPE = {
    CategoryKind.EXPENSE: TransactionType.EXPENSE,
    CategoryKind.INCOME: TransactionType.INCOME,
}


class _DatedValues:
    """Request-local conversion cache, without report or category policy."""

    def __init__(self, session: AsyncSession, currency: str, max_age_days: int):
        self.session, self.currency, self.max_age_days = session, currency, max_age_days
        self.rates: dict[tuple[str, str, date_, int], ResolvedRate | MissingRate] = {}

    async def value(self, amount: Decimal, native: str, day: date_) -> tuple[Decimal | None, FinancialDataStatus]:
        if amount == 0:
            return Decimal("0"), FinancialDataStatus()
        key = (native, self.currency, day, self.max_age_days)
        if key not in self.rates:
            self.rates[key] = await resolve_rate(self.session, *key)
        rate = self.rates[key]
        if isinstance(rate, MissingRate):
            return None, FinancialDataStatus(complete=False, missing_rates=[rate])
        return convert(amount, rate), FinancialDataStatus()


@dataclass
class _Contribution:
    transaction_id: int
    category_id: int
    day: date_
    amount: Decimal | None
    status: FinancialDataStatus


async def _category_contributions(
    session: AsyncSession, fx: _DatedValues, kind: CategoryKind,
    start_date: date_ | None, end_date: date_ | None, category_ids: set[int] | None = None,
) -> list[_Contribution]:
    """Select only qualifying transactions, convert, allocate, then filter leaves."""
    plain = Transaction.category_id.is_not(None) if category_ids is None else Transaction.category_id.in_(category_ids)
    split = TransactionSplit.category_id.is_not(None) if category_ids is None else TransactionSplit.category_id.in_(category_ids)
    stmt = select(Transaction, Account.currency).join(Account, Account.id == Transaction.account_id).where(
        Transaction.type == _KIND_TO_TRANSACTION_TYPE[kind], or_(plain, Transaction.splits.any(split)),
    ).options(selectinload(Transaction.splits)).order_by(Transaction.date, Transaction.id)
    if start_date is not None:
        stmt = stmt.where(Transaction.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Transaction.date <= end_date)
    contributions = []
    for transaction, native in (await session.execute(stmt)).all():
        value, status = await fx.value(transaction.amount, native, transaction.date)
        allocations = []
        if transaction.splits:
            allocated = Decimal("0")
            for index, part in enumerate(transaction.splits):
                if value is None:
                    amount = None
                elif index == len(transaction.splits) - 1:
                    # Match Dashboard: final stored split receives the precision
                    # residual, including when its category has been deleted.
                    amount = value - allocated
                else:
                    amount, _ = await fx.value(part.amount, native, transaction.date)
                    allocated += amount
                allocations.append((part.category_id, amount))
        else:
            allocations.append((transaction.category_id, value))
        for category_id, amount in allocations:
            if category_id is not None and (category_ids is None or category_id in category_ids):
                contributions.append(_Contribution(transaction.id, category_id, transaction.date, amount, status))
    return contributions


async def get_category_spending_report(
    session: AsyncSession, category_id: int, start_date: date_ | None, end_date: date_ | None,
) -> CategorySpendingReport:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _category_spending(session, category_id, start_date, end_date)


async def _category_spending(
    session: AsyncSession, category_id: int, start_date: date_ | None, end_date: date_ | None,
) -> CategorySpendingReport:
    settings = await get_or_create_app_settings(session)
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    category_ids = {category_id}
    if category.parent_id is None:
        category_ids.update((await session.scalars(select(Category.id).where(Category.parent_id == category_id))).all())
    fx = _DatedValues(session, settings.currency, settings.fx_max_age_days)
    contributions = await _category_contributions(session, fx, category.kind, start_date, end_date, category_ids)
    common = dict(currency=settings.currency, category_id=category.id, category_name=category.name,
                  category_color=category.color, category_icon=category.icon)
    status = merge_statuses(*(c.status for c in contributions))
    statuses = {name: status for name in ("total_amount", "average_per_month", "series")}
    if not contributions:
        # Preserve the existing empty-report contract, even with explicit bounds.
        return CategorySpendingReport(
            **common, start_date=start_date, end_date=end_date, total_amount=Decimal("0"),
            transaction_count=0, average_per_month=Decimal("0"), series=[], metric_status=statuses,
        )
    effective_start = start_date or min(c.day for c in contributions)
    effective_end = end_date or max(c.day for c in contributions)
    by_month: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    inputs: dict[tuple[int, int], list[FinancialDataStatus]] = defaultdict(list)
    for contribution in contributions:
        key = contribution.day.year, contribution.day.month
        inputs[key].append(contribution.status)
        if contribution.amount is not None:
            by_month[key] += contribution.amount
    series = []
    year, month = effective_start.year, effective_start.month
    while (year, month) <= (effective_end.year, effective_end.month):
        point_status = merge_statuses(*inputs[(year, month)])
        series.append(CategorySpendingPoint(
            year=year, month=month, amount=by_month[(year, month)] if point_status.complete else None,
            **point_status.model_dump(),
        ))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    total = sum((point.amount for point in series), Decimal("0")) if status.complete else None
    average = total / len(series) if total is not None and series else total
    return CategorySpendingReport(
        **common, **status.model_dump(), metric_status=statuses,
        start_date=effective_start, end_date=effective_end, total_amount=total,
        transaction_count=len({c.transaction_id for c in contributions}), average_per_month=average, series=series,
    )


async def get_category_ranking_report(
    session: AsyncSession, kind: CategoryKind, start_date: date_ | None, end_date: date_ | None,
) -> CategoryRankingReport:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _category_ranking(session, kind, start_date, end_date)


async def _category_ranking(
    session: AsyncSession, kind: CategoryKind, start_date: date_ | None, end_date: date_ | None,
) -> CategoryRankingReport:
    settings = await get_or_create_app_settings(session)
    fx = _DatedValues(session, settings.currency, settings.fx_max_age_days)
    contributions = await _category_contributions(session, fx, kind, start_date, end_date)
    status = merge_statuses(*(c.status for c in contributions))
    statuses = {"total_amount": status, "items": status}
    common = dict(currency=settings.currency, start_date=start_date, end_date=end_date,
                  metric_status=statuses, **status.model_dump())
    if not status.complete:
        return CategoryRankingReport(**common, total_amount=None, items=[])
    categories = {c.id: c for c in (await session.scalars(select(Category))).all()}
    rows = rollup_category_contributions(
        [(c.transaction_id, c.category_id, c.amount) for c in contributions], categories,
    )
    total = sum((row.amount for row in rows), Decimal("0"))
    items = [CategoryRankingItem(
        category_id=row.category_id, name=row.name, color=row.color, icon=row.icon,
        amount=row.amount, percent=float(row.amount / total * 100) if total else 0.0,
        transaction_count=row.transaction_count,
        children=[CategoryRankingChildItem(
            category_id=child.category_id, name=child.name, color=child.color, icon=child.icon, amount=child.amount,
        ) for child in row.children],
    ) for row in rows]
    return CategoryRankingReport(**common, total_amount=total, items=items)
