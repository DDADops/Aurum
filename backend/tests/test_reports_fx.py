"""Reports convert dated flows before category allocation and period aggregation."""
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.services import reports_service as reports
from app.services.fx_provider import FxProvider
from tests.test_dashboard_fx import account, post, rate


async def ranking(client, kind="expense", **params):
    response = await client.get("/reports/category-ranking", params={"kind": kind, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def detail(client, category_id, **params):
    response = await client.get("/reports/category-spending", params={"category_id": category_id, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def child(client, parent):
    response = await client.post("/categories", json={
        "name": "Child", "kind": "expense", "color": "#112233", "parent_id": parent,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def test_native_and_empty_reports(client, account_id, categories):
    groceries, salary = categories["Groceries"]["id"], categories["Salary"]["id"]
    await post(client, account_id, "100", type="income", category_id=salary)
    await post(client, account_id, "25", category_id=groceries)
    for kind, category_id, expected in (("expense", groceries, 25), ("income", salary, 100)):
        rank, single = await ranking(client, kind), await detail(client, category_id)
        assert rank["complete"] and single["complete"]
        assert Decimal(rank["total_amount"]) == Decimal(single["total_amount"]) == expected
        assert Decimal(single["average_per_month"]) == expected
        assert rank["items"][0]["percent"] == 100
        assert single["transaction_count"] == 1
    bounds = dict(start_date="2027-01-01", end_date="2027-03-31")
    empty = await detail(client, groceries, **bounds)
    assert empty["complete"] and empty["series"] == []
    assert Decimal(empty["total_amount"]) == Decimal(empty["average_per_month"]) == 0
    assert empty["start_date"] == bounds["start_date"] and empty["transaction_count"] == 0
    empty = await ranking(client, **bounds)
    assert empty["complete"] and empty["items"] == [] and Decimal(empty["total_amount"]) == 0
    assert (await client.get("/reports/category-spending", params={"category_id": 999999})).status_code == 404


async def test_transaction_dates_and_future_observation_isolation(client, categories, test_sessionmaker):
    rub, salary = await account(client, "RUB"), categories["Salary"]["id"]
    await post(client, rub, "100000", type="income", category_id=salary)
    await post(client, rub, "100000", "2026-01-20", type="income", category_id=salary)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "RUB", "80", "2026-01-20")
    before = [await detail(client, salary), await ranking(client, "income")]
    assert all(Decimal(b["total_amount"]) == 2250 for b in before)
    assert Decimal(before[0]["series"][0]["amount"]) == 2250
    await rate(test_sessionmaker, "USD", "RUB", "10", "2026-02-01")
    await rate(test_sessionmaker, "USD", "RUB", "20", date.today().isoformat())
    assert [await detail(client, salary), await ranking(client, "income")] == before


async def test_mixed_categories_rollup_and_converted_percentages(client, account_id, categories, test_sessionmaker):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    other = (await client.post("/categories", json={"name": "Other FX", "kind": "expense", "color": "#112233"})).json()["id"]
    rub, vnd = await account(client, "RUB"), await account(client, "VND")
    await post(client, account_id, "100", category_id=parent)
    await post(client, rub, "100000", category_id=leaf)
    await post(client, vnd, "3000000", category_id=other)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    body = await ranking(client)
    assert body["complete"] and Decimal(body["total_amount"]) == 1200
    first, second = body["items"]
    assert first["category_id"] == parent and Decimal(first["amount"]) == 1100
    assert {c["category_id"] for c in first["children"]} == {parent, leaf}
    # The established inverse resolver retains 96 digits, not cent quantization.
    assert Decimal(second["amount"]) == Decimal("99." + "9" * 94)
    with localcontext() as context:
        context.prec = 96
        assert first["percent"] == float(Decimal(1100) / 1200 * 100)
    assert Decimal((await detail(client, parent))["total_amount"]) == 1100
    assert Decimal((await detail(client, leaf))["total_amount"]) == 1000


async def test_split_residual_and_child_selection_match_dashboard(client, categories, test_sessionmaker):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    rub = await account(client, "RUB")
    await post(client, rub, "1", splits=[
        {"category_id": parent, "amount": "0.333333"},
        {"category_id": leaf, "amount": "0.666667"},
    ])
    await rate(test_sessionmaker, "USD", "RUB", "3")
    rank, single, leaf_report = await ranking(client), await detail(client, parent), await detail(client, leaf)
    dashboard = (await client.get("/dashboard/summary", params={"year": 2026, "month": 1})).json()
    item = rank["items"][0]
    assert rank["total_amount"] == single["total_amount"] == dashboard["spent"]
    assert single["transaction_count"] == item["transaction_count"] == 1
    with localcontext() as context:
        context.prec = 96
        assert sum(Decimal(c["amount"]) for c in item["children"]) == Decimal(single["total_amount"])
        expected = Decimal(1) / 3
        assert Decimal(single["total_amount"]) == expected
        assert Decimal(leaf_report["total_amount"]) == expected - Decimal("0.333333") * expected
    assert item["children"] == dashboard["spending_by_category"][0]["children"]
    # Deleted categories stay excluded; residual allocation still includes them.
    await client.delete(f"/categories/{leaf}")
    surviving = await detail(client, parent)
    with localcontext() as context:
        context.prec = 96
        assert Decimal(surviving["total_amount"]) == Decimal("0.333333") * (Decimal(1) / 3)


@pytest.mark.parametrize("reason", ["absent", "stale", "precision_unrepresentable"])
async def test_missing_period_independence_and_unavailable_distribution(client, account_id, categories, test_sessionmaker, reason):
    rub = await account(client, "RUB")
    category_id, salary = categories["Groceries"]["id"], categories["Salary"]["id"]
    await post(client, account_id, "100", category_id=category_id)
    await post(client, account_id, "5", "2026-02-10", category_id=category_id)
    await post(client, rub, "100", "2026-02-10", category_id=category_id)
    await post(client, account_id, "50", "2026-03-01", category_id=category_id)
    await post(client, account_id, "200", type="income", category_id=salary)
    if reason == "stale":
        await rate(test_sessionmaker, "USD", "RUB", "100", "2026-02-02")
    elif reason == "precision_unrepresentable":
        await rate(test_sessionmaker, "USD", "RUB", "1e19", "2026-02-10")
    body = await detail(client, category_id)
    first, middle, last = body["series"]
    assert first["complete"] and Decimal(first["amount"]) == 100
    assert last["complete"] and Decimal(last["amount"]) == 50
    assert not middle["complete"] and middle["amount"] is None
    assert body["total_amount"] is body["average_per_month"] is None
    assert not body["metric_status"]["series"]["complete"] and body["transaction_count"] == 4
    missing = middle["missing_rates"][0]
    assert missing["reason"] == reason and missing["requested_date"] == "2026-02-10"
    assert missing["max_age_days"] == 7
    assert missing["latest_available_date"] == {"absent": None, "stale": "2026-02-02", "precision_unrepresentable": "2026-02-10"}[reason]
    rank = await ranking(client)
    assert rank["total_amount"] is None and rank["items"] == []
    assert not rank["metric_status"]["items"]["complete"]
    assert body["missing_rates"] == rank["missing_rates"] == [missing]
    income = await ranking(client, "income")
    assert income["complete"] and Decimal(income["total_amount"]) == 200
    assert (await detail(client, salary))["complete"]


@pytest.mark.parametrize("observed,max_age,complete", [("2025-12-25", 7, True), ("2025-12-24", 7, False),
                                                      ("2026-01-02", 7, False), ("2026-01-01", 0, True)])
async def test_rate_age_and_future_policy(client, categories, test_sessionmaker, observed, max_age, complete):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    await post(client, rub, "100000", category_id=category_id)
    await client.patch("/settings", json={"fx_max_age_days": max_age})
    await rate(test_sessionmaker, "USD", "RUB", "100", observed)
    for body in (await detail(client, category_id), await ranking(client)):
        assert body["complete"] is complete
        if complete:
            assert Decimal(body["total_amount"]) == 1000
        else:
            assert body["total_amount"] is None
            assert body["missing_rates"][0]["reason"] == ("absent" if observed > "2026-01-01" else "stale")


async def test_periods_empty_month_and_average_without_rounding(client, categories, test_sessionmaker):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    for day, quote, amount in (("2026-01-01", "100", "100000"), ("2026-03-01", "80", "100080")):
        await post(client, rub, amount, day, category_id=category_id)
        await rate(test_sessionmaker, "USD", "RUB", quote, day)
    body = await detail(client, category_id)
    assert [Decimal(p["amount"]) for p in body["series"]] == [1000, 0, 1251]
    assert body["series"][1]["complete"]
    with localcontext() as context:
        context.prec = 96
        assert Decimal(body["average_per_month"]) == Decimal(2251) / 3
        assert sum(Decimal(p["amount"]) for p in body["series"]) == Decimal(body["total_amount"])


async def test_filters_transfers_uncategorized_and_future_transactions(client, account_id, categories, monkeypatch):
    parent = categories["Groceries"]["id"]
    other = (await client.post("/categories", json={"name": "Excluded", "kind": "expense", "color": "#112233"})).json()["id"]
    rub, vnd, usd = await account(client, "RUB"), await account(client, "VND"), await account(client, "USD")
    await post(client, rub, "10", "2026-01-14", category_id=parent)
    await post(client, rub, "10", "2026-02-11", category_id=parent)
    await post(client, rub, "10", "2026-02-01", category_id=other)
    await post(client, rub, "10", "2026-02-01")
    await post(client, account_id, "10", "2026-02-01", type="transfer", transfer_account_id=usd)
    await post(client, rub, "10000", "2026-02-01", type="transfer", transfer_account_id=vnd, transfer_amount="3000000")
    await post(client, account_id, "100", "2026-01-15", category_id=parent)
    await post(client, account_id, "25", "2026-02-10", category_id=parent)
    bounds = dict(start_date="2026-01-15", end_date="2026-02-10")
    original = reports.resolve_rate
    async def only_usd(session, source, *args):
        assert source == "USD", "Excluded transaction caused FX lookup"
        return await original(session, source, *args)
    monkeypatch.setattr(reports, "resolve_rate", only_usd)
    body = await detail(client, parent, **bounds)
    assert body["complete"] and Decimal(body["total_amount"]) == 125 and len(body["series"]) == 2
    await client.delete(f"/categories/{other}")
    body = await ranking(client, **bounds)
    assert body["complete"] and Decimal(body["total_amount"]) == 125
    await post(client, account_id, "200", "2099-12-01", category_id=parent)
    future = await detail(client, parent, start_date="2099-12-01")
    assert future["end_date"] == "2099-12-01" and Decimal(future["total_amount"]) == 200


async def test_zero_foreign_requires_no_fx(client, categories, test_sessionmaker, monkeypatch):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    tx = await post(client, rub, "1", category_id=category_id)
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET amount=0 WHERE id=:id"), {"id": tx})
        await session.commit()
    forbidden = AsyncMock(side_effect=AssertionError("Zero needs no FX"))
    monkeypatch.setattr(reports, "resolve_rate", forbidden)
    for body in (await detail(client, category_id), await ranking(client)):
        assert body["complete"] and Decimal(body["total_amount"]) == 0
    forbidden.assert_not_called()


async def test_base_change_preserves_native_rows(client, categories, test_sessionmaker):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    await post(client, rub, "100000", category_id=category_id)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("accounts", "transactions", "transaction_splits", "fx_rates")]
    before = await snapshot()
    assert Decimal((await detail(client, category_id))["total_amount"]) == 1000
    assert Decimal((await ranking(client))["total_amount"]) == 1000
    await client.patch("/settings", json={"currency": "VND"})
    for body in (await detail(client, category_id), await ranking(client)):
        assert body["currency"] == "VND" and Decimal(body["total_amount"]) == 30000000
    assert await snapshot() == before


@pytest.mark.parametrize("missing", [False, True])
@pytest.mark.parametrize("endpoint", ["detail", "ranking"])
async def test_cache_metadata_and_offline_read_only(client, categories, test_sessionmaker, monkeypatch, missing, endpoint):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    for day in ("2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"):
        await post(client, rub, "100", day, category_id=category_id)
    if not missing:
        await rate(test_sessionmaker, "USD", "RUB", "100")
    calls = []
    original = reports.resolve_rate
    async def record(session, *args):
        calls.append(args)
        return await original(session, *args)
    monkeypatch.setattr(reports, "resolve_rate", record)
    forbidden = AsyncMock(side_effect=AssertionError("Reports must stay offline"))
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
        for request in (1, 2):
            body = await detail(client, category_id) if endpoint == "detail" else await ranking(client)
            assert body["complete"] is not missing
            assert len(calls) == len(statements) == request * 2
            assert set(Counter(calls).values()) == {request}
            if missing:
                assert len(body["missing_rates"]) == 2
                assert {r["requested_date"] for r in body["missing_rates"]} == {"2026-01-01", "2026-01-02"}
                if endpoint == "detail":
                    assert len(body["series"][0]["missing_rates"]) == 2
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


async def test_high_precision_low_ambient_context(client, categories, test_sessionmaker):
    rub, category_id = await account(client, "RUB"), categories["Groceries"]["id"]
    await post(client, rub, "123456789.123456", category_id=category_id)
    await rate(test_sessionmaker, "RUB", "USD", "0.123456789012345678")
    with localcontext() as context:
        context.prec = 96
        expected = Decimal("123456789.123456") * Decimal("0.123456789012345678")
    async with test_sessionmaker() as session:
        with localcontext() as context:
            context.prec = 6
            single = await reports.get_category_spending_report(session, category_id, None, None)
            rank = await reports.get_category_ranking_report(session, reports.CategoryKind.EXPENSE, None, None)
    assert single.total_amount == single.average_per_month == rank.total_amount == expected
    assert single.series[0].amount == rank.items[0].amount == expected
    assert isinstance(single.total_amount, Decimal)
