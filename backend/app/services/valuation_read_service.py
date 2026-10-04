"""Read-only dated reference values and forward-filled valuation observations."""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.money import FinancialDataIssue, FinancialDataStatus, MissingRate
from app.services import fx_service


def issue(code: str) -> FinancialDataStatus:
    return FinancialDataStatus(complete=False, data_issues=[FinancialDataIssue(code=code)])


class ReferenceValues:
    """One response's cache of resolved AND missing pair/date/age results."""

    def __init__(self, session: AsyncSession, currency: str, max_age_days: int, *,
                 resolver: Callable[..., Awaitable[fx_service.ResolvedRate | MissingRate]] | None = None):
        self.session = session
        self.currency = currency
        self.max_age_days = max_age_days
        self.resolver = resolver or fx_service.resolve_rate
        self.rates: dict[tuple[str, str, date, int], fx_service.ResolvedRate | MissingRate] = {}

    async def value(self, amount: Decimal, native: str, day: date) -> tuple[Decimal | None, FinancialDataStatus]:
        if amount == 0:
            return Decimal(0), FinancialDataStatus()
        key = (native, self.currency, day, self.max_age_days)
        if key not in self.rates:
            self.rates[key] = await self.resolver(self.session, *key)
        rate = self.rates[key]
        if isinstance(rate, MissingRate):
            return None, FinancialDataStatus(complete=False, missing_rates=[rate])
        return fx_service.convert(amount, rate), FinancialDataStatus()


@dataclass
class ValuationHistory:
    # Each event is (observation date, amount, currency, confirmation).
    events: list[tuple[date, Decimal, str | None, bool]]
    index: int = 0

    async def at(self, day: date, fx: ReferenceValues) -> tuple[Decimal | None, FinancialDataStatus]:
        if not self.events:
            return None, issue("valuation_missing")
        while self.index < len(self.events) and self.events[self.index][0] <= day:
            self.index += 1
        if self.index == 0:
            return Decimal(0), FinancialDataStatus()
        observed, amount, currency, confirmed = self.events[self.index - 1]
        if not confirmed or currency is None:
            return None, issue("valuation_currency_unconfirmed")
        # Carry the event's denomination AND FX date forward, never the chart date.
        return await fx.value(amount, currency, observed)
