"""Dated stock valuation, local FX and independent point/metric completeness."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.schemas.fx import FxObservation
from app.services import net_worth_service as nw
from app.services.fx_provider import FxProvider
from app.services.fx_service import upsert_observation
from tests.helpers import txn_payload

TODAY = date(2026, 1, 3)
JAN1 = date(2026, 1, 1)
JAN2 = date(2026, 1, 2)


@pytest.fixture(autouse=True)
def valuation_today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY
    monkeypatch.setattr(nw, "date_", FixedDate)


async def account(client, currency="USD", type="checking"):
    response = await client.post("/accounts", json={"name": currency, "currency": currency, "type": type})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def post(client, id_, amount="100", day=JAN1, **kwargs):
    response = await client.post("/transactions", json=txn_payload(
        id_, type="income", amount=amount, date=day.isoformat(), **kwargs))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def transfer(client, source, destination, amount="10", credit="100", day=JAN2):
    response = await client.post("/transactions", json=txn_payload(
        source, type="transfer", amount=amount, transfer_account_id=destination,
        transfer_amount=credit, date=day.isoformat()))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def rate(sessionmaker, base, quote, amount, day=JAN1):
    async with sessionmaker() as session:
        await upsert_observation(session, FxObservation(
            base_currency=base, quote_currency=quote, rate=Decimal(amount), as_of_date=day,
            source="fixture", fetched_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        ))
        await session.commit()


async def asset(client, currency="EUR", value="1000", day=JAN1, **kwargs):
    response = await client.post("/assets", json={
        "name": currency, "currency": currency, "asset_class": "other", "value": value,
        "as_of_date": day.isoformat(), **kwargs,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def summary(client, range="all"):
    response = await client.get("/net-worth/summary", params={"range": range})
    assert response.status_code == 200, response.text
    return response.json()


def values(body):
    return [Decimal(p["value"]) if p["value"] is not None else None for p in body["series"]]


@pytest.mark.parametrize("currency", ["USD", "VND"])
async def test_same_currency_without_rates(client, currency):
    await client.patch("/settings", json={"currency": currency})
    id_ = await account(client, currency)
    await post(client, id_)
    body = await summary(client)
    assert body["currency"] == currency and body["complete"]
    assert Decimal(body["current"]) == 100 and values(body) == [Decimal(100)] * 3
    assert body["missing_rates"] == []


async def test_three_currencies_and_non_neutral_transfer(client, test_sessionmaker):
    usd, rub, vnd = await account(client), await account(client, "RUB"), await account(client, "VND")
    await post(client, usd, "500")
    await post(client, rub, "100000")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    await transfer(client, rub, vnd, "10000", "3000000")
    # Jan 1 = 500+1000; Jan 2 = 500+900+100.
    body = await summary(client)
    assert values(body) == [Decimal(1500)] * 3
    await rate(test_sessionmaker, "USD", "VND", "25000", JAN2)
    body = await summary(client)
    assert values(body) == [Decimal(1500), Decimal(1520), Decimal(1520)]
    assert Decimal(body["current"]) == 1520
    assert Decimal(body["breakdown"][0]["amount"]) == 1520


async def test_stock_revalued_without_transactions_on_second_day(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "RUB", "80", JAN2)
    body = await summary(client)
    assert values(body) == [Decimal(1000), Decimal(1250), Decimal(1250)]
    assert Decimal(body["change_amount"]) == 250 and body["change_percent"] == 25


async def test_historical_missing_does_not_poison_today_or_future_points(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await client.patch("/settings", json={"fx_max_age_days": 0})
    await rate(test_sessionmaker, "USD", "RUB", "100", JAN1)
    await rate(test_sessionmaker, "USD", "RUB", "80", TODAY)
    body = await summary(client)
    assert values(body) == [Decimal(1000), None, Decimal(1250)]
    missing = body["series"][1]["missing_rates"]
    assert missing[0]["reason"] == "stale" and missing[0]["requested_date"] == JAN2.isoformat()
    assert body["complete"] and body["missing_rates"] == []
    assert body["metric_status"]["change_amount"]["complete"]
    assert Decimal(body["change_amount"]) == 250


async def test_future_rate_cannot_fill_past_and_change_needs_both_endpoints(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await rate(test_sessionmaker, "USD", "RUB", "100", TODAY)
    body = await summary(client)
    assert values(body) == [None, None, Decimal(1000)]
    assert body["complete"] and not body["missing_rates"]
    assert body["series"][0]["missing_rates"][0]["reason"] == "absent"
    assert body["change_amount"] is body["change_percent"] is None
    assert not body["metric_status"]["change_amount"]["complete"]
    assert not body["metric_status"]["change_percent"]["complete"]


@pytest.mark.parametrize("age,max_age,complete", [(2, 7, True), (7, 7, True), (8, 7, False), (0, 0, True), (1, 0, False)])
async def test_calendar_age_and_setting(client, test_sessionmaker, age, max_age, complete):
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await client.patch("/settings", json={"fx_max_age_days": max_age})
    await rate(test_sessionmaker, "USD", "RUB", "100", TODAY - timedelta(days=age))
    body = await summary(client)
    assert body["complete"] is complete
    if complete:
        assert Decimal(body["current"]) == 1000
    else:
        assert body["current"] is None and body["missing_rates"][0]["reason"] == "stale"
        assert body["missing_rates"][0]["max_age_days"] == max_age


@pytest.mark.parametrize("reason", ["absent", "precision_unrepresentable"])
async def test_no_partial_subtotal_or_allocation(client, test_sessionmaker, reason):
    usd, rub = await account(client), await account(client, "RUB")
    await post(client, usd, "500")
    await post(client, rub, "100000")
    if reason == "precision_unrepresentable":
        await rate(test_sessionmaker, "USD", "RUB", "1e19")
    body = await summary(client)
    assert body["current"] is None and not body["complete"]
    assert body["missing_rates"][0]["reason"] == reason
    assert body["breakdown"] == body["capital_roles"] == body["risk_levels"] == []
    assert not body["metric_status"]["breakdown"]["complete"]
    assert not body["metric_status"]["risk_levels"]["complete"]


async def test_exact_zero_needs_no_fx_and_future_transactions_do_not_contribute(client):
    rub = await account(client, "RUB")
    await post(client, rub, "100", JAN2)
    response = await client.post("/transactions", json=txn_payload(rub, amount="100", date=JAN2.isoformat()))
    assert response.status_code == 201
    await post(client, rub, "900", TODAY + timedelta(days=1))
    body = await summary(client, "30d")
    assert all(p["complete"] and Decimal(p["value"]) == 0 for p in body["series"])
    assert body["complete"] and body["change_percent"] is None
    assert body["metric_status"]["change_percent"]["complete"]


async def test_unresolved_is_date_scoped_and_reconciliation_restores(client, test_sessionmaker):
    source, destination = await account(client), await account(client)
    await post(client, source, "100")
    tx = await transfer(client, source, destination, "10", "10")
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": tx})
        await session.commit()
    body = await summary(client)
    assert values(body) == [Decimal(100), None, None]
    assert body["series"][0]["complete"]
    assert body["data_issues"] == [{"code": "transfer_amount_unresolved"}]
    assert not body["metric_status"]["change_amount"]["complete"]
    # Source is still exact when the unknown destination is outside cash scope.
    await client.patch(f"/accounts/{destination}", json={"type": "credit_card"})
    assert values(await summary(client)) == [Decimal(100), Decimal(90), Decimal(90)]
    await client.patch(f"/accounts/{destination}", json={"type": "checking"})
    response = await client.patch(f"/transactions/{tx}", json={"transfer_amount": "10"})
    assert response.status_code == 200, response.text
    assert values(await summary(client)) == [Decimal(100)] * 3


async def test_asset_native_value_forward_fills_but_fx_revalues(client, test_sessionmaker):
    id_ = await asset(client)
    await rate(test_sessionmaker, "EUR", "USD", "2")
    await rate(test_sessionmaker, "EUR", "USD", "3", JAN2)
    response = await client.post(f"/assets/{id_}/valuations", json={"value": "2000", "as_of_date": TODAY.isoformat()})
    assert response.status_code == 200, response.text
    response = await client.post(f"/assets/{id_}/valuations", json={"value": "9000", "as_of_date": "2026-01-04"})
    assert response.status_code == 200
    body = await summary(client)
    assert values(body) == [Decimal(2000), Decimal(3000), Decimal(6000)]
    assert Decimal(body["current"]) == 6000


async def test_asset_without_history_is_unknown_but_before_first_valuation_is_zero(client, test_sessionmaker):
    id_ = await asset(client, "USD", day=JAN2)
    body = await summary(client, "30d")
    points = {p["date"]: p for p in body["series"]}
    assert Decimal(points[JAN1.isoformat()]["value"]) == 0
    assert Decimal(points[JAN2.isoformat()]["value"]) == 1000
    async with test_sessionmaker() as session:
        await session.execute(text("DELETE FROM asset_valuations WHERE asset_id=:id"), {"id": id_})
        await session.commit()
    body = await summary(client)
    assert body["current"] is None and body["data_issues"] == [{"code": "valuation_missing"}]


async def test_converted_breakdown_roles_cashflow_and_risk(client, test_sessionmaker):
    usd = await account(client)
    await post(client, usd, "100")
    await asset(client, "EUR", "100", capital_role="income", monthly_cash_flow="5", risk_level="high")
    await asset(client, "RUB", "10000", capital_role="income", monthly_cash_flow="100", risk_level="high")
    await rate(test_sessionmaker, "EUR", "USD", "2")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    body = await summary(client)
    assert Decimal(body["current"]) == 400
    breakdown = {r["key"]: r for r in body["breakdown"]}
    assert Decimal(breakdown["cash"]["amount"]) == 100
    assert Decimal(breakdown["other"]["amount"]) == 300 and breakdown["other"]["percent"] == 75
    role = next(r for r in body["capital_roles"] if r["role"] == "income")
    assert Decimal(role["total_value"]) == 300 and Decimal(role["monthly_cash_flow"]) == 11
    high = next(r for r in body["risk_levels"] if r["risk_level"] == "high")
    assert Decimal(high["total_value"]) == 300 and high["percent"] == 75
    assert sorted(Decimal(i["amount"]) for i in high["items"]) == [Decimal(100), Decimal(200)]
    assert sum(i["percent"] for i in high["items"]) == pytest.approx(100)


async def test_role_cashflow_failure_does_not_poison_zero_asset_stock(client):
    await asset(client, "EUR", "0", monthly_cash_flow="5")
    body = await summary(client)
    assert body["complete"] and Decimal(body["current"]) == 0
    assert body["missing_rates"] == [] and body["capital_roles"] == []
    assert not body["metric_status"]["capital_roles"]["complete"]
    assert body["metric_status"]["breakdown"]["complete"]


async def test_settings_change_recomputes_without_mutating_native_data(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await asset(client, "EUR", "1000")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "EUR", "USD", "2")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("accounts", "transactions", "assets", "asset_valuations", "fx_rates")]
    before = await snapshot()
    assert Decimal((await summary(client))["current"]) == 3000
    assert (await client.patch("/settings", json={"currency": "VND"})).status_code == 200
    body = await summary(client)
    assert body["currency"] == "VND" and Decimal(body["current"]) == 90000000
    assert await snapshot() == before
    native = {a["id"]: a for a in (await client.get("/accounts")).json()}[rub]
    assert native["currency"] == "RUB" and Decimal(native["balance"]) == 100000


async def test_precision_is_independent_of_ambient_context(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "123456789.123456")
    await rate(test_sessionmaker, "RUB", "USD", "0.123456789012345678")
    await asset(client, "EUR", "1234.123456789012345678")
    await rate(test_sessionmaker, "EUR", "USD", "1.234567890123456789")
    with localcontext() as context:
        context.prec = 96
        expected = Decimal("123456789.123456") * Decimal("0.123456789012345678") + Decimal("1234.123456789012345678") * Decimal("1.234567890123456789")
    async with test_sessionmaker() as session:
        with localcontext() as context:
            context.prec = 6
            result = await nw.get_net_worth_summary(session, "all")
    assert isinstance(result.current, Decimal) and result.current == expected
    assert Decimal(result.model_dump(mode="json")["current"]) == expected


async def test_request_cache_network_and_read_only_fx(client, test_sessionmaker, monkeypatch):
    for _ in range(3):
        rub = await account(client, "RUB")
        await post(client, rub, "100")
    await asset(client, "RUB", "100", monthly_cash_flow="100")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    calls = []
    original = nw.resolve_rate
    async def recording(session, source, target, day, max_age):
        calls.append((source, target, day, max_age))
        return await original(session, source, target, day, max_age)
    monkeypatch.setattr(nw, "resolve_rate", recording)
    forbidden = AsyncMock(side_effect=AssertionError("GET must not fetch network FX"))
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    statements = []
    engine = test_sessionmaker.kw["bind"].sync_engine
    def inspect(conn, cursor, statement, parameters, context, executemany):
        if "fx_rates" in statement.lower():
            assert statement.lstrip().upper().startswith("SELECT")
            statements.append(statement)
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        body = await summary(client)
        assert Decimal(body["current"]) == 4
        assert len(calls) == len(statements) == 3
        assert set(Counter(calls).values()) == {1}
        assert {call[2] for call in calls} == {JAN1, JAN2, TODAY}
        await summary(client)
        assert len(calls) == len(statements) == 6  # cache belongs to one request
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


@pytest.mark.parametrize("range_key,length", [("30d", 30), ("90d", 90), ("1y", 365), ("5y", 1825)])
async def test_range_calendar_dates(client, range_key, length):
    body = await summary(client, range_key)
    assert len(body["series"]) == length
    assert body["series"][0]["date"] == (TODAY - timedelta(days=length - 1)).isoformat()
    assert body["series"][-1]["date"] == TODAY.isoformat()


async def test_all_with_future_only_events_has_today_and_no_future_value(client):
    id_ = await account(client)
    await post(client, id_, day=TODAY + timedelta(days=1))
    await asset(client, "EUR", day=TODAY + timedelta(days=2))
    body = await summary(client)
    assert len(body["series"]) == 1 and body["series"][0]["date"] == TODAY.isoformat()
    assert Decimal(body["current"]) == 0


async def test_archived_and_account_type_scope_unchanged(client):
    archived = await account(client)
    excluded = await account(client, "RUB", "credit_card")
    await post(client, archived, "100")
    await post(client, excluded, "900")
    await client.patch(f"/accounts/{archived}", json={"is_archived": True})
    assert Decimal((await summary(client))["current"]) == 100


async def test_missing_rate_is_cached_and_deduplicated_per_point(client, monkeypatch):
    for _ in range(2):
        rub = await account(client, "RUB")
        await post(client, rub, "100")
    await asset(client, "RUB", "100")
    original = nw.resolve_rate
    spy = AsyncMock(wraps=original)
    monkeypatch.setattr(nw, "resolve_rate", spy)
    body = await summary(client)
    assert spy.await_count == 3
    assert all(len(point["missing_rates"]) == 1 for point in body["series"])
    assert len(body["missing_rates"]) == 1


async def test_future_unresolved_transfer_does_not_invalidate_today(client, test_sessionmaker):
    source, destination = await account(client), await account(client)
    await post(client, source, "100")
    tx = await transfer(client, source, destination, "10", "10", TODAY + timedelta(days=1))
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": tx})
        await session.commit()
    body = await summary(client)
    assert values(body) == [Decimal(100)] * 3 and body["complete"]


async def test_friday_observation_values_sunday(client, test_sessionmaker, monkeypatch):
    class Sunday(date):
        @classmethod
        def today(cls):
            return date(2026, 1, 4)
    monkeypatch.setattr(nw, "date_", Sunday)
    rub = await account(client, "RUB")
    await post(client, rub, "100000")
    await rate(test_sessionmaker, "USD", "RUB", "100", date(2026, 1, 2))
    body = await summary(client)
    assert body["series"][-1]["date"] == "2026-01-04"
    assert body["complete"] and Decimal(body["current"]) == 1000
