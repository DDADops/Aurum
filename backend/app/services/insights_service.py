"""Proactive early-warning checks, computed from data that already exists.
Five signals: sustained negative monthly cash flow, a sustained net-worth
decline, one or more over-budget categories, too much capital in medium/high
risk tiers (the "80% at zero risk, 20% at most exposed" rule), and cash
sitting idle in a depository account. The first two only look at
fully-elapsed calendar months, so a same-month false alarm (rent already
paid, salary not landed yet) never fires — their "sustained for how long"
thresholds, the risk-allocation percentage, and the idle-cash amount/days are
all user-configurable (Settings page), stored on AppSettings, see
get_or_create_app_settings. The budget check is the opposite: it deliberately
looks at the CURRENT, still-in-progress month, since the whole point is to
catch overspending while there's still time to react.
"""
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.enums import AccountType
from app.schemas.insights import AlertsResponse, FinancialAlert
from app.schemas.money import FinancialDataIssue, FinancialDataStatus
from app.schemas.net_worth import NetWorthPoint, NetWorthSummary
from app.services.budget_service import get_budget_status
from app.services.cash_flow_service import get_cash_flow
from app.services.financial_data_service import merge_statuses
from app.services.native_balance_service import NativeBalance, account_balances
from app.services.net_worth_service import get_net_worth_summary
from app.services.settings_service import get_or_create_app_settings

MAX_LOOKBACK_MONTHS = 24

# Accounts where a big, untouched balance means money isn't working — a
# credit card balance isn't "your cash", an investment account is already
# invested, and OTHER is too ambiguous to guess at.
_IDLE_CASH_ACCOUNT_TYPES = (AccountType.CHECKING, AccountType.DEBIT_CARD, AccountType.SAVINGS, AccountType.CASH)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


async def _negative_cash_flow_streak(
    session: AsyncSession, statuses: list[FinancialDataStatus] | None = None,
) -> int | None:
    statuses = statuses if statuses is not None else []
    today = date.today()
    year, month = today.year, today.month
    for _ in range(MAX_LOOKBACK_MONTHS):
        year, month = _previous_month(year, month)
    flow = await get_cash_flow(session, date(year, month, 1), today.replace(day=1) - timedelta(days=1))
    points = {(point.year, point.month): point for point in flow.points}
    year, month = _previous_month(today.year, today.month)
    streak = 0
    for _ in range(MAX_LOOKBACK_MONTHS):
        point = points.get((year, month))
        status = (merge_statuses(point.metric_status.get("net", point),
                                FinancialDataStatus(complete=point.net is not None))
                  if point is not None else FinancialDataStatus(complete=False))
        statuses.append(status)
        if not status.complete:
            return None
        if point.net >= 0:
            break
        streak += 1
        year, month = _previous_month(year, month)
    return streak


def _net_worth_decline_streak(
    summary: NetWorthSummary, statuses: list[FinancialDataStatus] | None = None,
) -> int | None:
    statuses = statuses if statuses is not None else []
    today = date.today()
    # Only month-end points participate; current month is still partial.
    month_end: dict[tuple[int, int], NetWorthPoint] = {}
    for point in summary.series:
        if (point.date.year, point.date.month) == (today.year, today.month):
            continue
        month_end[(point.date.year, point.date.month)] = point
    points = [month_end[key] for key in sorted(month_end, reverse=True)]
    streak = 0
    for index, point in enumerate(points):
        status = merge_statuses(point, FinancialDataStatus(complete=point.value is not None))
        statuses.append(status)
        if not status.complete:
            return None
        if index:
            if points[index - 1].value >= point.value:
                break
            streak += 1
    return streak


async def _idle_cash_account_count(
    session: AsyncSession, threshold_amount: Decimal, threshold_days: int, *, currency: str,
    statuses: list[FinancialDataStatus] | None = None,
) -> int | None:
    statuses = statuses if statuses is not None else []
    eligible = (await session.execute(
        select(Account.id, Account.currency).where(
            Account.is_archived.is_(False), Account.type.in_(_IDLE_CASH_ACCOUNT_TYPES),
        )
    )).all()
    if not eligible:
        return 0
    balances = await account_balances(session)
    cutoff = date.today() - timedelta(days=threshold_days)
    count = 0
    for account_id, native_currency in eligible:
        state = balances.get(account_id, NativeBalance())
        # No authoritative per-account converted balance is exposed yet.
        # A native balance cannot be compared with a foreign threshold.
        if state.balance is None:
            statuses.append(FinancialDataStatus(complete=False, data_issues=[
                FinancialDataIssue(code="transfer_amount_unresolved"),
            ]))
            continue
        if state.has_monetary_history and native_currency != currency:
            statuses.append(FinancialDataStatus(complete=False, data_issues=[
                FinancialDataIssue(code="idle_cash_balance_unavailable"),
            ]))
            continue
        if state.balance >= threshold_amount and (state.last_activity or cutoff) <= cutoff:
            count += 1
    return count if all(status.complete for status in statuses) else None


async def get_financial_alerts(session: AsyncSession) -> AlertsResponse:
    settings = await get_or_create_app_settings(session)
    metrics: dict[str, FinancialDataStatus] = {}
    alerts: list[FinancialAlert] = []

    inputs: list[FinancialDataStatus] = []
    cash_flow_streak = await _negative_cash_flow_streak(session, inputs)
    metrics["negative_cash_flow_streak"] = merge_statuses(*inputs)
    if cash_flow_streak is not None and cash_flow_streak >= settings.negative_cash_flow_threshold_months:
        alerts.append(
            FinancialAlert(
                key="negative_cash_flow_streak",
                severity="warning",
                params={"months": cash_flow_streak},
            )
        )

    net_worth_summary = await get_net_worth_summary(session, "all")

    inputs = []
    net_worth_streak = _net_worth_decline_streak(net_worth_summary, inputs)
    metrics["net_worth_decline_streak"] = merge_statuses(*inputs)
    if net_worth_streak is not None and net_worth_streak >= settings.net_worth_decline_threshold_months:
        alerts.append(
            FinancialAlert(
                key="net_worth_decline_streak",
                severity="warning",
                params={"months": net_worth_streak},
            )
        )

    metrics["risky_allocation_exceeded"] = merge_statuses(
        net_worth_summary.metric_status.get("risk_levels", net_worth_summary),
    )
    risky_percent = None
    if metrics["risky_allocation_exceeded"].complete:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            total = sum((tier.total_value for tier in net_worth_summary.risk_levels), Decimal(0))
            risky = sum((tier.total_value for tier in net_worth_summary.risk_levels
                         if tier.risk_level != "low"), Decimal(0))
            risky_percent = risky / total * 100 if total != 0 else Decimal(0)
    if risky_percent is not None and risky_percent > settings.risky_allocation_threshold_percent:
        alerts.append(
            FinancialAlert(
                key="risky_allocation_exceeded",
                severity="warning",
                params={"percent": round(risky_percent), "threshold": settings.risky_allocation_threshold_percent},
            )
        )

    today = date.today()
    budget_status = await get_budget_status(session, today.year, today.month)
    budget_inputs = [merge_statuses(item, FinancialDataStatus(complete=item.is_over_budget is not None))
                     for item in budget_status.items]
    metrics["budget_exceeded"] = merge_statuses(*budget_inputs)
    over_budget_count = sum(1 for item, status in zip(budget_status.items, budget_inputs)
                            if status.complete and item.is_over_budget)
    if over_budget_count > 0:
        alerts.append(
            FinancialAlert(
                key="budget_exceeded",
                severity="warning",
                params={"count": over_budget_count},
            )
        )

    inputs = []
    idle_cash_count = await _idle_cash_account_count(
        session, settings.idle_cash_threshold_amount, settings.idle_cash_threshold_days, currency=settings.currency,
        statuses=inputs,
    )
    metrics["idle_cash"] = merge_statuses(*inputs)
    if idle_cash_count is not None and idle_cash_count > 0:
        alerts.append(
            FinancialAlert(
                key="idle_cash",
                severity="warning",
                params={"count": idle_cash_count, "days": settings.idle_cash_threshold_days},
            )
        )

    return AlertsResponse(currency=settings.currency, alerts=alerts, metric_status=metrics,
                          **merge_statuses(*metrics.values()).model_dump())
