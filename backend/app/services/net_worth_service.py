"""Net worth aggregation: a single trend line plus a percent breakdown by
asset class.

Design note: "Cash" is not stored anywhere — it's derived live from
Account/Transaction data (checking/debit-card/savings/cash/investment accounts) so the
Transactions feature stays the single source of truth for liquid money.
Investment accounts count here too: their balance is uninvested/unallocated
money sitting on the account, not the market value of what's actually
invested — that value is still tracked manually via Asset/AssetValuation,
same as every other class (investments, crypto, real estate, vehicles,
precious metals, other). Without this, money deposited into an investment
account but not yet turned into a valued Asset silently disappeared from
net worth. Both halves are collapsed into one
daily, forward-filled series so the chart reads as one continuous line even
though the two halves are updated at very different cadences.
"""
from collections import defaultdict
from datetime import date as date_
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import sum_money
from app.models.account import Account
from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding
from app.models.enums import AccountType, AssetClass, CapitalRole, RiskLevel
from app.models.transaction import Transaction
from app.schemas.money import FinancialDataIssue, FinancialDataStatus, MissingRate
from app.schemas.net_worth import (
    CapitalRoleSummary,
    NetWorthBreakdownItem,
    NetWorthPoint,
    NetWorthSummary,
    RiskLevelItem,
    RiskLevelSummary,
)
from app.services.financial_data_service import merge_statuses
from app.services.fx_service import ResolvedRate, convert, resolve_rate
from app.services.native_balance_service import native_legs
from app.services.settings_service import get_or_create_app_settings

CASH_ACCOUNT_TYPES = {
    AccountType.CHECKING,
    AccountType.DEBIT_CARD,
    AccountType.SAVINGS,
    AccountType.CASH,
    AccountType.INVESTMENT,
}

RANGE_DAYS = {"30d": 30, "90d": 90, "1y": 365, "5y": 365 * 5}

_CLASS_META: dict[str, tuple[str, str, str]] = {
    # key -> (display name, color, lucide icon). Colors follow the dataviz
    # skill's validated 8-slot order (blue, orange, aqua, yellow, magenta,
    # green, violet, red) in this exact sequence — an adjacent-pair-safe
    # order for a segmented bar/donut; do not reorder without re-validating.
    "cash": ("Наличные", "#2a78d6", "wallet"),  # slot 1 blue
    AssetClass.INVESTMENTS.value: ("Инвестиции", "#eb6834", "trending-up"),  # slot 2 orange
    AssetClass.CRYPTO.value: ("Криптовалюта", "#1baf7a", "bitcoin"),  # slot 3 aqua
    AssetClass.REAL_ESTATE.value: ("Недвижимость", "#eda100", "building-2"),  # slot 4 yellow
    AssetClass.VEHICLES.value: ("Транспорт", "#e87ba4", "car"),  # slot 5 magenta
    AssetClass.PRECIOUS_METALS.value: ("Драгметаллы", "#008300", "gem"),  # slot 6 green
    AssetClass.OTHER.value: ("Прочее", "#4a3aa7", "package"),  # slot 7 violet
}

_ROLE_META: dict[CapitalRole, tuple[str, str]] = {
    # role -> (label, color). Status colors, not categorical ones — "good"
    # and "critical" from the dataviz skill's fixed status palette, reused
    # verbatim from how StatCard/NetWorthChart already color income vs.
    # expense elsewhere in the app.
    CapitalRole.INCOME: ("Доходный", "var(--success)"),
    CapitalRole.NEUTRAL: ("Нейтральный", "var(--text-muted)"),
    CapitalRole.DRAIN: ("Убыточный", "var(--danger)"),
}

_RISK_META: dict[RiskLevel, tuple[str, str]] = {
    # Same status-color reuse as _ROLE_META — low risk reads as "good",
    # high risk as "critical", not a categorical hue.
    RiskLevel.LOW: ("Низкий", "var(--success)"),
    RiskLevel.MEDIUM: ("Средний", "var(--text-muted)"),
    RiskLevel.HIGH: ("Высокий", "var(--danger)"),
}


def _issue(code: str) -> FinancialDataStatus:
    return FinancialDataStatus(complete=False, data_issues=[FinancialDataIssue(code=code)])


class _ReferenceValues:
    """Request-local FX cache, including missing results. Never fetches providers."""

    def __init__(self, session: AsyncSession, currency: str, max_age_days: int):
        self.session = session
        self.currency = currency
        self.max_age_days = max_age_days
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


async def _cash_cumulative_events(
    session: AsyncSession, account_ids: set[int] | None = None, *, through: date_ | None = None,
) -> dict[int, list[tuple[date_, Decimal | None]]]:
    """Per-account native stocks, never a sum across account currencies."""
    if account_ids is None:
        account_ids = set((await session.scalars(select(Account.id).where(Account.type.in_(CASH_ACCOUNT_TYPES)))).all())
    statement = select(
        Transaction.date, Transaction.type, Transaction.amount, Transaction.account_id,
        Transaction.transfer_account_id, Transaction.transfer_amount,
    ).order_by(Transaction.date, Transaction.id)
    if through is not None:
        statement = statement.where(Transaction.date <= through)
    events: dict[int, list[tuple[date_, Decimal | None]]] = defaultdict(list)
    for day, kind, amount, source, destination, transfer_amount in await session.execute(statement):
        for account_id, delta in native_legs(kind, amount, source, destination, transfer_amount):
            if account_id not in account_ids:
                continue
            previous = events[account_id][-1][1] if events[account_id] else Decimal("0")
            balance = None if previous is None or delta is None else sum_money((previous, delta))
            events[account_id].append((day, balance))
    return dict(events)


@dataclass
class _History:
    events: list[tuple[date_, Decimal | None]]
    index: int = 0
    value: Decimal | None = Decimal("0")

    def at(self, day: date_) -> Decimal | None:
        """Forward-fill native state as the chart advances in calendar order."""
        while self.index < len(self.events) and self.events[self.index][0] <= day:
            self.value = self.events[self.index][1]
            self.index += 1
        return self.value


def _percent(amount: Decimal, denominator: Decimal) -> float:
    # All ratio arithmetic is Decimal. Only the existing percentage wire fields
    # are floats; converted monetary amounts never pass through float.
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return float(amount / denominator * Decimal("100")) if denominator != 0 else 0.0


def _breakdown(assets: list[Asset], values: dict[int, Decimal], cash: Decimal) -> list[NetWorthBreakdownItem]:
    by_class: dict[str, Decimal] = {"cash": cash}
    for asset in assets:
        key = asset.asset_class.value
        by_class[key] = sum_money((by_class.get(key, Decimal("0")), values[asset.id]))
    total = sum_money(by_class.values())
    return [NetWorthBreakdownItem(
        key=key, name=name, color=color, icon=icon,
        amount=by_class.get(key, Decimal("0")), percent=_percent(by_class.get(key, Decimal("0")), total),
    ) for key, (name, color, icon) in _CLASS_META.items()]


async def _capital_role_summary(
    assets: list[Asset], values: dict[int, Decimal], fx: _ReferenceValues, today: date_,
) -> tuple[list[CapitalRoleSummary], FinancialDataStatus]:
    # Preserve the existing asset-only role scope; accounts have no capital role.
    totals: dict[CapitalRole, Decimal] = defaultdict(Decimal)
    flows: dict[CapitalRole, Decimal] = defaultdict(Decimal)
    counts: dict[CapitalRole, int] = defaultdict(int)
    statuses = []
    for asset in assets:
        totals[asset.capital_role] = sum_money((totals[asset.capital_role], values[asset.id]))
        amount = asset.monthly_cash_flow if asset.monthly_cash_flow is not None else Decimal("0")
        flow, status = await fx.value(amount, asset.currency, today)
        statuses.append(status)
        if flow is not None:
            flows[asset.capital_role] = sum_money((flows[asset.capital_role], flow))
        counts[asset.capital_role] += 1
    status = merge_statuses(*statuses)
    if not status.complete:
        return [], status
    return [CapitalRoleSummary(
        role=role.value, label=_ROLE_META[role][0], color=_ROLE_META[role][1],
        total_value=totals[role], monthly_cash_flow=flows[role], count=counts[role],
    ) for role in CapitalRole], status


def _risk_level_summary(assets: list[Asset], values: dict[int, Decimal], cash: Decimal) -> list[RiskLevelSummary]:
    items: dict[RiskLevel, list[tuple[str, str, Decimal]]] = defaultdict(list)
    if cash != 0:
        items[RiskLevel.LOW].append(("cash", _CLASS_META["cash"][0], cash))
    for asset in assets:
        value = values[asset.id]
        if value != 0:
            items[asset.risk_level].append((f"asset:{asset.id}", asset.name, value))
    totals = {level: sum_money(value for _, _, value in items[level]) for level in RiskLevel}
    total = sum_money(totals.values())
    return [RiskLevelSummary(
        risk_level=level.value, label=_RISK_META[level][0], color=_RISK_META[level][1],
        total_value=totals[level], percent=_percent(totals[level], total),
        items=[RiskLevelItem(key=key, name=name, amount=value, percent=_percent(value, totals[level]))
               for key, name, value in sorted(items[level], key=lambda item: item[2], reverse=True)],
    ) for level in RiskLevel]


async def get_net_worth_summary(session: AsyncSession, range_key: str) -> NetWorthSummary:
    # Covers stock reconstruction, aggregation and endpoint subtraction even if
    # a caller uses a smaller ambient Decimal precision. No monetary quantize.
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _value_net_worth(session, range_key)


async def _value_net_worth(session: AsyncSession, range_key: str) -> NetWorthSummary:
    today = date_.today()
    settings = await get_or_create_app_settings(session)
    fx = _ReferenceValues(session, settings.currency, settings.fx_max_age_days)
    # Archive inclusion and account-type scope are unchanged.
    accounts = list((await session.scalars(select(Account).where(Account.type.in_(CASH_ACCOUNT_TYPES)))).all())
    assets = list((await session.scalars(select(Asset).order_by(Asset.id))).all())
    crypto_ids = set((await session.scalars(select(CryptoHolding.asset_id))).all())
    cash_events = await _cash_cumulative_events(session, {account.id for account in accounts}, through=today)
    valuations: dict[int, list[tuple[date_, Decimal | None]]] = defaultdict(list)
    valuation_provenance = defaultdict(list)
    for asset_id, day, value, currency, confirmed in await session.execute(select(
        AssetValuation.asset_id, AssetValuation.as_of_date, AssetValuation.value,
        AssetValuation.currency, AssetValuation.currency_confirmed,
    ).order_by(AssetValuation.as_of_date, AssetValuation.id)):
        valuations[asset_id].append((day, value))
        valuation_provenance[asset_id].append((currency, confirmed))
    dates = [day for events in (*cash_events.values(), *valuations.values()) for day, _ in events if day <= today]
    start = today - timedelta(days=RANGE_DAYS[range_key] - 1) if range_key in RANGE_DAYS else min(dates, default=today)
    cash_history = {account.id: _History(cash_events.get(account.id, [])) for account in accounts}
    asset_history = {asset.id: _History(valuations[asset.id]) for asset in assets}
    series: list[NetWorthPoint] = []
    day = start
    while day <= today:
        statuses = []
        cash_values: list[Decimal] = []
        asset_values: dict[int, Decimal] = {}
        for account in accounts:
            native = cash_history[account.id].at(day)
            if native is None:
                statuses.append(_issue("transfer_amount_unresolved"))
                continue
            value, status = await fx.value(native, account.currency, day)
            statuses.append(status)
            if value is not None:
                cash_values.append(value)
        for asset in assets:
            # No history is unknown, as before. With a dated history, the asset
            # enters the chart at its first valuation; earlier native value is 0.
            if not valuations[asset.id]:
                statuses.append(_issue("valuation_missing"))
            if asset.id in crypto_ids:
                # Legacy synced valuations lack historical quote provenance.
                statuses.append(_issue("valuation_currency_unconfirmed"))
                continue
            if not valuations[asset.id]:
                continue
            native = asset_history[asset.id].at(day)
            index = asset_history[asset.id].index
            if index:
                currency, confirmed = valuation_provenance[asset.id][index - 1]
                # Stage 1 only: never treat unknown/different quote as native.
                # Per-observation conversion and crypto integration are Stage 2.
                if not confirmed or currency != asset.currency:
                    statuses.append(_issue("valuation_currency_unconfirmed"))
                    continue
            value, status = await fx.value(native, asset.currency, day)
            statuses.append(status)
            if value is not None:
                asset_values[asset.id] = value
        status = merge_statuses(*statuses)
        cash = sum_money(cash_values)
        value = sum_money((cash, *asset_values.values())) if status.complete else None
        series.append(NetWorthPoint(date=day, value=value, **status.model_dump()))
        day += timedelta(days=1)

    current = series[-1]
    current_status = FinancialDataStatus(**current.model_dump(exclude={"date", "value"}))
    start_status = FinancialDataStatus(**series[0].model_dump(exclude={"date", "value"}))
    change_status = merge_statuses(start_status, current_status)
    change = current.value - series[0].value if change_status.complete else None
    change_percent = _percent(change, series[0].value) if change is not None and series[0].value != 0 else None
    metrics = {"current": current_status, "change_amount": change_status, "change_percent": change_status,
               "breakdown": current_status, "capital_roles": current_status, "risk_levels": current_status}
    breakdown, roles, risk = [], [], []
    if current.complete:
        breakdown = _breakdown(assets, asset_values, cash)
        roles, metrics["capital_roles"] = await _capital_role_summary(assets, asset_values, fx, today)
        risk = _risk_level_summary(assets, asset_values, cash)
    return NetWorthSummary(
        currency=settings.currency, range=range_key, current=current.value, **current_status.model_dump(),
        metric_status=metrics, change_amount=change, change_percent=change_percent,
        series=series, breakdown=breakdown, capital_roles=roles, risk_levels=risk,
    )
