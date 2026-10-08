"""Atomic, conflict-rejecting ingestion; network precedes opening a DB session."""
from dataclasses import asdict, dataclass, field
from datetime import date
from collections.abc import Sequence

from sqlalchemy import select, text

from app.models.fx import FxRate
from app.schemas.fx import FxObservation
from app.services.fx_provider import FxProvider
from app.services.fx_providers.openexchangerates import ProviderError, validate_request
from app.services.fx_service import upsert_observation


@dataclass
class IngestionReport:
    dates_requested: list[str]
    dry_run: bool
    observations_inserted: int = 0
    observations_to_insert: int = 0
    identical_observations: int = 0
    conflicting_observations: int = 0
    missing_observations: int = 0
    errors: list[str] = field(default_factory=list)
    exit_code: int = 0

    def as_dict(self):
        return asdict(self)


async def ingest_rates(provider: FxProvider, session_factory, *, currencies: Sequence[str],
                       dates: Sequence[date], source: str, dry_run: bool = False) -> IngestionReport:
    quotes, days = validate_request(currencies, dates)
    report = IngestionReport([day.isoformat() for day in days], dry_run)
    expected = {("USD", quote, day) for quote in quotes for day in days}
    try:
        observations = await provider.fetch_observations(currencies=quotes, dates=days)
        accepted = {}
        for raw in observations:
            row = FxObservation.model_validate(raw.model_dump())
            key = row.base_currency, row.quote_currency, row.as_of_date
            if key not in expected or key in accepted or row.source != source:
                raise ValueError("unexpected_or_duplicate_observation")
            accepted[key] = row
        report.missing_observations = len(expected - accepted.keys())
    except ProviderError as exc:
        # ProviderError codes are fixed by the adapter, never HTTP response bodies.
        report.errors = [str(exc)]
        report.exit_code = 3
        return report
    except Exception:
        report.errors = ["invalid_observation_batch"]
        report.exit_code = 3
        return report
    if report.missing_observations:
        report.errors = ["incomplete_observation_batch"]
        report.exit_code = 4
        return report

    try:
        async with session_factory() as session:
            async with session.begin():
                if dry_run:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                else:
                    await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
                    await session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    # Serializes conflict-check + insert, including against writers
                    # that do not honor advisory locks and against backup restore.
                    # Readers remain allowed; no network is performed under this lock.
                    await session.execute(text("LOCK TABLE fx_rates IN SHARE ROW EXCLUSIVE MODE"))
                rows = (await session.scalars(select(FxRate).where(
                    FxRate.base_currency == "USD", FxRate.quote_currency.in_(quotes),
                    FxRate.as_of_date.in_(days)))).all()
                existing = {(r.base_currency, r.quote_currency, r.as_of_date): r for r in rows}
                new = []
                for key, observation in sorted(accepted.items()):
                    old = existing.get(key)
                    if old is None:
                        new.append(observation)
                    elif old.rate == observation.rate and old.source == observation.source:
                        report.identical_observations += 1
                    else:
                        report.conflicting_observations += 1
                report.observations_to_insert = len(new)
                if report.conflicting_observations:
                    report.errors = ["existing_observation_conflict"]
                    report.exit_code = 5
                    return report
                if not dry_run:
                    for observation in new:
                        await upsert_observation(session, observation)
            # Count committed inserts only. Identical rows retain fetched_at and ID.
            if not dry_run:
                report.observations_inserted = len(new)
    except Exception:
        report.errors = ["database_ingestion_failed"]
        report.exit_code = 6
    return report
