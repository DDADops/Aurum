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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.enums import AccountType
from app.schemas.insights import AlertsResponse, FinancialAlert
from app.schemas.money import FinancialDataIssue
from app.schemas.net_worth import NetWorthSummary
from app.services.budget_service import get_budget_status
from app.services.dashboard_service import get_dashboard_summary
from app.services.native_balance_service import NativeBalance, account_balances
from app.services.net_worth_service import get_net_worth_summary
from app.services.settings_service import get_or_create_app_settings

from app.services.financial_data_service import capital_status, transaction_status, merge_statuses

MAX_LOOKBACK_MONTHS = 24

# Accounts where a big, untouched balance means money isn't working — a
# credit card balance isn't "your cash", an investment account is already
# invested, and OTHER is too ambiguous to guess at.
_IDLE_CASH_ACCOUNT_TYPES = (AccountType.CHECKING, AccountType.DEBIT_CARD, AccountType.SAVINGS, AccountType.CASH)


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


async def _negative_cash_flow_streak(session: AsyncSession) -> int | None:
    today = date.today()
    year, month = _previous_month(today.year, today.month)

    streak = 0
    for _ in range(MAX_LOOKBACK_MONTHS):
        summary = await get_dashboard_summary(session, year, month)
        if summary.net is None or not summary.metric_status.get("net", summary).complete:
            return None
        if summary.net >= 0:
            break
        streak += 1
        year, month = _previous_month(year, month)
    return streak


def _net_worth_decline_streak(summary: NetWorthSummary) -> int | None:
    if any(not point.complete or point.value is None for point in summary.series):
        return None
    today = date.today()

    # Keep the last point seen for each (year, month) — `series` is ordered
    # ascending by date, so that's the month-end value — and drop the
    # current month, which is always partial.
    month_end: dict[tuple[int, int], Decimal] = {}
    for point in summary.series:
        if (point.date.year, point.date.month) == (today.year, today.month):
            continue
        month_end[(point.date.year, point.date.month)] = point.value

    ordered_months = sorted(month_end)
    if len(ordered_months) < 2:
        return 0

    streak = 0
    for i in range(len(ordered_months) - 1, 0, -1):
        if month_end[ordered_months[i]] < month_end[ordered_months[i - 1]]:
            streak += 1
        else:
            break
    return streak


async def _idle_cash_account_count(
    session: AsyncSession, threshold_amount: Decimal, threshold_days: int, *, currency: str,
) -> int | None:
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
        # Thresholds currently use settings currency. Conversion / explicit
        # threshold denomination belongs to the later FX insights work.
        if state.balance is None or (state.has_monetary_history and native_currency != currency):
            return None
        if state.balance >= threshold_amount and (state.last_activity or cutoff) <= cutoff:
            count += 1
    return count


async def get_financial_alerts(session: AsyncSession) -> AlertsResponse:
    settings = await get_or_create_app_settings(session)
    status = merge_statuses(
        await capital_status(session, settings.currency),
        await transaction_status(session, settings.currency),
    )
    if not status.complete:
        return AlertsResponse(currency=settings.currency, **status.model_dump(), alerts=[])
    alerts: list[FinancialAlert] = []

    cash_flow_streak = await _negative_cash_flow_streak(session)
    if cash_flow_streak is not None and cash_flow_streak >= settings.negative_cash_flow_threshold_months:
        alerts.append(
            FinancialAlert(
                key="negative_cash_flow_streak",
                severity="warning",
                params={"months": cash_flow_streak},
            )
        )

    net_worth_summary = await get_net_worth_summary(session, "all")

    net_worth_streak = _net_worth_decline_streak(net_worth_summary)
    if net_worth_streak is not None and net_worth_streak >= settings.net_worth_decline_threshold_months:
        alerts.append(
            FinancialAlert(
                key="net_worth_decline_streak",
                severity="warning",
                params={"months": net_worth_streak},
            )
        )

    risky_percent = (sum(tier.percent for tier in net_worth_summary.risk_levels if tier.risk_level != "low")
                     if net_worth_summary.metric_status["risk_levels"].complete else None)
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
    if not budget_status.complete:
        # A missing budget-target rate must not become a definitive zero count.
        return AlertsResponse(currency=settings.currency, **merge_statuses(budget_status).model_dump(), alerts=[])
    over_budget_count = sum(1 for item in budget_status.items if item.is_over_budget)
    if over_budget_count > 0:
        alerts.append(
            FinancialAlert(
                key="budget_exceeded",
                severity="warning",
                params={"count": over_budget_count},
            )
        )

    idle_cash_count = await _idle_cash_account_count(
        session, settings.idle_cash_threshold_amount, settings.idle_cash_threshold_days, currency=settings.currency
    )
    if idle_cash_count is None:
        return AlertsResponse(currency=settings.currency, complete=False, alerts=[], data_issues=[
            FinancialDataIssue(code="idle_cash_balance_unavailable"),
        ])
    if idle_cash_count > 0:
        alerts.append(
            FinancialAlert(
                key="idle_cash",
                severity="warning",
                params={"count": idle_cash_count, "days": settings.idle_cash_threshold_days},
            )
        )

    return AlertsResponse(currency=settings.currency, alerts=alerts)
