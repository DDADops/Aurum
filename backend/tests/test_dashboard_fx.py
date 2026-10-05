"""Dashboard values dated flows, never revalued stocks."""
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.schemas.dashboard import DashboardSummary
from app.schemas.fx import FxObservation
from app.schemas.money import FinancialDataStatus
from app.services import advice_service, dashboard_service as dashboard, insights_service
from app.services.fx_provider import FxProvider
from app.services.fx_service import upsert_observation
from tests.helpers import txn_payload


async def account(client, currency):
    result = await client.post("/accounts", json={"name": currency, "currency": currency})
    assert result.status_code == 201
    return result.json()["id"]


async def post(client, id_, amount="100", day="2026-01-01", **kwargs):
    result = await client.post("/transactions", json=txn_payload(id_, amount=amount, date=day, **kwargs))
    assert result.status_code == 201, result.text
    return result.json()["id"]


async def rate(sessionmaker, base, quote, value, day="2026-01-01"):
    async with sessionmaker() as session:
        await upsert_observation(session, FxObservation(
            base_currency=base, quote_currency=quote, rate=Decimal(value),
            as_of_date=date.fromisoformat(day), source="fixture", fetched_at=datetime.now(timezone.utc),
        ))
        await session.commit()


async def summary(client, month=1):
    result = await client.get("/dashboard/summary", params={"year": 2026, "month": month})
    assert result.status_code == 200, result.text
    return result.json()


async def test_same_currency_and_empty_month(client, account_id, categories):
    await post(client, account_id, "100", type="income")
    await post(client, account_id, "25", category_id=categories["Groceries"]["id"])
    body = await summary(client)
    assert body["complete"]
    assert [Decimal(body[k]) for k in ("real_income", "spent", "net", "transferred_out")] == [100, 25, 75, 0]
    empty = await summary(client, 2)
    assert empty["complete"] and empty["spending_by_category"] == []
    assert all(Decimal(empty[k]) == 0 for k in ("real_income", "spent", "net", "transferred_out"))


async def test_each_income_uses_its_transaction_date_and_future_rates_do_not_revalue(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await post(client, rub, "100000", day="2026-01-02", type="income")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "RUB", "80", "2026-01-02")
    body = await summary(client)
    assert Decimal(body["real_income"]) == 2250 and Decimal(body["net"]) == 2250
    await rate(test_sessionmaker, "USD", "RUB", "40", "2026-02-01")
    await rate(test_sessionmaker, "USD", "RUB", "20", date.today().isoformat())
    assert await summary(client) == body


async def test_mixed_flows_and_categories_convert_before_grouping(client, account_id, categories, test_sessionmaker):
    rub, vnd = await account(client, "RUB"), await account(client, "VND")
    groceries, dining = categories["Groceries"]["id"], categories["Dining Out"]["id"]
    await post(client, account_id, "500", type="income")
    await post(client, rub, "100000", type="income")
    await post(client, account_id, "25", category_id=groceries)
    await post(client, rub, "7500", category_id=groceries)
    await post(client, vnd, "3000000", category_id=dining)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    body = await summary(client)
    assert [Decimal(body[k]) for k in ("real_income", "spent", "net")] == [1500, 200, 1300]
    rows = {r["category_id"]: r for r in body["spending_by_category"]}
    assert Decimal(rows[groceries]["amount"]) == 100
    # The existing resolver represents 1/30000 at 96 significant digits.
    # Preserve that result rather than rounding this category to money scale.
    assert Decimal(rows[dining]["amount"]) == Decimal("99." + "9" * 94)
    assert rows[groceries]["percent"] == rows[dining]["percent"] == 50


@pytest.mark.parametrize("day,max_age,reason", [("2025-12-25", 7, None), ("2025-12-24", 7, "stale"),
                                             ("2026-01-02", 7, "absent"), ("2025-12-31", 0, "stale"),
                                             ("2026-01-01", 0, None)])
async def test_historical_staleness_policy(client, test_sessionmaker, day, max_age, reason):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await client.patch("/settings", json={"fx_max_age_days": max_age})
    await rate(test_sessionmaker, "USD", "RUB", "100", day)
    body = await summary(client)
    if reason:
        assert body["real_income"] is None and body["net"] is None and not body["complete"]
        missing = body["metric_status"]["real_income"]["missing_rates"]
        assert missing[0]["reason"] == reason and missing[0]["requested_date"] == "2026-01-01"
        assert missing[0]["max_age_days"] == max_age
    else:
        assert body["complete"] and Decimal(body["real_income"]) == 1000


@pytest.mark.parametrize("reason", ["absent", "precision_unrepresentable"])
async def test_missing_expense_preserves_income_but_suppresses_net_and_categories(client, account_id, categories, test_sessionmaker, reason):
    rub = await account(client, "RUB")
    await post(client, account_id, "500", type="income")
    await post(client, account_id, "25", category_id=categories["Groceries"]["id"])
    await post(client, rub, "100", category_id=categories["Groceries"]["id"])
    if reason == "precision_unrepresentable":
        await rate(test_sessionmaker, "USD", "RUB", "1e19")
    body = await summary(client)
    assert Decimal(body["real_income"]) == 500 and body["metric_status"]["real_income"]["complete"]
    assert body["spent"] is body["net"] is None
    assert body["spending_by_category"] == [] and not body["metric_status"]["spending_by_category"]["complete"]
    assert body["missing_rates"][0]["reason"] == reason and not body["complete"]
    assert len(body["missing_rates"]) == 1


async def test_outgoing_transfer_is_source_flow_not_execution_rate(client, test_sessionmaker):
    rub, vnd = await account(client, "RUB"), await account(client, "VND")
    tx = await post(client, rub, "10000", type="transfer", transfer_account_id=vnd, transfer_amount="3000000")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    body = await summary(client)
    assert body["complete"] and Decimal(body["transferred_out"]) == 100
    assert all(Decimal(body[k]) == 0 for k in ("real_income", "spent", "net"))
    # Destination and execution amount are irrelevant to outgoing volume.
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": tx})
        await session.commit()
    assert await summary(client) == body


async def test_missing_transfer_rate_does_not_hide_net(client):
    rub, usd = await account(client, "RUB"), await account(client, "USD")
    await post(client, rub, "100", type="transfer", transfer_account_id=usd, transfer_amount="1")
    body = await summary(client)
    assert body["transferred_out"] is None and not body["complete"]
    assert Decimal(body["net"]) == 0 and body["metric_status"]["net"]["complete"]


@pytest.mark.parametrize("fx_rate", ["7", "1.234567890123456789"])
async def test_foreign_splits_reconcile_without_money_rounding(client, categories, test_sessionmaker, fx_rate):
    rub = await account(client, "RUB")
    parent = categories["Groceries"]["id"]
    child = (await client.post("/categories", json={"name": "Child", "kind": "expense", "color": "#123456", "parent_id": parent})).json()["id"]
    await post(client, rub, "1.000003", splits=[{"category_id": parent, "amount": "0.333334"},
                                               {"category_id": child, "amount": "0.666669"}])
    await rate(test_sessionmaker, "USD", "RUB", fx_rate)
    body = await summary(client)
    row = body["spending_by_category"][0]
    with localcontext() as context:
        context.prec = 96
        expected = Decimal("1.000003") * (Decimal(1) / Decimal(fx_rate))
        assert Decimal(body["spent"]) == expected
        assert Decimal(row["amount"]) == expected
        assert sum((Decimal(c["amount"]) for c in row["children"]), Decimal(0)) == expected
    assert row["percent"] == 100
    assert len(str(expected).split(".")[1]) > 6


async def test_zero_foreign_legacy_amount_needs_no_rate(client, test_sessionmaker, monkeypatch):
    rub = await account(client, "RUB")
    tx = await post(client, rub, "1", type="income")
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET amount=0 WHERE id=:id"), {"id": tx})
        await session.commit()
    spy = AsyncMock(side_effect=AssertionError("Zero does not need FX"))
    monkeypatch.setattr(dashboard, "resolve_rate", spy)
    body = await summary(client)
    assert body["complete"] and Decimal(body["real_income"]) == 0
    spy.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
async def test_request_cache_and_offline_read_only_queries(client, test_sessionmaker, monkeypatch, missing):
    rub = await account(client, "RUB")
    for day in ("2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"):
        await post(client, rub, "100", day=day, type="income")
    if not missing:
        await rate(test_sessionmaker, "USD", "RUB", "100")
    calls = []
    original = dashboard.resolve_rate
    async def record(session, *args):
        calls.append(args)
        return await original(session, *args)
    monkeypatch.setattr(dashboard, "resolve_rate", record)
    forbidden = AsyncMock(side_effect=AssertionError("No external FX from Dashboard"))
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
        assert body["complete"] is not missing
        assert len(calls) == len(statements) == 2 and set(Counter(calls).values()) == {1}
        if missing:
            assert body["real_income"] is None and len(body["missing_rates"]) == 2
        else:
            assert Decimal(body["real_income"]) == 4
        await summary(client)
        assert len(calls) == len(statements) == 4
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


async def test_base_change_preserves_native_records_and_recomputes(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("accounts", "transactions", "fx_rates")]
    before = await snapshot()
    assert Decimal((await summary(client))["real_income"]) == 1000
    await client.patch("/settings", json={"currency": "VND"})
    body = await summary(client)
    assert body["currency"] == "VND" and Decimal(body["real_income"]) == 30000000
    assert await snapshot() == before
    record = (await client.get("/transactions")).json()["items"][0]
    assert Decimal(record["amount"]) == 100000 and record["account"]["currency"] == "RUB"


async def test_low_context_preserves_high_precision(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "123456789.123456", type="income")
    await rate(test_sessionmaker, "RUB", "USD", "0.123456789012345678")
    with localcontext() as context:
        context.prec = 96
        expected = Decimal("123456789.123456") * Decimal("0.123456789012345678")
    async with test_sessionmaker() as session:
        with localcontext() as context:
            context.prec = 6
            body = await dashboard.get_dashboard_summary(session, 2026, 1)
    assert isinstance(body.real_income, Decimal) and body.real_income == body.net == expected


async def test_month_boundaries_and_out_of_period_missing_rates(client, account_id):
    rub = await account(client, "RUB")
    await post(client, account_id, "100", "2026-01-01", type="income")
    await post(client, account_id, "25", "2026-01-31")
    await post(client, rub, "9999", "2025-12-31")
    await post(client, rub, "9999", "2026-02-01")
    body = await summary(client)
    assert body["complete"] and Decimal(body["net"]) == 75


async def test_dashboard_callers_suppress_unknown_operands(test_sessionmaker, monkeypatch):
    incomplete = DashboardSummary(
        year=2026, month=1, currency="USD", complete=False,
        real_income=Decimal(100), spent=None, net=None, transferred_out=Decimal(0),
        spending_by_category=[], metric_status={"net": FinancialDataStatus(complete=False)},
    )
    from app.schemas.cash_flow import CashFlowResponse
    monkeypatch.setattr(insights_service, "get_cash_flow", AsyncMock(return_value=CashFlowResponse(
        currency="USD", start_date=None, end_date=None, points=[],
        total_income=None, total_expense=None, total_net=None, complete=False,
    )))
    monkeypatch.setattr(advice_service, "get_dashboard_summary", AsyncMock(return_value=incomplete))
    async with test_sessionmaker() as session:
        assert await insights_service._negative_cash_flow_streak(session) is None
        assert await advice_service._savings_rate_trend_advice(session, 2026, 1) is None
