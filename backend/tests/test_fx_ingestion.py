"""Mocked OXR transport and real PostgreSQL atomic ingestion/locking."""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select, text

from app.models.fx import FxRate
from app.schemas.fx import FxObservation
from app.services import fx_ingestion_service as ingestion
from app.services.fx_providers import openexchangerates as oxr
from app.services.fx_service import upsert_observation
from scripts import sync_fx
from tests.test_native_transfers import wait_blocked

DAY = date(2020, 1, 2)
STAMP = int(datetime(2020, 1, 2, 23, 59, 59, tzinfo=timezone.utc).timestamp())
SECRET = "0123456789abcdef0123456789abcdef"


def body(rates='{"RUB": 61.123456789123456789, "VND": 23000}', **changes):
    data = dict(base="USD", timestamp=STAMP, **changes)
    return (json.dumps(data)[:-1] + ', "rates": ' + rates + '}').encode()


def obs(quote="RUB", rate="61.123456789123456789", day=DAY, source=oxr.SOURCE):
    return FxObservation(base_currency="USD", quote_currency=quote, rate=Decimal(rate),
                         as_of_date=day, source=source, fetched_at=datetime.now(timezone.utc))


def provider(rows):
    value = AsyncMock()
    value.fetch_observations.return_value = rows
    return value


async def run(sessions, rows, **kwargs):
    return await ingestion.ingest_rates(provider(rows), sessions, currencies=["RUB"],
                                       dates=[DAY], source=oxr.SOURCE, **kwargs)


async def stored(sessions):
    async with sessions() as session:
        return (await session.scalars(select(FxRate).order_by(FxRate.id))).all()


async def test_exact_parse_header_and_log_safety(caplog):
    caplog.set_level(logging.DEBUG)
    def handle(request):
        assert str(request.url) == "https://openexchangerates.org/api/historical/2020-01-02.json"
        assert request.headers["Authorization"] == f"Token {SECRET}"
        return httpx.Response(200, content=body())
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(handle))
    rows = await adapter.fetch_observations(currencies=["RUB", "VND"], dates=[DAY])
    assert rows[0].rate == Decimal("61.123456789123456789")
    assert rows[1].rate == Decimal(23000)
    assert all(r.source == oxr.SOURCE and r.as_of_date == DAY for r in rows)
    assert SECRET not in caplog.text


@pytest.mark.parametrize("content", [
    b'not JSON', b'[]', b'{"base":"USD","base":"USD"}',
    body('{"RUB":1,"RUB":2}'), body('{"RUB":0}'), body('{"RUB":-1}'),
    body('{"RUB":NaN}'), body('{"RUB":Infinity}'), body('{"RUB":true}'),
    body('{"RUB":"61.2"}'), body('{"RUB":0.0000000000000000001}'),
    body('{"RUB":100000000000000000000}'), body('[]'),
    body().replace(b'"USD"', b'"EUR"'), body().replace(str(STAMP).encode(), b'0'),
    body('{"USD":2,"RUB":1}'),
])
async def test_malformed_payload(content):
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)))
    with pytest.raises(oxr.ProviderError, match="invalid_provider_payload"):
        await adapter.fetch_observations(currencies=["RUB"], dates=[DAY])


@pytest.mark.parametrize("status,attempts,code", [(401, 1, "provider_authentication_failed"),
    (403, 1, "provider_authentication_failed"), (429, 3, "provider_rate_limited"),
    (500, 3, "provider_unavailable"), (503, 3, "provider_unavailable"),
    (404, 1, "provider_request_rejected"), (400, 1, "provider_request_rejected"), (302, 1, "provider_request_rejected")])
async def test_http_failures_bounded_and_sanitized(monkeypatch, status, attempts, code):
    sleep = AsyncMock()
    monkeypatch.setattr(oxr.asyncio, "sleep", sleep)
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status, text=SECRET, headers={"Retry-After": "99999", "Location": "https://example.com"})
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(handle))
    with pytest.raises(oxr.ProviderError) as exc:
        await adapter.fetch_observations(currencies=["RUB"], dates=[DAY])
    assert str(exc.value) == code and SECRET not in str(exc.value)
    assert len(calls) == attempts
    assert all(0 < call.args[0] <= 5 for call in sleep.call_args_list)


async def test_timeout_retry_and_transient_recovery(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(oxr.asyncio, "sleep", sleep)
    calls = 0
    def handle(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ReadTimeout(SECRET, request=request)
        return httpx.Response(200, content=body())
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(handle))
    assert len(await adapter.fetch_observations(currencies=["RUB"], dates=[DAY])) == 1
    assert calls == 3 and [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]
    def timeout(request):
        raise httpx.ReadTimeout(SECRET, request=request)
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(timeout))
    with pytest.raises(oxr.ProviderError, match="provider_transport_failure"):
        await adapter.fetch_observations(currencies=["RUB"], dates=[DAY])


async def test_missing_currency_and_whole_date_fail_before_session(test_sessionmaker):
    forbidden_session = lambda: pytest.fail("Must validate whole batch before DB access")
    for rows in ([obs()], []):
        report = await ingestion.ingest_rates(provider(rows), forbidden_session, currencies=["RUB", "VND"],
                                             dates=[DAY, DAY + timedelta(days=1)], source=oxr.SOURCE)
        assert report.exit_code == 4 and report.missing_observations == 4 - len(rows)
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body('{}'))))
    report = await ingestion.ingest_rates(adapter, forbidden_session, currencies=["RUB"], dates=[DAY], source=oxr.SOURCE)
    assert report.missing_observations == 1 and report.observations_inserted == 0


@pytest.mark.parametrize("rows", [[obs(), obs()], [obs(source="untrusted")], [obs(quote="EUR")],
    [obs(day=DAY + timedelta(days=1))], [obs().model_copy(update={"rate": Decimal("0")})]])
async def test_batch_revalidated_before_db(rows):
    report = await run(lambda: pytest.fail("DB accessed"), rows)
    assert report.exit_code == 3 and report.errors == ["invalid_observation_batch"]


async def test_rerun_preserves_id_rate_source_and_fetched_at(test_sessionmaker):
    first = obs()
    result = await run(test_sessionmaker, [first])
    assert result.observations_inserted == 1 and result.exit_code == 0
    before = (await stored(test_sessionmaker))[0]
    result = await run(test_sessionmaker, [obs()])
    assert result.identical_observations == 1 and result.observations_inserted == 0
    after = (await stored(test_sessionmaker))[0]
    assert (after.id, after.rate, after.source, after.fetched_at) == (before.id, before.rate, before.source, before.fetched_at)


@pytest.mark.parametrize("source,rate", [(oxr.SOURCE, "62"), ("other", "61.123456789123456789")])
async def test_conflicts_reject_whole_batch_and_preserve_provenance(test_sessionmaker, source, rate):
    async with test_sessionmaker() as session:
        await upsert_observation(session, obs(source=source, rate=rate))
        await session.commit()
    report = await ingestion.ingest_rates(provider([obs(), obs(quote="VND")]), test_sessionmaker,
                                         currencies=["RUB", "VND"], dates=[DAY], source=oxr.SOURCE)
    assert report.exit_code == 5 and report.conflicting_observations == 1
    assert report.observations_inserted == 0 and report.observations_to_insert == 1
    rows = await stored(test_sessionmaker)
    assert len(rows) == 1 and rows[0].source == source and rows[0].rate == Decimal(rate)


async def test_dry_run_and_rollback_on_second_insert(test_sessionmaker, monkeypatch):
    report = await run(test_sessionmaker, [obs()], dry_run=True)
    assert report.exit_code == 0 and report.observations_to_insert == 1 and report.observations_inserted == 0
    assert await stored(test_sessionmaker) == []
    original = ingestion.upsert_observation
    calls = 0
    async def fail(session, observation):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError(SECRET)
        return await original(session, observation)
    monkeypatch.setattr(ingestion, "upsert_observation", fail)
    report = await ingestion.ingest_rates(provider([obs(), obs(quote="VND")]), test_sessionmaker,
                                         currencies=["RUB", "VND"], dates=[DAY], source=oxr.SOURCE)
    assert report.exit_code == 6 and report.observations_inserted == 0
    assert SECRET not in str(report.as_dict()) and await stored(test_sessionmaker) == []


@pytest.mark.parametrize("rate,expected", [("61.123456789123456789", 0), ("62", 5)])
async def test_concurrent_jobs_cannot_bypass_policy(test_sessionmaker, monkeypatch, rate, expected):
    entered, release = asyncio.Event(), asyncio.Event()
    original = ingestion.upsert_observation
    pid = None
    async def pause(session, observation):
        nonlocal pid
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        entered.set()
        await release.wait()
        return await original(session, observation)
    monkeypatch.setattr(ingestion, "upsert_observation", pause)
    first = asyncio.create_task(run(test_sessionmaker, [obs()]))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(run(test_sessionmaker, [obs(rate=rate)]))
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        assert (await asyncio.wait_for(first, 5)).observations_inserted == 1
        result = await asyncio.wait_for(second, 5)
        assert result.exit_code == expected and result.observations_inserted == 0
        assert result.identical_observations == (1 if expected == 0 else 0)
        rows = await stored(test_sessionmaker)
        assert len(rows) == 1 and rows[0].rate == obs().rate
    finally:
        release.set()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


async def test_fetch_finishes_before_session_opens(test_sessionmaker):
    fetched = False
    class Provider:
        async def fetch_observations(self, **kwargs):
            nonlocal fetched
            fetched = True
            return [obs()]
    def sessions():
        assert fetched
        return test_sessionmaker()
    result = await ingestion.ingest_rates(Provider(), sessions, currencies=["RUB"], dates=[DAY], source=oxr.SOURCE)
    assert result.exit_code == 0


@pytest.mark.parametrize("args", [[], ["--secret", SECRET],
    ["--from", "2020-02-01", "--to", "2020-01-01"],
    ["--from", "2020-01-01", "--to", "2020-02-01"],
    ["--from", "2020-01-01", "--to", "2020-01-01", "--currencies", "BAD"],
    ["--from", "2020-01-01", "--to", "2020-01-01", "--currencies", "RUB,RUB"],
    ["--from", "2099-01-01", "--to", "2099-01-01"],
    ["--from", datetime.now(timezone.utc).date().isoformat(), "--to", datetime.now(timezone.utc).date().isoformat()],
])
async def test_cli_invalid_arguments_sanitized(args, capsys):
    assert await sync_fx.run_cli(args) == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert json.loads(captured.out)["errors"] == ["invalid_arguments_or_missing_credentials"]


async def test_cli_dry_run_json_and_commit(test_sessionmaker, monkeypatch, capsys):
    from app.db import session as db
    monkeypatch.setenv("AURUM_OXR_APP_ID", SECRET)
    monkeypatch.setattr(sync_fx, "OpenExchangeRatesProvider", lambda _: provider([obs()]))
    monkeypatch.setattr(db, "AsyncSessionLocal", test_sessionmaker)
    args = ["--from", DAY.isoformat(), "--to", DAY.isoformat(), "--currencies", "RUB"]
    assert await sync_fx.run_cli([*args, "--dry-run"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dates_requested"] == [DAY.isoformat()] and report["observations_inserted"] == 0
    assert await stored(test_sessionmaker) == []
    assert await sync_fx.run_cli(args) == 0
    assert json.loads(capsys.readouterr().out)["observations_inserted"] == 1


async def test_gets_never_invoke_adapter(client, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("No FX network in GET"))
    monkeypatch.setattr(oxr.OpenExchangeRatesProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    for path in ("/accounts", "/settings", "/dashboard/summary", "/net-worth/summary", "/cash-flow", "/reports/category-ranking"):
        assert (await client.get(path)).status_code == 200
    forbidden.assert_not_called()


async def test_uncommitted_existing_writer_is_seen_after_table_lock(test_sessionmaker):
    async with test_sessionmaker() as writer:
        await upsert_observation(writer, obs(rate="80", source="existing-writer"))
        pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(run(test_sessionmaker, [obs()]))
        try:
            await wait_blocked(test_sessionmaker, pid)
            await writer.commit()
            result = await asyncio.wait_for(task, 5)
            assert result.exit_code == 5 and result.conflicting_observations == 1
            row = (await stored(test_sessionmaker))[0]
            assert row.rate == Decimal(80) and row.source == "existing-writer"
        finally:
            await writer.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("scenario,code", [("provider", 3), ("missing", 4), ("conflict", 5), ("database", 6)])
async def test_cli_failure_codes_without_secret_disclosure(test_sessionmaker, monkeypatch, capsys, scenario, code):
    from app.db import session as db
    monkeypatch.setenv("AURUM_OXR_APP_ID", SECRET)
    adapter = provider([] if scenario == "missing" else [obs()])
    if scenario == "provider":
        adapter.fetch_observations.side_effect = oxr.ProviderError(SECRET)
    if scenario == "conflict":
        async with test_sessionmaker() as session:
            await upsert_observation(session, obs(rate="70"))
            await session.commit()
    if scenario == "database":
        monkeypatch.setattr(ingestion, "upsert_observation", AsyncMock(side_effect=RuntimeError(SECRET)))
    monkeypatch.setattr(sync_fx, "OpenExchangeRatesProvider", lambda _: adapter)
    monkeypatch.setattr(db, "AsyncSessionLocal", test_sessionmaker)
    result = await sync_fx.run_cli(["--from", DAY.isoformat(), "--to", DAY.isoformat(), "--currencies", "RUB"])
    assert result == code
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    report = json.loads(captured.out)
    assert report["exit_code"] == code and report["observations_inserted"] == 0


async def test_unsupported_request_and_missing_key_never_fetch(monkeypatch, capsys):
    adapter = oxr.OpenExchangeRatesProvider(SECRET, transport=httpx.MockTransport(lambda _: pytest.fail("No network")))
    with pytest.raises(ValueError):
        await adapter.fetch_observations(currencies=["BTC"], dates=[DAY])
    monkeypatch.delenv("AURUM_OXR_APP_ID", raising=False)
    assert await sync_fx.run_cli(["--from", DAY.isoformat(), "--to", DAY.isoformat()]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


async def test_dry_run_reports_conflict_without_upsert(test_sessionmaker, monkeypatch):
    async with test_sessionmaker() as session:
        await upsert_observation(session, obs(source="original"))
        await session.commit()
    forbidden = AsyncMock(side_effect=AssertionError("Dry-run cannot upsert"))
    monkeypatch.setattr(ingestion, "upsert_observation", forbidden)
    result = await run(test_sessionmaker, [obs()], dry_run=True)
    assert result.exit_code == 5 and result.conflicting_observations == 1
    assert result.observations_inserted == 0
    assert (await stored(test_sessionmaker))[0].source == "original"
    forbidden.assert_not_called()
