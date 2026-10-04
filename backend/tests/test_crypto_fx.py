"""Dated BUY/cache/valuation FX, independent metrics and strictly local GETs."""
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding, CryptoPortfolio, CryptoTransaction
from app.models.enums import AssetClass, CryptoTransactionType
from app.schemas.fx import FxObservation
from app.services import crypto_service as crypto, fx_service, net_worth_service as nw
from app.services.fx_provider import FxProvider
from app.services.insights_service import _net_worth_decline_streak
from app.schemas.net_worth import NetWorthPoint, NetWorthSummary

D1, D2, D3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return D3
    monkeypatch.setattr(crypto, "date_", FixedDate)
    monkeypatch.setattr(nw, "date_", FixedDate)


async def rate(sessions, source, target, value, day=D1):
    async with sessions() as session:
        await fx_service.upsert_observation(session, FxObservation(base_currency=source, quote_currency=target,
            rate=Decimal(value), as_of_date=day, source="fixture", fetched_at=datetime(2026, 1, 3, tzinfo=timezone.utc)))
        await session.commit()


async def holding(sessions, buys=None, price="60000", price_currency="USD", price_date=D3, observations=()):
    # Direct fixtures isolate read semantics from Stage 1's unchanged writer rules.
    if buys is None:
        buys = [("buy", "1", "50000", "USD", D1)]
    async with sessions() as session:
        portfolio = CryptoPortfolio(name="Portfolio")
        asset = Asset(name="BTC", asset_class=AssetClass.CRYPTO, currency="USD")
        session.add_all([portfolio, asset])
        await session.flush()
        session.add(CryptoHolding(asset_id=asset.id, portfolio_id=portfolio.id, coingecko_id="bitcoin",
            symbol="BTC", name="BTC", last_price=Decimal(price) if price is not None else None,
            last_price_currency=price_currency, last_price_as_of_date=price_date))
        await session.flush()
        for kind, quantity, paid, quote, day in buys:
            session.add(CryptoTransaction(asset_id=asset.id, type=CryptoTransactionType(kind), quantity=Decimal(quantity),
                price_per_unit=Decimal(paid), quote_currency=quote, date=day))
        for day, value, quote, confirmed in observations:
            session.add(AssetValuation(asset_id=asset.id, value=Decimal(value), as_of_date=day,
                                       currency=quote, currency_confirmed=confirmed))
        await session.commit()
        return asset.id


async def reads(client):
    response = await client.get("/crypto/holdings")
    assert response.status_code == 200, response.text
    return response.json()


async def test_mixed_buys_sell_and_cache_have_independent_fx_dates(client, test_sessionmaker):
    await holding(test_sessionmaker, buys=[("buy", "1", "50000", "USD", D1),
        ("buy", "1", "6000000", "RUB", D2), ("sell", "0.5", "999999", None, D3)],
        price="1500000000", price_currency="VND", price_date=D2)
    await rate(test_sessionmaker, "RUB", "USD", "0.01", D2)
    await rate(test_sessionmaker, "RUB", "USD", "0.5", D3)
    await rate(test_sessionmaker, "VND", "USD", "0.00004", D2)
    await rate(test_sessionmaker, "VND", "USD", "0.01", D3)
    row = (await reads(client))["holdings"][0]
    assert row["complete"] and row["currency"] == "USD"
    assert Decimal(row["quantity"]) == Decimal("1.5")
    assert Decimal(row["cost_basis"]) == 82500 and Decimal(row["avg_buy_price"]) == 55000
    assert Decimal(row["value"]) == 90000 and Decimal(row["profit_loss"]) == 7500
    assert Decimal(row["current_price"]) == 1500000000 and row["last_price_currency"] == "VND"
    assert row["profit_loss_percent"] == pytest.approx(7500 / 82500 * 100)


async def test_close_unknown_position_and_reopen_resets_cost_issues(client, test_sessionmaker):
    id_ = await holding(test_sessionmaker, buys=[("buy", "1", "100", None, D1),
        ("sell", "1", "2", None, D2), ("buy", "2", "50", "USD", D3)])
    row = (await reads(client))["holdings"][0]
    assert row["complete"] and Decimal(row["cost_basis"]) == 100
    async with test_sessionmaker() as session:
        await session.execute(text("DELETE FROM crypto_transactions WHERE asset_id=:id AND date=:day"), {"id": id_, "day": D3})
        await session.commit()
    row = (await reads(client))["holdings"][0]
    assert Decimal(row["quantity"]) == 0 and row["cost_basis"] is None and row["avg_buy_price"] is None
    assert row["metric_status"]["cost_basis"]["complete"]


@pytest.mark.parametrize("quote,reason", [(None, "provenance"), ("RUB", "absent"), ("EUR", "stale")])
async def test_cost_incomplete_never_hides_known_market_value(client, test_sessionmaker, quote, reason):
    await holding(test_sessionmaker, buys=[("buy", "1", "20", "USD", D1), ("buy", "1", "50", quote, D2)])
    await client.patch("/settings", json={"fx_max_age_days": 0})
    await rate(test_sessionmaker, "EUR", "USD", "2", D1)
    row = (await reads(client))["holdings"][0]
    assert Decimal(row["quantity"]) == 2 and Decimal(row["value"]) == 120000
    assert row["cost_basis"] is None and row["profit_loss"] is None
    assert row["metric_status"]["value"]["complete"] and not row["complete"]
    status = row["metric_status"]["cost_basis"]
    if reason == "provenance":
        assert status["data_issues"] == [{"code": "crypto_transaction_quote_currency_unconfirmed"}]
        assert status["missing_rates"] == []
    else:
        assert status["missing_rates"][0]["reason"] == reason
        assert status["missing_rates"][0]["requested_date"] == D2.isoformat()


@pytest.mark.parametrize("currency,day", [(None, D2), ("USD", None), ("RUB", D2)])
async def test_cost_complete_market_incomplete(client, test_sessionmaker, currency, day):
    await holding(test_sessionmaker, price_currency=currency, price_date=day)
    row = (await reads(client))["holdings"][0]
    assert Decimal(row["cost_basis"]) == 50000
    assert row["value"] is None and row["profit_loss"] is None
    assert row["metric_status"]["cost_basis"]["complete"]
    assert not row["metric_status"]["value"]["complete"]


async def test_history_and_net_worth_convert_events_not_chart_dates(client, test_sessionmaker):
    await holding(test_sessionmaker, observations=[(D1, "100", "EUR", True), (D3, "2600000", "VND", True)])
    await rate(test_sessionmaker, "EUR", "USD", "2", D1)
    await rate(test_sessionmaker, "EUR", "USD", "9", D2)
    await rate(test_sessionmaker, "VND", "USD", "0.00004", D3)
    for path in ("/crypto/history?range=all", "/net-worth/summary?range=all"):
        response = await client.get(path)
        assert response.status_code == 200
        body = response.json()
        assert body["currency"] == "USD" and body["complete"]
        assert [Decimal(point["value"]) for point in body["series"]] == [200, 200, 104]
        assert Decimal(body["current"]) == 104


@pytest.mark.parametrize("confirmed,quote", [(False, "USD"), (True, None), (True, "RUB")])
async def test_unknown_history_point_does_not_poison_other_dates(client, test_sessionmaker, confirmed, quote):
    await holding(test_sessionmaker, observations=[(D1, "100", "USD", True), (D2, "200", quote, confirmed), (D3, "300", "USD", True)])
    response = await client.get("/crypto/history?range=all")
    assert response.status_code == 200
    body = response.json()
    assert [Decimal(p["value"]) if p["value"] is not None else None for p in body["series"]] == [100, None, 300]
    assert body["complete"] is False and body["metric_status"]["current"]["complete"]
    assert Decimal(body["change_amount"]) == 200
    if quote == "RUB":
        assert body["missing_rates"][0]["reason"] == "absent"
    else:
        assert body["data_issues"] == [{"code": "valuation_currency_unconfirmed"}]


@pytest.mark.parametrize("kind", ["direct", "inverse", "bridge", "weekend", "stale", "precision", "future"])
async def test_fx_routes_and_failure_classification(client, test_sessionmaker, kind):
    day = date(2026, 1, 4) if kind == "weekend" else D2
    await holding(test_sessionmaker, buys=[("buy", "1", "10", "RUB", day)], price_date=D1)
    if kind == "direct":
        await rate(test_sessionmaker, "RUB", "USD", "2", D2)
    elif kind in ("inverse", "weekend", "stale"):
        await rate(test_sessionmaker, "USD", "RUB", "0.5", D2)
        if kind == "stale":
            await client.patch("/settings", json={"fx_max_age_days": 0})
            async with test_sessionmaker() as session:
                await session.execute(text("UPDATE crypto_transactions SET date=:day"), {"day": D3})
                await session.commit()
    elif kind == "bridge":
        await rate(test_sessionmaker, "RUB", "EUR", "4", D2)
        await rate(test_sessionmaker, "EUR", "USD", "0.5", D2)
    elif kind == "precision":
        await rate(test_sessionmaker, "RUB", "EUR", "0.000000000000000001", D2)
        await rate(test_sessionmaker, "EUR", "USD", "0.000000000000000001", D2)
    else:
        await rate(test_sessionmaker, "RUB", "USD", "2", D3)
    row = (await reads(client))["holdings"][0]
    if kind in ("direct", "inverse", "bridge", "weekend"):
        assert Decimal(row["cost_basis"]) == 20
    else:
        assert row["cost_basis"] is None
        expected = {"stale": "stale", "precision": "precision_unrepresentable", "future": "absent"}[kind]
        assert row["metric_status"]["cost_basis"]["missing_rates"][0]["reason"] == expected


async def test_cache_missing_and_resolved_once_per_pair_date(client, test_sessionmaker, monkeypatch):
    for _ in range(3):
        await holding(test_sessionmaker, buys=[("buy", "1", "100", "RUB", D1)], price="200", price_currency="RUB", price_date=D1)
    spy = AsyncMock(wraps=fx_service.resolve_rate)
    monkeypatch.setattr(fx_service, "resolve_rate", spy)
    body = await reads(client)
    assert not body["complete"] and spy.await_count == 1
    await rate(test_sessionmaker, "RUB", "USD", "0.01")
    spy.reset_mock()
    body = await reads(client)
    assert body["complete"] and spy.await_count == 1


async def test_zero_bypasses_fx_but_not_provenance(client, test_sessionmaker, monkeypatch):
    await holding(test_sessionmaker, buys=[("buy", "1", "0", "RUB", D1)], price="0", price_currency="VND", price_date=D1)
    forbidden = AsyncMock(side_effect=AssertionError("zero needs no rate"))
    monkeypatch.setattr(fx_service, "resolve_rate", forbidden)
    row = (await reads(client))["holdings"][0]
    assert row["complete"] and Decimal(row["value"]) == 0 and Decimal(row["cost_basis"]) == 0
    forbidden.assert_not_called()


async def test_settings_only_changes_derived_reads_offline_and_precision(client, test_sessionmaker, monkeypatch):
    await holding(test_sessionmaker, buys=[("buy", "1", "50000.123456789123456789", "USD", D1)],
        price="60000.123456789123456789", price_date=D1, observations=[(D1, "60000", "USD", True)])
    await rate(test_sessionmaker, "USD", "VND", "26000", D1)
    async def snapshot():
        async with test_sessionmaker() as session:
            return [(await session.execute(text(f"SELECT * FROM {table} ORDER BY 1"))).all()
                    for table in ("assets", "crypto_holdings", "crypto_transactions", "asset_valuations", "fx_rates")]
    before = await snapshot()
    forbidden = AsyncMock(side_effect=AssertionError("aggregate GET must be offline"))
    monkeypatch.setattr(crypto, "_fetch_market_data", forbidden)
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    engine = test_sessionmaker.kw["bind"].sync_engine
    statements = []
    def inspect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().lower())
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        usd = (await reads(client))["holdings"][0]
        await client.patch("/settings", json={"currency": "VND"})
        statements.clear()
        with localcontext() as context:
            context.prec = 6
            low = (await reads(client))["holdings"][0]
            history = (await client.get("/crypto/history?range=all")).json()
            net_worth = (await client.get("/net-worth/summary?range=all")).json()
        high = (await reads(client))["holdings"][0]
        assert low == high
        with localcontext() as context:
            context.prec = 96
            assert Decimal(usd["cost_basis"]) == Decimal("50000.123456789123456789")
            assert Decimal(high["cost_basis"]) == Decimal("50000.123456789123456789") * 26000
            assert Decimal(high["value"]) == Decimal("60000.123456789123456789") * 26000
        assert high["last_price_currency"] == "USD"
        assert Decimal(history["current"]) == Decimal(net_worth["current"]) == 1560000000
        assert not any(s.startswith(("insert", "update", "delete")) for s in statements)
        assert await snapshot() == before
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


async def test_sorting_uses_converted_cost_and_stable_unknown_tail(client, test_sessionmaker):
    usd = await holding(test_sessionmaker, buys=[("buy", "1", "100", "USD", D1)])
    rub = await holding(test_sessionmaker, buys=[("buy", "1", "1000", "RUB", D1)])
    unknown1 = await holding(test_sessionmaker, buys=[("buy", "1", "9999", None, D1)])
    unknown2 = await holding(test_sessionmaker, buys=[("buy", "1", "999999", None, D1)])
    await rate(test_sessionmaker, "RUB", "USD", "0.01")
    assert [row["asset_id"] for row in (await reads(client))["holdings"]] == [usd, rub, unknown1, unknown2]


async def test_90d_is_dimensionless_and_does_not_resolve_fx(client, test_sessionmaker, monkeypatch):
    await holding(test_sessionmaker)
    forbidden = AsyncMock(side_effect=AssertionError("percent does not need FX"))
    monkeypatch.setattr(fx_service, "resolve_rate", forbidden)
    monkeypatch.setattr(crypto, "_require_api_key", lambda: "test")
    monkeypatch.setattr(crypto, "_fetch_90d_change", AsyncMock(return_value=12.5))
    response = await client.get("/crypto/performance/90d")
    assert response.status_code == 200 and response.json()["items"][0]["price_change_percent"] == 12.5
    forbidden.assert_not_called()


def test_insights_decline_does_not_compare_unknown_to_numbers():
    summary = NetWorthSummary(currency="USD", range="all", current=None, change_amount=None,
        change_percent=None, breakdown=[], capital_roles=[], risk_levels=[], series=[
            NetWorthPoint(date=D1, value=Decimal(100)), NetWorthPoint(date=D2, value=None, complete=False)])
    assert _net_worth_decline_streak(summary) is None
