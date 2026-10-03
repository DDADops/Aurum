"""Budgets own their denomination; expense valuation uses transaction-date FX."""
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.services import budget_service as budgets
from app.services.fx_provider import FxProvider
from tests.test_dashboard_fx import account, post, rate
from tests.test_reports_fx import child


async def create(client, category_id, currency="USD", limit="1000"):
    payload = {"category_id": category_id, "monthly_limit": limit}
    if currency is not None:
        payload["currency"] = currency
    response = await client.post("/budgets", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def status(client, year=2026, month=1):
    response = await client.get("/budgets/status", params={"year": year, "month": month})
    assert response.status_code == 200, response.text
    return response.json()


async def test_persisted_create_update_semantics(client, categories):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    await client.patch("/settings", json={"currency": "VND"})
    inherited = await create(client, parent, None)
    explicit = await create(client, leaf, "usd")
    assert inherited["currency"] == "VND" and explicit["currency"] == "USD"
    await client.patch("/settings", json={"currency": "RUB"})
    assert {b["currency"] for b in (await client.get("/budgets")).json()} == {"USD", "VND"}
    id_ = explicit["id"]
    assert (await client.patch(f"/budgets/{id_}", json={"currency": "vnd"})).status_code == 422
    for currency in (None, "BAD"):
        assert (await client.patch(f"/budgets/{id_}", json={"currency": currency, "monthly_limit": "1000"})).status_code == 422
    response = await client.patch(f"/budgets/{id_}", json={"currency": "vnd", "monthly_limit": "1000"})
    assert response.status_code == 200
    assert response.json()["currency"] == "VND" and Decimal(response.json()["monthly_limit"]) == 1000
    response = await client.patch(f"/budgets/{id_}", json={"monthly_limit": "2000"})
    assert response.json()["currency"] == "VND"
    assert (await client.post("/budgets", json={"category_id": 9999, "currency": "BAD", "monthly_limit": "10"})).status_code == 422


@pytest.mark.parametrize("currency,amount,limit", [("USD", "120", "100"), ("VND", "1000000", "5000000")])
async def test_same_currency_no_fx_and_derived(client, categories, currency, amount, limit):
    category_id = categories["Groceries"]["id"]
    source = await account(client, currency)
    await create(client, category_id, currency, limit)
    await post(client, source, amount, category_id=category_id)
    body = await status(client)
    item = body["items"][0]
    assert body["complete"] and item["complete"] and item["currency"] == currency
    assert Decimal(item["spent"]) == Decimal(amount)
    assert Decimal(item["remaining"]) == Decimal(limit) - Decimal(amount)
    assert item["percent"] == float(Decimal(amount) / Decimal(limit) * 100)
    assert item["is_over_budget"] is (Decimal(amount) > Decimal(limit))
    empty = (await status(client, month=2))["items"][0]
    assert empty["complete"] and Decimal(empty["spent"]) == 0 and not empty["is_over_budget"]


async def test_dated_spending_and_future_rates(client, categories, test_sessionmaker):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await create(client, category_id, limit="500")
    await post(client, rub, "100000", category_id=category_id)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    assert Decimal((await status(client))["items"][0]["spent"]) == 1000
    await post(client, rub, "100000", "2026-01-20", category_id=category_id)
    await rate(test_sessionmaker, "USD", "RUB", "80", "2026-01-20")
    before = await status(client)
    assert Decimal(before["items"][0]["spent"]) == 2250
    await rate(test_sessionmaker, "USD", "RUB", "10", "2026-02-01")
    await rate(test_sessionmaker, "USD", "RUB", "20", date.today().isoformat())
    assert await status(client) == before


async def test_mixed_and_base_currency_invariant(client, account_id, categories, test_sessionmaker):
    category_id = categories["Groceries"]["id"]
    rub, vnd = await account(client, "RUB"), await account(client, "VND")
    await create(client, category_id)
    await post(client, vnd, "3000000", category_id=category_id)
    await rate(test_sessionmaker, "USD", "VND", "30000")
    assert Decimal((await status(client))["items"][0]["spent"]) == Decimal("99." + "9" * 94)
    await post(client, rub, "100000", category_id=category_id)
    await post(client, account_id, "50", category_id=category_id)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await post(client, rub, "10000", type="transfer", transfer_account_id=vnd, transfer_amount="3000000")
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("accounts", "transactions", "transaction_splits", "budgets", "fx_rates")]
    rows = await snapshot()
    before = await status(client)
    assert before["items"][0]["currency"] == "USD" and Decimal(before["items"][0]["spent"]) == 1150
    await client.patch("/settings", json={"currency": "VND"})
    assert await status(client) == before
    assert await snapshot() == rows


@pytest.mark.parametrize("route", ["direct", "inverse", "bridge"])
async def test_budget_target_not_settings_currency(client, categories, test_sessionmaker, monkeypatch, route):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await create(client, category_id, "VND", "5000000")
    await post(client, rub, "10000", category_id=category_id)
    if route == "direct":
        await rate(test_sessionmaker, "RUB", "VND", "200")
    elif route == "inverse":
        await rate(test_sessionmaker, "VND", "RUB", "0.005")
    else:
        await rate(test_sessionmaker, "USD", "RUB", "100")
        await rate(test_sessionmaker, "USD", "VND", "20000")
    calls = []
    original = budgets.resolve_rate
    async def record(session, *args):
        calls.append(args)
        return await original(session, *args)
    monkeypatch.setattr(budgets, "resolve_rate", record)
    before = await status(client)
    assert Decimal(before["items"][0]["spent"]) == 2000000
    assert calls == [("RUB", "VND", date(2026, 1, 1), 7)]
    await client.patch("/settings", json={"currency": "RUB"})
    assert await status(client) == before


async def test_parent_child_split_residual_and_deleted_category(client, categories, test_sessionmaker):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    rub = await account(client, "RUB")
    await create(client, parent)
    await create(client, leaf)
    await post(client, rub, "1", splits=[
        {"category_id": parent, "amount": "0.333333"}, {"category_id": leaf, "amount": "0.666667"},
    ])
    await rate(test_sessionmaker, "USD", "RUB", "3")
    items = {b["category_id"]: b for b in (await status(client))["items"]}
    with localcontext() as ctx:
        ctx.prec = 96
        full = Decimal(1) / 3
        direct = Decimal("0.333333") * full
        assert Decimal(items[parent]["spent"]) == full
        assert Decimal(items[leaf]["spent"]) == full - direct
    await client.delete(f"/categories/{leaf}")
    remaining = (await status(client))["items"]
    assert len(remaining) == 1 and Decimal(remaining[0]["spent"]) == direct


@pytest.mark.parametrize("reason", ["absent", "stale", "precision_unrepresentable"])
async def test_missing_never_returns_partial_or_false(client, account_id, categories, test_sessionmaker, reason):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await create(client, category_id)
    await post(client, account_id, "25", category_id=category_id)
    await post(client, rub, "100", category_id=category_id)
    if reason == "stale":
        await rate(test_sessionmaker, "USD", "RUB", "100", "2025-12-24")
    elif reason == "precision_unrepresentable":
        await rate(test_sessionmaker, "USD", "RUB", "1e19")
    body = await status(client)
    item = body["items"][0]
    assert not body["complete"] and not item["complete"]
    assert item["spent"] is item["remaining"] is item["percent"] is item["is_over_budget"] is None
    assert Decimal(item["monthly_limit"]) == 1000 and item["metric_status"]["monthly_limit"]["complete"]
    assert all(not item["metric_status"][key]["complete"] for key in ("spent", "remaining", "percent", "is_over_budget"))
    assert body["missing_rates"] == item["missing_rates"] == [{
        "base_currency": "RUB", "quote_currency": "USD", "requested_date": "2026-01-01",
        "reason": reason, "max_age_days": 7,
        "latest_available_date": {"absent": None, "stale": "2025-12-24", "precision_unrepresentable": "2026-01-01"}[reason],
    }]


async def test_independent_budget_targets(client, categories, test_sessionmaker):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    rub = await account(client, "RUB")
    await create(client, parent, "USD")
    await create(client, leaf, "VND")
    await post(client, rub, "100000", category_id=leaf)
    await rate(test_sessionmaker, "USD", "RUB", "100")
    body = await status(client)
    items = {b["currency"]: b for b in body["items"]}
    assert not body["complete"] and items["USD"]["complete"]
    assert Decimal(items["USD"]["spent"]) == 1000
    assert items["VND"]["spent"] is None and items["VND"]["missing_rates"][0]["quote_currency"] == "VND"


async def test_age_policy_and_future_rate(client, categories, test_sessionmaker):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await create(client, category_id)
    await post(client, rub, "100000", category_id=category_id)
    await rate(test_sessionmaker, "USD", "RUB", "100", "2026-01-02")
    assert (await status(client))["missing_rates"][0]["reason"] == "absent"
    await rate(test_sessionmaker, "USD", "RUB", "100", "2025-12-25")
    assert (await status(client))["complete"]
    await client.patch("/settings", json={"fx_max_age_days": 6})
    body = await status(client)
    assert not body["complete"] and body["missing_rates"][0]["max_age_days"] == 6
    await client.patch("/settings", json={"fx_max_age_days": 7})
    assert (await status(client))["complete"]


async def test_period_category_type_filters_before_fx(client, account_id, categories, monkeypatch):
    category_id, other = categories["Groceries"]["id"], categories["Housing & Utilities"]["id"]
    rub, vnd, usd = await account(client, "RUB"), await account(client, "VND"), await account(client, "USD")
    await create(client, category_id)
    await post(client, rub, "100", "2025-12-31", category_id=category_id)
    await post(client, rub, "100", "2026-02-01", category_id=category_id)
    await post(client, rub, "100", category_id=other)
    await post(client, rub, "100")
    await post(client, rub, "100", type="income", category_id=categories["Salary"]["id"])
    await post(client, rub, "10000", type="transfer", transfer_account_id=vnd, transfer_amount="3000000")
    await post(client, account_id, "100", type="transfer", transfer_account_id=usd)
    await post(client, account_id, "10", "2026-01-01", category_id=category_id)
    await post(client, account_id, "20", "2026-01-31", category_id=category_id)
    original = budgets.resolve_rate
    async def only_usd(session, source, *args):
        assert source == "USD", "Excluded foreign expense triggered resolution"
        return await original(session, source, *args)
    monkeypatch.setattr(budgets, "resolve_rate", only_usd)
    assert Decimal((await status(client))["items"][0]["spent"]) == 30
    await post(client, account_id, "50", "2099-12-31", category_id=category_id)
    assert Decimal((await status(client, 2099, 12))["items"][0]["spent"]) == 50


async def test_legacy_zero_amount_and_limit(client, categories, test_sessionmaker, monkeypatch):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    budget = await create(client, category_id)
    tx = await post(client, rub, "1", category_id=category_id)
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET amount=0 WHERE id=:id"), {"id": tx})
        await session.execute(text("UPDATE budgets SET monthly_limit=0 WHERE id=:id"), {"id": budget["id"]})
        await session.commit()
    forbidden = AsyncMock(side_effect=AssertionError("Zero requires no FX"))
    monkeypatch.setattr(budgets, "resolve_rate", forbidden)
    item = (await status(client))["items"][0]
    assert item["complete"] and Decimal(item["spent"]) == Decimal(item["remaining"]) == 0
    assert item["percent"] == 0 and item["is_over_budget"] is False
    forbidden.assert_not_called()


@pytest.mark.parametrize("missing", [False, True])
async def test_shared_request_cache_targets_dates_and_offline_reads(client, categories, test_sessionmaker, monkeypatch, missing):
    parent = categories["Groceries"]["id"]
    leaf = await child(client, parent)
    other = categories["Housing & Utilities"]["id"]
    rub = await account(client, "RUB")
    for category_id, currency in ((parent, "USD"), (leaf, "USD"), (other, "VND")):
        await create(client, category_id, currency)
    for day in ("2026-01-01", "2026-01-01", "2026-01-02"):
        for category_id in (leaf, other):
            await post(client, rub, "100", day, category_id=category_id)
    if not missing:
        await rate(test_sessionmaker, "RUB", "USD", "0.01")
        await rate(test_sessionmaker, "RUB", "VND", "200")
    calls = []
    original = budgets.resolve_rate
    async def record(session, *args):
        calls.append(args)
        return await original(session, *args)
    monkeypatch.setattr(budgets, "resolve_rate", record)
    forbidden = AsyncMock(side_effect=AssertionError("No provider or network from Budget GET"))
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
        assert (await client.get("/budgets")).status_code == 200
        assert calls == statements == []
        for request in (1, 2):
            body = await status(client)
            assert body["complete"] is not missing
            assert len(calls) == len(statements) == request * 4
            assert set(Counter(calls).values()) == {request}
            assert {(c[1], c[2]) for c in calls} == {(target, date(2026, 1, day)) for target in ("USD", "VND") for day in (1, 2)}
            if missing:
                assert len(body["missing_rates"]) == 4
                assert all(len(item["missing_rates"]) == 2 for item in body["items"])
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


async def test_precision_and_low_ambient_context(client, categories, test_sessionmaker):
    category_id = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await create(client, category_id, limit="20000000.123456")
    await post(client, rub, "123456789.123456", category_id=category_id)
    await rate(test_sessionmaker, "RUB", "USD", "0.123456789012345678")
    with localcontext() as ctx:
        ctx.prec = 96
        expected = Decimal("123456789.123456") * Decimal("0.123456789012345678")
        remaining = Decimal("20000000.123456") - expected
        percent = float(expected / Decimal("20000000.123456") * 100)
    async with test_sessionmaker() as session:
        with localcontext() as ctx:
            ctx.prec = 6
            body = await budgets.get_budget_status(session, 2026, 1)
    item = body.items[0]
    assert item.spent == expected and item.remaining == remaining and item.percent == percent


async def test_insights_does_not_treat_unknown_budget_as_false(client, account_id, categories, test_sessionmaker):
    category_id = categories["Groceries"]["id"]
    await create(client, category_id, "VND", "100")
    day = date.today().isoformat()
    await post(client, account_id, "200", day, category_id=category_id)
    body = (await client.get("/insights/alerts")).json()
    assert not body["complete"] and body["alerts"] == []
    assert body["missing_rates"][0]["quote_currency"] == "VND"
    await rate(test_sessionmaker, "USD", "VND", "20000", day)
    body = (await client.get("/insights/alerts")).json()
    assert body["complete"]
    assert next(a for a in body["alerts"] if a["key"] == "budget_exceeded")["params"]["count"] == 1
