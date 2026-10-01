"""Reference FX foundation, tested against migrated temporary PostgreSQL."""
import builtins
import runpy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models.fx import FxRate
from app.schemas.fx import FxObservation
from app.schemas.money import MissingRate
from app.services.backup_service import build_backup
from app.services.fx_provider import FxProvider
from app.services.fx_service import ResolvedRate, convert, resolve_observations, resolve_rate, upsert_observation
from tests.helpers import txn_payload
from tests.test_monetary_precision import migrate

DAY = date(2026, 9, 20)
FETCHED = datetime(2026, 9, 30, tzinfo=timezone.utc)
PREVIOUS = "a72e9b4c106d"
REVISION = "c4e8a91b720f"


def obs(base="USD", quote="VND", rate="26000", age=0, **kwargs):
    return FxObservation(base_currency=base, quote_currency=quote, rate=Decimal(rate),
                         as_of_date=DAY - timedelta(days=age), source="fixture", fetched_at=FETCHED, **kwargs)


async def store(sessionmaker, *observations):
    async with sessionmaker() as session:
        for observation in observations:
            await upsert_observation(session, observation)
        await session.commit()


async def test_exact_storage_upsert_and_no_inverse(test_sessionmaker):
    value = obs(rate="99999999999999999999.123456789012345678")
    async with test_sessionmaker() as session:
        row = await upsert_observation(session, value)
        row_id = row.id
        await session.commit()
    async with test_sessionmaker() as session:
        row = await session.get(FxRate, row_id)
        assert row.rate == value.rate and isinstance(row.rate, Decimal)
        replacement = value.model_copy(update={"rate": Decimal("26000.123456789012345678"),
                                               "source": "replacement", "fetched_at": FETCHED + timedelta(days=1)})
        updated = await upsert_observation(session, replacement)
        await session.commit()
        assert updated is row
        assert row.rate == replacement.rate and row.source == "replacement"
        assert row.fetched_at == replacement.fetched_at
        assert await session.scalar(select(func.count()).select_from(FxRate)) == 1
        assert (row.base_currency, row.quote_currency) == ("USD", "VND")


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "-Infinity", "0", "-1", "1e20", "1e38", "0.1234567890123456789"])
def test_invalid_rates(rate):
    with pytest.raises(ValidationError):
        obs(rate=rate)


@pytest.mark.parametrize("changes", [{"base_currency": "ZZZ"}, {"quote_currency": "US"},
                                     {"base_currency": " USD"}, {"quote_currency": "USD"},
                                     {"rate": 1.25}, {"rate": "1.25"},
                                     {"fetched_at": datetime(2026, 9, 20)}])
def test_invalid_observation(changes):
    with pytest.raises(ValidationError):
        FxObservation.model_validate({**obs().model_dump(), **changes})


async def test_lowercase_normalizes_and_invalid_construct_cannot_write(test_sessionmaker):
    value = obs(base="usd", quote="vnd")
    assert value.base_currency == "USD" and value.quote_currency == "VND"
    async with test_sessionmaker() as session:
        with pytest.raises(ValidationError):
            await upsert_observation(session, value.model_copy(update={"rate": Decimal("0")}))
        assert await session.scalar(select(func.count()).select_from(FxRate)) == 0


@pytest.mark.parametrize("base,quote,rate", [("USD", "USD", "1"), ("USD", "VND", "0"),
                                             ("USD", "VND", "-1"), ("USD", "VND", "NaN")])
async def test_database_constraints(test_sessionmaker, base, quote, rate):
    async with test_sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text("""INSERT INTO fx_rates
                (base_currency,quote_currency,rate,as_of_date,source,fetched_at)
                VALUES (:base,:quote,:rate,:day,'raw',:fetched)"""),
                {"base": base, "quote": quote, "rate": Decimal(rate), "day": DAY, "fetched": FETCHED})
        await session.rollback()


async def test_unique_constraint_rejects_duplicate_raw_row(test_sessionmaker):
    await store(test_sessionmaker, obs())
    async with test_sessionmaker() as session:
        session.add(FxRate(**obs().model_dump()))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()


async def test_settings_api_and_database_validation(client, test_sessionmaker):
    assert (await client.get("/settings")).json()["fx_max_age_days"] == 7
    for invalid in (-1, None):
        assert (await client.patch("/settings", json={"fx_max_age_days": invalid})).status_code == 422
    response = await client.patch("/settings", json={"fx_max_age_days": 0})
    assert response.status_code == 200 and response.json()["fx_max_age_days"] == 0
    assert (await client.get("/settings")).json()["fx_max_age_days"] == 0
    async with test_sessionmaker() as session:
        with pytest.raises(IntegrityError):
            await session.execute(text("UPDATE app_settings SET fx_max_age_days=-1"))
        await session.rollback()
        await session.execute(text("DELETE FROM app_settings"))
        await session.commit()
    assert (await client.get("/settings")).json()["fx_max_age_days"] == 7


async def test_dated_database_lookup_and_priority(test_sessionmaker):
    await store(test_sessionmaker, obs(rate="26000", age=7), obs(rate="27000", age=0),
                obs(rate="28000", age=-1), obs("VND", "USD", "0.0001", age=0))
    async with test_sessionmaker() as session:
        result = await resolve_rate(session, "usd", "vnd", DAY, 7)
        assert result.kind == "direct" and result.rate == Decimal("27000")
        assert result.path == ("USD", "VND") and result.legs[0].observation.source == "fixture"
        older = await resolve_rate(session, "USD", "VND", DAY - timedelta(days=1), 7)
        assert older.rate == Decimal("26000") and older.staleness_days == 6
        missing = await resolve_rate(session, "USD", "VND", DAY - timedelta(days=8), 7)
        assert missing.reason == "absent"
    result = resolve_observations([obs(age=7), obs("VND", "USD", "0.0001")], "USD", "VND", DAY, 7)
    assert result.kind == "direct" and result.rate == Decimal("26000")


@pytest.mark.parametrize("age,limit,reason", [(7, 7, None), (8, 7, "stale"), (0, 0, None), (1, 0, "stale"), (-1, 7, "absent")])
def test_age_boundaries(age, limit, reason):
    result = resolve_observations([obs(age=age)], "USD", "VND", DAY, limit)
    if reason:
        assert result.reason == reason
    else:
        assert result.rate == Decimal("26000") and result.staleness_days == age


def test_inverse_precision_and_priority():
    observations = [obs("USD", "RUB", "3", age=7), obs("RUB", "EUR", "2"), obs("EUR", "USD", "4")]
    with localcontext() as context:
        context.prec = 6
        result = resolve_observations(observations, "RUB", "USD", DAY, 7)
    with localcontext() as context:
        context.prec = 96
        assert result.rate == Decimal(1) / Decimal(3)
    assert result.kind == "inverse" and result.legs[0].inverse
    assert len(result.rate.as_tuple().digits) == 96
    # A stale direct observation does not mask a fresh inverse.
    result = resolve_observations([obs("RUB", "USD", "2", age=8), *observations], "RUB", "USD", DAY, 7)
    assert result.kind == "inverse"


@pytest.mark.parametrize("inverse_first,inverse_second", [(False, False), (True, False), (False, True), (True, True)])
async def test_bridge_orientations(test_sessionmaker, inverse_first, inverse_second):
    first = obs("USD", "RUB", "2") if inverse_first else obs("RUB", "USD", "0.5")
    second = obs("VND", "USD", "0.25") if inverse_second else obs("USD", "VND", "4")
    await store(test_sessionmaker, first, second)
    async with test_sessionmaker() as session:
        result = await resolve_rate(session, "RUB", "VND", DAY, 7)
        assert result.kind == "bridge" and result.path == ("RUB", "USD", "VND")
        assert result.rate == Decimal("2")
        assert [leg.inverse for leg in result.legs] == [inverse_first, inverse_second]
        assert await session.scalar(select(func.count()).select_from(FxRate)) == 2


def test_bridge_selection_staleness_then_lexical_not_rate():
    observations = [obs("RUB", "USD", "10", 1), obs("USD", "VND", "10", 3),
                    obs("RUB", "EUR", "2", 2), obs("EUR", "VND", "2", 2)]
    result = resolve_observations(observations, "RUB", "VND", DAY, 7)
    assert result.path[1] == "EUR" and result.staleness_days == 2
    observations[1] = obs("USD", "VND", "100", 2)
    assert resolve_observations(reversed(observations), "RUB", "VND", DAY, 7).path[1] == "EUR"
    # Direct leg wins over a newer reverse leg, just as for whole pairs.
    observations.append(obs("EUR", "RUB", "100", 0))
    assert resolve_observations(observations, "RUB", "VND", DAY, 7).rate == Decimal(4)


@pytest.mark.parametrize("first_age,second_age", [(8, 0), (0, 8), (8, 8)])
def test_each_bridge_leg_must_be_fresh(first_age, second_age):
    result = resolve_observations([obs("RUB", "USD", "2", first_age), obs("USD", "VND", "3", second_age)],
                                  "RUB", "VND", DAY, 7)
    assert result.reason == "stale"


def test_absent_partial_future_and_three_leg_paths():
    for observations in ([], [obs("RUB", "USD", "2", 8)], [obs("RUB", "USD", "2"), obs(age=-1)],
                         [obs("RUB", "EUR", "2"), obs("EUR", "USD", "3"), obs()]):
        result = resolve_observations(observations, "RUB", "VND", DAY, 7)
        assert isinstance(result, MissingRate) and result.reason == "absent"
        assert result.base_currency == "RUB" and result.quote_currency == "VND"


async def test_same_currency_no_database_or_observations():
    session = AsyncMock()
    result = await resolve_rate(session, "usd", "USD", DAY, 0)
    assert result.rate == Decimal("1") and result.kind == "same" and result.legs == ()
    session.scalars.assert_not_called()


@pytest.mark.parametrize("rates", [("1e-18", "1e-18"), ("1e19", "1e19")])
def test_precision_unrepresentable_bridge(rates):
    result = resolve_observations([obs("RUB", "USD", rates[0]), obs("USD", "VND", rates[1])], "RUB", "VND", DAY, 7)
    assert result.reason == "precision_unrepresentable"


def test_precision_unrepresentable_inverse():
    result = resolve_observations([obs("USD", "RUB", "1e19")], "RUB", "USD", DAY, 7)
    assert result.reason == "precision_unrepresentable"


def test_conversion_keeps_decimal_precision():
    result = resolve_observations([obs("RUB", "USD", "0.123456789012345678"), obs("USD", "VND", "26000.123456789012345678")],
                                  "RUB", "VND", DAY, 7)
    amount = Decimal("123.123456")
    with localcontext() as context:
        context.prec = 96
        expected = amount * Decimal("0.123456789012345678") * Decimal("26000.123456789012345678")
    with localcontext() as context:
        context.prec = 6
        assert convert(amount, result) == expected
    assert isinstance(result.rate, Decimal) and isinstance(convert(amount, result), Decimal)
    for invalid in (1.25, "1.25", Decimal("NaN"), Decimal("Infinity")):
        with pytest.raises(ValueError):
            convert(invalid, result)


async def test_reference_fx_isolated_from_execution_and_reads(client, account_id, test_sessionmaker, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Ordinary reads must not fetch FX"))
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    await store(test_sessionmaker, obs())
    destination = (await client.post("/accounts", json={"name": "VND", "currency": "VND"})).json()["id"]
    payload = txn_payload(account_id, type="transfer", transfer_account_id=destination, amount="2")
    assert (await client.post("/transactions", json=payload)).status_code == 400
    response = await client.post("/transactions", json={**payload, "transfer_amount": "600"})
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["effective_rate"]) == 300
    async with test_sessionmaker() as session:
        resolved = await resolve_rate(session, "USD", "VND", DAY, 7)
        assert convert(Decimal(2), resolved) == 52000
    for endpoint in ("/transactions", "/dashboard/summary", "/net-worth/summary", "/cash-flow", "/reports/category-ranking"):
        response = await client.get(endpoint)
        assert response.status_code == 200, response.text
    forbidden.assert_not_called()


async def test_backup_v1_intentionally_defers_fx(test_sessionmaker):
    await store(test_sessionmaker, obs())
    async with test_sessionmaker() as session:
        payload = (await build_backup(session)).model_dump()
        assert payload["aurum_backup_version"] == 1
        assert "fx_rates" not in payload
        assert "fx_max_age_days" not in payload["app_settings"]


async def test_fx_migration_roundtrip(test_sessionmaker):
    migrate("downgrade", PREVIOUS)
    try:
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            assert await session.scalar(text("SELECT count(*) FROM app_settings")) == 1
        migrate("upgrade", REVISION)
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            assert await session.scalar(text("SELECT fx_max_age_days FROM app_settings")) == 7
            columns = (await session.execute(text("""SELECT numeric_precision,numeric_scale
                FROM information_schema.columns WHERE table_name='fx_rates' AND column_name='rate'"""))).one()
            assert tuple(columns) == (38, 18)
            constraints = set((await session.execute(text("SELECT conname FROM pg_constraint WHERE conrelid='fx_rates'::regclass"))).scalars())
            assert {"uq_fx_rates_pair_date", "ck_fx_rates_distinct_currencies", "ck_fx_rates_positive_finite"} <= constraints
        await store(test_sessionmaker, obs())
        migrate("downgrade", PREVIOUS)
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            assert await session.scalar(text("SELECT to_regclass('public.fx_rates')")) is None
            assert await session.scalar(text("""SELECT count(*) FROM information_schema.columns
                WHERE table_name='app_settings' AND column_name='fx_max_age_days'""")) == 0
        migrate("upgrade", REVISION)
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            # Expected destructive downgrade: observations are not resurrected.
            assert await session.scalar(select(func.count()).select_from(FxRate)) == 0
            assert await session.scalar(text("SELECT fx_max_age_days FROM app_settings")) == 7
    finally:
        migrate("upgrade", "head")


@pytest.mark.parametrize("max_age", [-1, 1.5, True])
async def test_invalid_max_age_fails_before_query(max_age):
    session = AsyncMock()
    with pytest.raises(ValueError):
        await resolve_rate(session, "USD", "VND", DAY, max_age)
    session.scalars.assert_not_called()


def test_unrepresentable_route_can_fall_back_to_representable_bridge():
    observations = [obs("VND", "RUB", "1e19"),
                    obs("RUB", "USD", "1e-18"), obs("USD", "VND", "1e-18"),
                    obs("RUB", "EUR", "2", 1), obs("EUR", "VND", "3", 1)]
    result = resolve_observations(observations, "RUB", "VND", DAY, 7)
    assert result.path == ("RUB", "EUR", "VND") and result.rate == Decimal(6)


def test_intermediate_inverse_is_not_quantized_or_clamped():
    result = resolve_observations([obs("USD", "RUB", "1e19"), obs("USD", "VND", "1e19")],
                                  "RUB", "VND", DAY, 7)
    assert result.kind == "bridge" and result.rate == Decimal(1)
    assert result.legs[0].rate() == Decimal("1e-19")


def test_fx_migration_does_not_import_runtime_modules(monkeypatch):
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "app" or name.startswith("app."):
            raise AssertionError("Historical migration must be self-contained")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    migration = runpy.run_path(str(Path(__file__).resolve().parents[1] / "alembic" / "versions" /
                                  "c4e8a91b720f_add_dated_reference_exchange_rates.py"))
    assert migration["revision"] == REVISION and migration["down_revision"] == PREVIOUS
