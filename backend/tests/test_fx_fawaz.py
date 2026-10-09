"""Fawaz transport contract and PostgreSQL ingestion regression coverage."""
import json
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.currency import SUPPORTED_CURRENCIES
from app.services.fx_providers import common, fawaz
from app.services import fx_ingestion_service as ingestion
from scripts import sync_fx
from tests.test_fx_ingestion import stored, obs, run

DAY = date(2024, 3, 6)


def payload(rates='"rub":61.123456789123456789', day=DAY):
    return ('{"date":"' + day.isoformat() + '","usd":{"usd":1,' + rates + '}}').encode()


def adapter(content):
    return fawaz.FawazProvider(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)))


async def test_all_supported_quotes_exact_decimal_no_auth():
    def handle(request):
        assert str(request.url) == 'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@2024-03-06/v1/currencies/usd.min.json'
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=payload(','.join(
            '"' + code.lower() + '":61.123456789123456789' for code in sorted(SUPPORTED_CURRENCIES - {'USD'}))))
    provider = fawaz.FawazProvider(transport=httpx.MockTransport(handle))
    rows = await provider.fetch_observations(currencies=sorted(SUPPORTED_CURRENCIES), dates=[DAY])
    assert {row.quote_currency for row in rows} == SUPPORTED_CURRENCIES - {'USD'}
    assert all(row.rate == Decimal('61.123456789123456789') and row.base_currency == 'USD'
               and row.as_of_date == DAY and row.source == fawaz.SOURCE for row in rows)


@pytest.mark.parametrize('value', ['0', '-1', 'true', 'null', '"1.2"', 'NaN', 'Infinity',
                                   '1e21', '0.0000000000000000001', '{}', '[]'])
async def test_invalid_rate(value):
    with pytest.raises(common.ProviderError, match='invalid_provider_payload'):
        await adapter(payload('"rub":' + value)).fetch_observations(currencies=['RUB'], dates=[DAY])


@pytest.mark.parametrize('content', [b'{}', b'null', b'[]', b'bad',
    payload().replace(b'2024-03-06', b'2024-03-07'),
    payload().replace(b'"usd":{', b'"eur":{'),
    payload().replace(b'"usd":1', b'"usd":2'),
    payload().replace(b'"usd":1', b'"usd":true'),
    payload('"rub":1,"rub":2')])
async def test_invalid_payload(content):
    with pytest.raises(common.ProviderError, match='invalid_provider_payload'):
        await adapter(content).fetch_observations(currencies=['RUB'], dates=[DAY])


@pytest.mark.parametrize('status,attempts', [(401,1), (403,1), (404,1), (302,1), (429,3), (500,3), (503,3)])
async def test_http_errors_no_latest_or_redirect(monkeypatch, status, attempts):
    sleep = AsyncMock()
    monkeypatch.setattr(common.asyncio, 'sleep', sleep)
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(status, headers={'Retry-After': '100', 'Location': 'https://example.com/latest'}, content=b'secret')
    provider = fawaz.FawazProvider(transport=httpx.MockTransport(handle))
    with pytest.raises(common.ProviderError) as exc:
        await provider.fetch_observations(currencies=['RUB'], dates=[DAY])
    assert 'secret' not in str(exc.value)
    assert len(calls) == attempts and all('@2024-03-06/' in url for url in calls)
    assert all(call.args == (5,) for call in sleep.call_args_list)


async def test_timeout_and_transient_recovery(monkeypatch):
    monkeypatch.setattr(common.asyncio, 'sleep', AsyncMock())
    count = 0
    def handle(request):
        nonlocal count
        count += 1
        if count < 3:
            raise httpx.ReadTimeout('secret')
        return httpx.Response(200, content=payload())
    provider = fawaz.FawazProvider(transport=httpx.MockTransport(handle))
    assert len(await provider.fetch_observations(currencies=['RUB'], dates=[DAY])) == 1
    assert count == 3
    def timeout(request):
        raise httpx.ReadTimeout('secret')
    with pytest.raises(common.ProviderError, match='provider_transport_failure'):
        await fawaz.FawazProvider(transport=httpx.MockTransport(timeout)).fetch_observations(currencies=['RUB'], dates=[DAY])


async def test_missing_currency_rejects_batch_before_database():
    report = await ingestion.ingest_rates(adapter(payload()), lambda: pytest.fail('DB opened'),
        currencies=['RUB', 'VND'], dates=[DAY], source=fawaz.SOURCE)
    assert report.exit_code == 4 and report.missing_observations == 1


async def test_cli_keyless_dry_run_write_idempotence(test_sessionmaker, monkeypatch, capsys):
    from app.db import session as db
    monkeypatch.setenv('AURUM_FX_PROVIDER', 'fawaz')
    monkeypatch.delenv('AURUM_OXR_APP_ID', raising=False)
    monkeypatch.setattr(sync_fx, 'FawazProvider', lambda: adapter(payload()))
    monkeypatch.setattr(db, 'AsyncSessionLocal', test_sessionmaker)
    args = ['--from', DAY.isoformat(), '--to', DAY.isoformat(), '--currencies', 'RUB']
    assert await sync_fx.run_cli([*args, '--dry-run']) == 0
    assert await stored(test_sessionmaker) == []
    assert await sync_fx.run_cli(args) == 0
    before = (await stored(test_sessionmaker))[0]
    assert before.source == fawaz.SOURCE
    assert await sync_fx.run_cli(args) == 0
    after = (await stored(test_sessionmaker))[0]
    assert (before.id, before.fetched_at, before.rate) == (after.id, after.fetched_at, after.rate)
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports[0]['observations_inserted'] == 0 and reports[-1]['identical_observations'] == 1


async def test_provider_switch_conflict_preserves_oxr(test_sessionmaker):
    from tests.test_fx_ingestion import DAY as OLD_DAY
    await run(test_sessionmaker, [obs()])
    report = await ingestion.ingest_rates(adapter(payload(day=OLD_DAY)), test_sessionmaker,
        currencies=['RUB'], dates=[OLD_DAY], source=fawaz.SOURCE)
    assert report.exit_code == 5
    assert (await stored(test_sessionmaker))[0].source == obs().source


async def test_unknown_provider_rejected(monkeypatch, capsys):
    monkeypatch.setenv('AURUM_FX_PROVIDER', 'unknown')
    assert await sync_fx.run_cli(['--from', DAY.isoformat(), '--to', DAY.isoformat()]) == 2
