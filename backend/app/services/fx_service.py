"""Local reference FX only. Execution transfer amounts never use this service.

Stored inputs are exact Numeric(38,18). Derived rates use 96 significant digits,
including repeating inverses, without scale quantization. Their permitted range
is [1e-18, 99999999999999999999.999999999999999999]; outside that envelope we
report precision_unrepresentable, never clamp or round to the storage scale.

Missing classification: a fresh complete route outside the range is precision
unrepresentable; otherwise a complete historical route with an old leg is stale;
otherwise absent. Future observations and disconnected partial routes do not
establish a historical route. Each leg prefers fresh direct over fresh inverse.
"""
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import SUPPORTED_CURRENCIES, normalize_currency
from app.models.fx import FxRate
from app.schemas.fx import FxObservation, require_decimal
from app.schemas.money import MissingRate

_MIN_RATE = Decimal("1e-18")
_MAX_RATE = Decimal("99999999999999999999.999999999999999999")


@dataclass(frozen=True)
class FxLeg:
    observation: FxObservation
    inverse: bool
    age_days: int

    def rate(self) -> Decimal:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            return Decimal(1) / self.observation.rate if self.inverse else self.observation.rate


@dataclass(frozen=True)
class ResolvedRate:
    rate: Decimal
    source_currency: str
    target_currency: str
    requested_date: date
    kind: Literal["same", "direct", "inverse", "bridge"]
    path: tuple[str, ...]
    legs: tuple[FxLeg, ...]
    max_age_days: int

    @property
    def staleness_days(self) -> int:
        return max((leg.age_days for leg in self.legs), default=0)


def _arguments(source: str, target: str, max_age_days: int) -> tuple[str, str]:
    if type(max_age_days) is not int or max_age_days < 0:
        raise ValueError("max_age_days must be a nonnegative integer")
    return normalize_currency(source), normalize_currency(target)


def resolve_observations(
    observations: Iterable[FxObservation], source_currency: str, target_currency: str,
    requested_date: date, max_age_days: int,
) -> ResolvedRate | MissingRate:
    """Bounded two-leg resolution; no persistence, provider or graph traversal."""
    source, target = _arguments(source_currency, target_currency, max_age_days)
    if source == target:
        return ResolvedRate(Decimal(1), source, target, requested_date, "same", (source,), (), max_age_days)
    latest: dict[tuple[str, str], FxObservation] = {}
    for observation in observations:
        key = observation.base_currency, observation.quote_currency
        if observation.as_of_date <= requested_date and (
            key not in latest or observation.as_of_date > latest[key].as_of_date
        ):
            latest[key] = observation

    def choices(base: str, quote: str) -> list[FxLeg]:
        return [
            FxLeg(obs, inverse, (requested_date - obs.as_of_date).days)
            for key, inverse in (((base, quote), False), ((quote, base), True))
            if (obs := latest.get(key)) is not None
        ]

    def fresh(legs: list[FxLeg]) -> FxLeg | None:
        return next((leg for leg in legs if leg.age_days <= max_age_days), None)

    def resolved(
        kind: Literal["direct", "inverse", "bridge"], path: tuple[str, ...], legs: tuple[FxLeg, ...],
    ) -> ResolvedRate | None:
        with localcontext() as context:
            context.prec = 96
            context.rounding = ROUND_HALF_EVEN
            rate = Decimal(1)
            for leg in legs:
                rate *= leg.rate()
        if not rate.is_finite() or not _MIN_RATE <= rate <= _MAX_RATE:
            return None
        return ResolvedRate(rate, source, target, requested_date, kind, path, legs, max_age_days)

    historical_dates: list[date] = []
    precision_failure = False
    for leg in choices(source, target):
        historical_dates.append(leg.observation.as_of_date)
        if leg.age_days <= max_age_days:
            result = resolved("inverse" if leg.inverse else "direct", (source, target), (leg,))
            if result is not None:
                return result
            precision_failure = True

    candidates: list[ResolvedRate] = []
    for bridge in sorted(SUPPORTED_CURRENCIES - {source, target}):
        first, second = choices(source, bridge), choices(bridge, target)
        if first and second:
            historical_dates.extend(leg.observation.as_of_date for leg in first + second)
        leg1, leg2 = fresh(first), fresh(second)
        if leg1 is not None and leg2 is not None:
            result = resolved("bridge", (source, bridge, target), (leg1, leg2))
            if result is not None:
                candidates.append(result)
            else:
                precision_failure = True
    if candidates:
        return min(candidates, key=lambda result: (result.staleness_days, result.path[1]))
    return MissingRate(
        base_currency=source, quote_currency=target, requested_date=requested_date,
        max_age_days=max_age_days,
        reason="precision_unrepresentable" if precision_failure else "stale" if historical_dates else "absent",
        latest_available_date=max(historical_dates, default=None),
    )


async def resolve_rate(
    session: AsyncSession, source_currency: str, target_currency: str,
    requested_date: date, max_age_days: int,
) -> ResolvedRate | MissingRate:
    source, target = _arguments(source_currency, target_currency, max_age_days)
    if source == target:
        return resolve_observations((), source, target, requested_date, max_age_days)
    # Only pairs touching an endpoint can contribute to a maximum two-leg path.
    rows = (await session.scalars(
        select(FxRate).where(
            FxRate.as_of_date <= requested_date,
            or_(FxRate.base_currency.in_((source, target)), FxRate.quote_currency.in_((source, target))),
        ).distinct(FxRate.base_currency, FxRate.quote_currency)
        .order_by(FxRate.base_currency, FxRate.quote_currency, FxRate.as_of_date.desc())
    )).all()
    return resolve_observations(
        (FxObservation.model_validate(row) for row in rows), source, target, requested_date, max_age_days,
    )


async def upsert_observation(session: AsyncSession, observation: FxObservation) -> FxRate:
    """Replace one accepted directed observation atomically; caller owns commit."""
    # Revalidate even model_construct / externally supplied model instances.
    validated = FxObservation.model_validate(observation.model_dump())
    statement = insert(FxRate).values(**validated.model_dump())
    statement = statement.on_conflict_do_update(
        constraint="uq_fx_rates_pair_date",
        set_={name: getattr(statement.excluded, name) for name in ("rate", "source", "fetched_at")},
    ).returning(FxRate)
    return (await session.scalars(statement, execution_options={"populate_existing": True})).one()


def convert(amount: Decimal, resolved: ResolvedRate) -> Decimal:
    """Reference conversion without money rounding; never resolves or fetches."""
    require_decimal(amount)
    require_decimal(resolved.rate)
    if not amount.is_finite():
        raise ValueError("Conversion amount must be finite")
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return amount * resolved.rate
