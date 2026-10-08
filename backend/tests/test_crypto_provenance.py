"""Quote snapshots, immutable daily denominations and real PostgreSQL races."""
import asyncio
import builtins
import runpy
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding
from app.services import crypto_service as crypto
from app.services.valuation_service import write_valuation
from tests.test_crypto import _add_bitcoin, _fake_fetch, _point
from tests.test_monetary_precision import migrate
from tests.test_native_transfers import wait_blocked

PREVIOUS = "c4e8a91b720f"
REVISION = "e91b7a42c603"


async def valuations(client, id_):
    return (await client.get(f"/assets/{id_}/valuations")).json()


async def cache(sessions, id_):
    async with sessions() as session:
        return tuple((await session.execute(text("SELECT last_price,last_price_currency,last_price_as_of_date FROM crypto_holdings WHERE asset_id=:id"), {"id": id_})).one())


async def empty_holding(client, sessions, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("50000")}))
    id_ = (await _add_bitcoin(client))["asset_id"]
    async with sessions() as session:
        await session.execute(text("DELETE FROM asset_valuations WHERE asset_id=:id"), {"id": id_})
        await session.execute(text("DELETE FROM crypto_transactions WHERE asset_id=:id"), {"id": id_})
        await session.commit()
    return id_


async def test_migration_preserves_unknowns_and_roundtrip(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("50000")}))
    await _add_bitcoin(client)
    await client.post("/assets", json={"name": "House", "asset_class": "real_estate", "currency": "RUB", "value": "123.123456789123456789"})
    migrate("downgrade", PREVIOUS)
    try:
        async with test_sessionmaker() as session:
            await session.execute(text("UPDATE app_settings SET currency='VND'"))
            await session.commit()
        for _ in range(2):
            migrate("upgrade", REVISION)
            await test_sessionmaker.kw["bind"].dispose()
            async with test_sessionmaker() as session:
                assert (await session.execute(text("SELECT currency,currency_confirmed FROM asset_valuations ORDER BY id"))).all() == [(None, False), (None, False)]
                assert (await session.execute(text("SELECT last_price_currency,last_price_as_of_date FROM crypto_holdings"))).one() == (None, None)
                assert await session.scalar(text("SELECT quote_currency FROM crypto_transactions")) is None
                assert await session.scalar(text("SELECT value FROM asset_valuations WHERE asset_id=2")) == Decimal("123.123456789123456789")
                assert await session.scalar(text("SELECT count(*) FROM pg_constraint WHERE conname='uq_asset_valuation_date'")) == 1
                rows = (await session.execute(text("SELECT column_name,is_nullable FROM information_schema.columns WHERE table_name='asset_valuations' AND column_name IN ('currency','currency_confirmed')"))).all()
                assert set(rows) == {("currency", "YES"), ("currency_confirmed", "NO")}
                columns = (await session.execute(text("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE (table_name='crypto_transactions' AND column_name='quote_currency') OR (table_name='crypto_holdings' AND column_name IN ('last_price_currency','last_price_as_of_date'))"))).all()
                assert set(columns) == {("crypto_transactions", "quote_currency", "character varying", "YES"), ("crypto_holdings", "last_price_currency", "character varying", "YES"), ("crypto_holdings", "last_price_as_of_date", "date", "YES")}
                assert set((await session.scalars(text("SELECT column_name FROM information_schema.columns WHERE table_name='asset_valuations'"))).all()) == {"id", "asset_id", "value", "as_of_date", "currency", "currency_confirmed"}
            migrate("downgrade", PREVIOUS)
            async with test_sessionmaker() as session:
                assert await session.scalar(text("SELECT count(*) FROM asset_valuations")) == 2
                assert await session.scalar(text("SELECT count(*) FROM information_schema.columns WHERE table_name='asset_valuations' AND column_name IN ('currency','currency_confirmed')")) == 0
    finally:
        migrate("upgrade", "head")


def test_migration_is_independent_of_runtime(monkeypatch):
    import alembic  # Load third-party internals before guarding migration imports.
    original = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.startswith("app")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    migration = Path(__file__).parents[1] / "alembic/versions/e91b7a42c603_crypto_quote_provenance.py"
    module = runpy.run_path(str(migration))
    assert module["down_revision"] == PREVIOUS
    import ast
    tree = ast.parse(migration.read_text())
    assert {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} == {"alembic"}
    assert {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} == {"sqlalchemy"}
    source = migration.read_text()
    assert "app_settings" not in source and "UPDATE" not in source


async def test_transaction_snapshots_and_explicit_price_reentry(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({}))
    id_ = (await _add_bitcoin(client))["asset_id"]
    path = f"/crypto/holdings/{id_}/transactions"
    tx = (await client.get(path)).json()[0]
    assert tx["quote_currency"] == "USD"
    await client.patch("/settings", json={"currency": "VND"})
    assert (await client.patch(f"/crypto/transactions/{tx['id']}", json={"note": "Metadata"})).status_code == 200
    assert (await client.get(path)).json()[0]["quote_currency"] == "USD"
    assert (await client.patch(f"/crypto/transactions/{tx['id']}", json={"quote_currency": "VND"})).status_code == 422
    assert (await client.patch(f"/crypto/transactions/{tx['id']}", json={"price_per_unit": "40000"})).status_code == 200
    assert (await client.get(path)).json()[0]["quote_currency"] == "VND"
    body = {"type": "buy", "quantity": "1", "price_per_unit": "1.123456789123456789", "date": "2099-01-01", "quote_currency": "rub"}
    assert (await client.post(path, json=body)).status_code == 201
    rows = (await client.get(path)).json()
    assert rows[0]["quote_currency"] == "RUB"
    assert Decimal(rows[0]["price_per_unit"]) == Decimal(body["price_per_unit"])
    assert (await client.post(path, json={**body, "quote_currency": "BAD"})).status_code == 422
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE crypto_transactions SET quote_currency=NULL"))
        await session.commit()
    assert all(t["quote_currency"] is None for t in (await client.get(path)).json())
    assert (await client.patch(f"/assets/{id_}", json={"currency": "VND"})).status_code == 409


async def test_daily_refresh_atomic_conflict_and_next_day(client, test_sessionmaker, monkeypatch):
    requested = []
    async def fetch(ids, quote):
        requested.append(quote)
        return {"bitcoin": _point("1700000000" if quote == "vnd" else "50000")}
    monkeypatch.setattr(crypto, "_fetch_market_data", fetch)
    id_ = (await _add_bitcoin(client))["asset_id"]
    today = date.today()
    before_cache, before_rows = await cache(test_sessionmaker, id_), await valuations(client, id_)
    assert before_rows[0]["currency"] == "USD" and before_rows[0]["currency_confirmed"]
    assert (await client.post("/crypto/refresh")).status_code == 200
    await client.patch("/settings", json={"currency": "VND"})
    assert await cache(test_sessionmaker, id_) == before_cache
    response = await client.post("/crypto/refresh")
    assert response.status_code == 409 and response.json()["detail"] == "valuation_provenance_conflict"
    assert await cache(test_sessionmaker, id_) == before_cache
    assert await valuations(client, id_) == before_rows
    class Tomorrow(date):
        @classmethod
        def today(cls):
            return today + timedelta(days=1)
    monkeypatch.setattr(crypto, "date_", Tomorrow)
    assert (await client.post("/crypto/refresh")).status_code == 200
    rows = await valuations(client, id_)
    assert [r["currency"] for r in rows] == ["USD", "VND"]
    assert Decimal(rows[1]["value"]) == 850000000
    assert await cache(test_sessionmaker, id_) == (Decimal("1700000000"), "VND", Tomorrow.today())
    asset = (await client.get("/assets")).json()[0]
    assert asset["currency"] == "USD" and asset["valuation_currency"] == "VND"
    assert asset["valuation_currency_confirmed"] is True
    assert requested == ["usd", "usd", "vnd", "vnd"]
    assert (await client.get("/crypto/history")).status_code == 200


@pytest.mark.parametrize("legacy", [False, True])
async def test_manual_daily_provenance_conflicts(client, test_sessionmaker, legacy):
    created = await client.post("/assets", json={"name": "Native", "asset_class": "other", "currency": "RUB", "value": "400000"})
    id_ = created.json()["id"]
    path = f"/assets/{id_}/valuations"
    assert (await client.post(path, json={"value": "500000", "expected_currency": "RUB"})).status_code == 200
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE asset_valuations SET currency=:currency,currency_confirmed=:confirmed WHERE asset_id=:id"), {"id": id_, "currency": None if legacy else "USD", "confirmed": not legacy})
        await session.commit()
    before = await valuations(client, id_)
    response = await client.post(path, json={"value": "7", "expected_currency": "RUB"})
    assert response.status_code == 409 and response.json()["detail"] == "valuation_provenance_conflict"
    assert await valuations(client, id_) == before
    assert (await client.patch(f"/assets/{id_}", json={"currency": "VND"})).status_code == 409
    asset = (await client.get("/assets")).json()[0]
    assert asset["valuation_currency"] == (None if legacy else "USD")
    assert asset["valuation_currency_confirmed"] is (not legacy)
    summary = (await client.get("/net-worth/summary")).json()
    assert (summary["current"] is None and not summary["complete"]) if legacy else Decimal(summary["current"]) == 500000


async def test_legacy_refresh_conflict_and_backup_unknown(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("50000")}))
    id_ = (await _add_bitcoin(client))["asset_id"]
    backup = (await client.get("/backup/export")).json()
    backup["aurum_backup_version"] = 1
    assert (await client.post("/backup/import", json=backup)).status_code == 200
    before = await cache(test_sessionmaker, id_)
    assert before == (Decimal("50000"), None, None)
    rows = await valuations(client, id_)
    assert rows[0]["currency"] is None and rows[0]["currency_confirmed"] is False
    failure = await client.post("/crypto/refresh")
    assert failure.status_code == 409 and failure.json()["detail"] == "valuation_provenance_conflict"
    assert await cache(test_sessionmaker, id_) == before
    assert await valuations(client, id_) == rows


async def test_empty_holding_change_clears_all_cache(client, test_sessionmaker, monkeypatch):
    id_ = await empty_holding(client, test_sessionmaker, monkeypatch)
    response = await client.patch(f"/assets/{id_}", json={"currency": "VND"})
    assert response.status_code == 200
    assert await cache(test_sessionmaker, id_) == (None, None, None)
    async with test_sessionmaker() as session:
        holding = await session.get(CryptoHolding, id_)
        assert all(getattr(holding, f"price_change_{p}") is None for p in ("1h", "24h", "7d", "30d", "1y"))


async def test_http_has_no_row_locks_and_mutation_discards_response(client, test_sessionmaker, monkeypatch):
    id_ = await empty_holding(client, test_sessionmaker, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    async def fetch(ids, quote):
        entered.set()
        await release.wait()
        return {"bitcoin": _point("60000")}
    monkeypatch.setattr(crypto, "_fetch_market_data", fetch)
    request = asyncio.create_task(client.post("/crypto/refresh"))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # Would block if the provider request held either row lock.
        async with test_sessionmaker() as session:
            await session.execute(text("SELECT id FROM assets WHERE id=:id FOR UPDATE NOWAIT"), {"id": id_})
            await session.execute(text("SELECT asset_id FROM crypto_holdings WHERE asset_id=:id FOR UPDATE NOWAIT"), {"id": id_})
        response = await asyncio.wait_for(client.patch(f"/assets/{id_}", json={"currency": "VND"}), 5)
        assert response.status_code == 200
        release.set()
        failure = await asyncio.wait_for(request, 5)
        assert failure.status_code == 409 and failure.json()["detail"] == "crypto_refresh_state_changed_retry"
        assert await cache(test_sessionmaker, id_) == (None, None, None)
    finally:
        release.set()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("writer", ["transaction", "valuation"])
async def test_currency_change_wins_writer_lock(client, test_sessionmaker, monkeypatch, writer):
    id_ = await empty_holding(client, test_sessionmaker, monkeypatch)
    async with test_sessionmaker() as blocker:
        await blocker.execute(select(Asset).where(Asset.id == id_).with_for_update())
        pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        if writer == "transaction":
            task = asyncio.create_task(client.post(f"/crypto/holdings/{id_}/transactions", json={"type": "buy", "quantity": "1", "price_per_unit": "20", "date": "2099-01-01"}))
        else:
            task = asyncio.create_task(client.post(f"/assets/{id_}/valuations", json={"value": "0", "as_of_date": "2099-01-01", "expected_currency": "USD"}))
        try:
            await wait_blocked(test_sessionmaker, pid)
            await blocker.execute(text("UPDATE assets SET currency='RUB' WHERE id=:id"), {"id": id_})
            await blocker.execute(text("UPDATE crypto_holdings SET last_price=NULL,last_price_currency=NULL,last_price_as_of_date=NULL WHERE asset_id=:id"), {"id": id_})
            await blocker.commit()
            response = await asyncio.wait_for(task, 5)
            assert response.status_code == (201 if writer == "transaction" else 409)
            if writer == "valuation":
                assert response.json()["detail"] == "asset_currency_mismatch"
                assert await valuations(client, id_) == []
            else:
                # Quote is settings denomination, independent of native Asset currency.
                assert (await client.get(f"/crypto/holdings/{id_}/transactions")).json()[0]["quote_currency"] == "USD"
            assert (await client.patch(f"/assets/{id_}", json={"currency": "VND"})).status_code == (409 if writer == "transaction" else 200)
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("second_currency", ["USD", "VND"])
async def test_concurrent_daily_writers(client, test_sessionmaker, monkeypatch, second_currency):
    id_ = await empty_holding(client, test_sessionmaker, monkeypatch)
    async with test_sessionmaker() as first:
        await write_valuation(first, id_, Decimal("100"), date.today(), "USD")
        pid = await first.scalar(text("SELECT pg_backend_pid()"))
        async def second_write():
            async with test_sessionmaker() as second:
                try:
                    await write_valuation(second, id_, Decimal("200"), date.today(), second_currency)
                    await second.commit()
                    return 200
                except HTTPException as exc:
                    await second.rollback()
                    assert exc.detail == "valuation_provenance_conflict"
                    return exc.status_code
        task = asyncio.create_task(second_write())
        try:
            await wait_blocked(test_sessionmaker, pid)
            await first.commit()
            assert await asyncio.wait_for(task, 5) == (200 if second_currency == "USD" else 409)
        finally:
            await first.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    rows = await valuations(client, id_)
    assert len(rows) == 1 and rows[0]["currency"] == "USD" and rows[0]["currency_confirmed"]
    assert Decimal(rows[0]["value"]) == (200 if second_currency == "USD" else 100)


@pytest.mark.parametrize("second_quote", ["USD", "VND"])
async def test_two_refreshes_keep_atomic_tuples(client, test_sessionmaker, monkeypatch, second_quote):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({}))
    id_ = (await _add_bitcoin(client))["asset_id"]
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    count = 0
    async def fetch(ids, quote):
        nonlocal count
        index = count
        count += 1
        entered[index].set()
        await release[index].wait()
        return {"bitcoin": _point("100" if index == 0 else "200")}
    monkeypatch.setattr(crypto, "_fetch_market_data", fetch)
    first = asyncio.create_task(client.post("/crypto/refresh"))
    second = None
    try:
        await asyncio.wait_for(entered[0].wait(), 5)
        await client.patch("/settings", json={"currency": second_quote})
        second = asyncio.create_task(client.post("/crypto/refresh"))
        await asyncio.wait_for(entered[1].wait(), 5)
        release[0].set()
        assert (await asyncio.wait_for(first, 5)).status_code == 200
        # First request retains its fetched USD even if settings changed in flight.
        assert (await cache(test_sessionmaker, id_))[1] == "USD"
        release[1].set()
        response = await asyncio.wait_for(second, 5)
        assert response.status_code == (200 if second_quote == "USD" else 409)
        if second_quote != "USD":
            assert response.json()["detail"] == "valuation_provenance_conflict"
        expected = Decimal("200" if second_quote == "USD" else "100")
        assert await cache(test_sessionmaker, id_) == (expected, "USD", date.today())
        rows = await valuations(client, id_)
        assert len(rows) == 1 and Decimal(rows[0]["value"]) == expected / 2
        assert rows[0]["currency"] == "USD"
    finally:
        for event_ in release:
            event_.set()
        await asyncio.gather(*(t for t in (first, second) if t is not None), return_exceptions=True)


async def test_refresh_rolls_back_earlier_holdings_and_sync_state(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("50000")}))
    first = (await _add_bitcoin(client))["asset_id"]
    second = (await _add_bitcoin(client))["asset_id"]
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE asset_valuations SET currency=NULL,currency_confirmed=false WHERE asset_id=:id"), {"id": second})
        await session.commit()
        stamp = await session.scalar(text("SELECT last_synced_at FROM crypto_sync_state WHERE id=1"))
    before = [await cache(test_sessionmaker, id_) for id_ in (first, second)]
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("90000")}))
    failure = await client.post("/crypto/refresh")
    assert failure.status_code == 409
    assert [await cache(test_sessionmaker, id_) for id_ in (first, second)] == before
    assert Decimal((await valuations(client, first))[0]["value"]) == 25000
    async with test_sessionmaker() as session:
        assert await session.scalar(text("SELECT last_synced_at FROM crypto_sync_state WHERE id=1")) == stamp


async def test_provider_decimal_precision_and_seed_before_inserts(client, test_sessionmaker, monkeypatch):
    import httpx
    price = "12345678901234567890.123456789123456789"
    async def get(self, url, **kwargs):
        assert kwargs["params"]["vs_currency"] == "usd"
        async with test_sessionmaker() as session:
            assert await session.scalar(text("SELECT count(*) FROM assets")) == 0
        return httpx.Response(200, request=httpx.Request("GET", url),
            content=('[{"id":"bitcoin","current_price":' + price + '}]').encode())
    monkeypatch.setattr(crypto, "_require_api_key", lambda: "test")
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    # Use POST for API calls so only the provider GET is patched.
    response = await client.post("/crypto/holdings", json={"coingecko_id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "quantity": "1", "price_per_unit": "1"})
    assert response.status_code == 201
    id_ = response.json()["asset_id"]
    assert (await cache(test_sessionmaker, id_))[0] == Decimal(price)
    async with test_sessionmaker() as session:
        assert await session.scalar(select(AssetValuation.value).where(AssetValuation.asset_id == id_)) == Decimal(price)


async def test_refresh_write_phase_holds_parent_lock(client, test_sessionmaker, monkeypatch):
    id_ = await empty_holding(client, test_sessionmaker, monkeypatch)
    locked, release = asyncio.Event(), asyncio.Event()
    original = crypto._store_observation
    pid = None
    async def pause(session, *args):
        nonlocal pid
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        locked.set()
        await release.wait()
        return await original(session, *args)
    monkeypatch.setattr(crypto, "_store_observation", pause)
    refresh = asyncio.create_task(client.post("/crypto/refresh"))
    changer = None
    try:
        await asyncio.wait_for(locked.wait(), 5)
        changer = asyncio.create_task(client.patch(f"/assets/{id_}", json={"currency": "VND"}))
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        assert (await asyncio.wait_for(refresh, 5)).status_code == 200
        assert (await asyncio.wait_for(changer, 5)).status_code == 200
        assert await cache(test_sessionmaker, id_) == (None, None, None)
    finally:
        release.set()
        await asyncio.gather(*(t for t in (refresh, changer) if t is not None), return_exceptions=True)


async def test_unknown_and_mixed_quotes_never_become_known_aggregates(client, test_sessionmaker, monkeypatch):
    from sqlalchemy import event
    from unittest.mock import AsyncMock
    from app.services import fx_service
    from app.services.fx_provider import FxProvider
    forbidden = AsyncMock(side_effect=AssertionError("Crypto provenance must not use reference FX"))
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    engine = test_sessionmaker.kw["bind"].sync_engine
    def inspect(conn, cursor, statement, parameters, context, executemany):
        assert not ("fx_rates" in statement.lower() and statement.lstrip().lower().startswith(("insert", "update", "delete")))
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("50000")}))
        id_ = (await _add_bitcoin(client))["asset_id"]
        result = await client.post(f"/crypto/holdings/{id_}/transactions", json={"type": "buy", "quantity": "1", "price_per_unit": "10", "quote_currency": "RUB", "date": "2026-02-01"})
        assert result.status_code == 201
        assert result.json()["avg_buy_price"] is None
        assert result.json()["cost_basis"] is None and result.json()["profit_loss"] is None
        async with test_sessionmaker() as session:
            await session.execute(text("UPDATE crypto_holdings SET last_price_currency=NULL,last_price_as_of_date=NULL"))
            await session.execute(text("UPDATE asset_valuations SET currency=NULL,currency_confirmed=false"))
            await session.commit()
        result = (await client.get("/crypto/holdings")).json()["holdings"][0]
        assert result["current_price"] is None and result["value"] is None
        history = await client.get("/crypto/history")
        assert history.status_code == 200 and {"code": "valuation_currency_unconfirmed"} in history.json()["data_issues"]
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()
