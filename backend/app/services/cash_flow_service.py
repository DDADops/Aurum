"""Month-by-month income vs. expense — the multi-month view neither the
Dashboard (locked to one month) nor Reports (one category at a time, or a
category ranking) answers on its own. Transfers between the user's own
accounts are excluded from both totals, same as the Dashboard breakdown.
"""
from collections import defaultdict
from datetime import date as date_
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.cash_flow import CashFlowPoint, CashFlowResponse
from app.schemas.money import FinancialDataStatus, MissingRate
from app.services.financial_data_service import merge_statuses
from app.services.fx_service import ResolvedRate, convert, resolve_rate
from app.services.settings_service import get_or_create_app_settings


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


class _DatedValues:
    """Request-local dated conversion only; no bucket policy or provider I/O."""

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


async def get_cash_flow(
    session: AsyncSession, start_date: date_ | None, end_date: date_ | None,
) -> CashFlowResponse:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _dated_cash_flow(session, start_date, end_date)


async def _dated_cash_flow(
    session: AsyncSession, start_date: date_ | None, end_date: date_ | None,
) -> CashFlowResponse:
    settings = await get_or_create_app_settings(session)
    bounds_stmt = select(func.min(Transaction.date), func.max(Transaction.date)).where(
        Transaction.type != TransactionType.TRANSFER
    )
    if start_date:
        bounds_stmt = bounds_stmt.where(Transaction.date >= start_date)
    if end_date:
        bounds_stmt = bounds_stmt.where(Transaction.date <= end_date)
    min_date, max_date = (await session.execute(bounds_stmt)).one()
    effective_start, effective_end = start_date or min_date, end_date or max_date
    if effective_start is None or effective_end is None:
        return CashFlowResponse(
            currency=settings.currency, start_date=effective_start, end_date=effective_end,
            points=[], total_income=Decimal("0"), total_expense=Decimal("0"), total_net=Decimal("0"),
            metric_status={name: FinancialDataStatus() for name in ("total_income", "total_expense", "total_net")},
        )

    fx = _DatedValues(session, settings.currency, settings.fx_max_age_days)
    amounts: dict[tuple[int, int, str], Decimal] = defaultdict(Decimal)
    inputs: dict[tuple[int, int, str], list[FinancialDataStatus]] = defaultdict(list)
    rows = await session.execute(select(
        Transaction.date, Transaction.type, Transaction.amount, Account.currency,
    ).join(Account, Account.id == Transaction.account_id).where(
        Transaction.type != TransactionType.TRANSFER,
        Transaction.date >= effective_start, Transaction.date <= effective_end,
    ).order_by(Transaction.date, Transaction.id))
    for day, kind, amount, native in rows:
        metric = "income" if kind == TransactionType.INCOME else "expense"
        key = day.year, day.month, metric
        converted, status = await fx.value(amount, native, day)
        inputs[key].append(status)
        if converted is not None:
            amounts[key] += converted

    points: list[CashFlowPoint] = []
    year, month = effective_start.year, effective_start.month
    while (year, month) <= (effective_end.year, effective_end.month):
        statuses = {name: merge_statuses(*inputs[(year, month, name)]) for name in ("income", "expense")}
        statuses["net"] = merge_statuses(statuses["income"], statuses["expense"])
        income = amounts[(year, month, "income")] if statuses["income"].complete else None
        expense = amounts[(year, month, "expense")] if statuses["expense"].complete else None
        points.append(CashFlowPoint(
            year=year, month=month, income=income, expense=expense,
            net=income - expense if statuses["net"].complete else None,
            metric_status=statuses, **statuses["net"].model_dump(),
        ))
        year, month = _next_month(year, month)

    # Totals consume the exact same converted buckets, never a second FX path.
    statuses = {f"total_{name}": merge_statuses(*(p.metric_status[name] for p in points))
                for name in ("income", "expense", "net")}
    totals = {f"total_{name}": sum((getattr(p, name) for p in points), Decimal("0"))
              if statuses[f"total_{name}"].complete else None for name in ("income", "expense", "net")}
    return CashFlowResponse(
        currency=settings.currency, start_date=effective_start, end_date=effective_end,
        points=points, metric_status=statuses, **merge_statuses(*statuses.values()).model_dump(), **totals,
    )
