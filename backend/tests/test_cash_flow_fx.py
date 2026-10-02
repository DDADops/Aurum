"""Dated Cash Flow contributions and independent monthly/metric status."""
from collections import Counter
from datetime import date
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.services import cash_flow_service as flow
from app.services.fx_provider import FxProvider
from tests.test_dashboard_fx import account, post, rate


async def report(client, start="2026-01-01", end="2026-03-31"):
    response = await client.get("/cash-flow", params={k: v for k, v in {"start_date": start, "end_date": end}.items() if v is not None})
    assert response.status_code == 200, response.text
    return response.json()


async def test_same_currency_empty_buckets_and_reconciliation(client, account_id):
    await post(client, account_id, "100", type="income")
    await post(client, account_id, "25")
    await post(client, account_id, "50", "2026-03-31", type="income")
    body = await report(client)
    assert body["complete"]
    assert [Decimal(body[k]) for k in ("total_income", "total_expense", "total_net")] == [150, 25, 125]
    assert body["points"][1]["complete"] and Decimal(body["points"][1]["net"]) == 0
    for name in ("income", "expense", "net"):
        assert sum(Decimal(p[name]) for p in body["points"]) == Decimal(body[f"total_{name}"])
    empty = await report(client, "2027-01-01", "2027-03-31")
    assert empty["complete"] and len(empty["points"]) == 3
    assert all(Decimal(p["net"]) == 0 for p in empty["points"])


async def test_flow_dates_precede_bucketing_and_future_fx_does_not_revalue(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await post(client, rub, "100000", "2026-01-20", type="income")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "RUB", "80", "2026-01-20")
    body = await report(client)
    assert body["complete"] and Decimal(body["total_income"]) == 2250
    assert Decimal(body["points"][0]["income"]) == 2250
    await rate(test_sessionmaker, "USD", "RUB", "20", "2026-02-01")
    await rate(test_sessionmaker, "USD", "RUB", "10", date.today().isoformat())
    assert await report(client) == body


async def test_mixed_income_expense(client, account_id, test_sessionmaker):
    rub, vnd = await account(client, "RUB"), await account(client, "VND")
    await post(client, account_id, "500", type="income")
    await post(client, rub, "100000", type="income")
    await post(client, vnd, "3000000")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    body = await report(client)
    assert body["complete"] and Decimal(body["total_income"]) == 1500
    # Preserve the existing 96-digit repeating inverse; no money quantization.
    assert Decimal(body["total_expense"]) == Decimal("99." + "9" * 94)
    assert Decimal(body["total_net"]) == 1400


@pytest.mark.parametrize("reason", ["absent", "stale", "precision_unrepresentable"])
async def test_middle_bucket_failure_preserves_other_buckets_and_income(client, account_id, test_sessionmaker, reason):
    rub = await account(client, "RUB")
    await post(client, account_id, "100", type="income")
    await post(client, account_id, "200", "2026-02-01", type="income")
    await post(client, account_id, "5", "2026-02-01")
    await post(client, rub, "100", "2026-02-10")
    await post(client, account_id, "50", "2026-03-01")
    if reason == "stale":
        await rate(test_sessionmaker, "USD", "RUB", "100", "2026-02-02")
    elif reason == "precision_unrepresentable":
        await rate(test_sessionmaker, "USD", "RUB", "1e19", "2026-02-10")
    body = await report(client)
    first, middle, last = body["points"]
    assert first["complete"] and Decimal(first["net"]) == 100
    assert last["complete"] and Decimal(last["net"]) == -50
    assert not middle["complete"] and Decimal(middle["income"]) == 200
    assert middle["expense"] is middle["net"] is None
    assert middle["metric_status"]["income"]["complete"]
    assert not body["complete"] and Decimal(body["total_income"]) == 300
    assert body["total_expense"] is body["total_net"] is None
    assert body["metric_status"]["total_income"]["complete"]
    missing = middle["missing_rates"][0]
    assert missing["reason"] == reason and missing["requested_date"] == "2026-02-10"
    assert missing["max_age_days"] == 7 and body["missing_rates"] == [missing]


@pytest.mark.parametrize("observed,max_age,complete", [("2025-12-25", 7, True), ("2025-12-24", 7, False),
                                                      ("2026-01-02", 7, False), ("2026-01-01", 0, True)])
async def test_observation_age_and_no_future_rate(client, test_sessionmaker, observed, max_age, complete):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await client.patch("/settings", json={"fx_max_age_days": max_age})
    await rate(test_sessionmaker, "USD", "RUB", "100", observed)
    body = await report(client)
    assert body["complete"] is complete
    if complete:
        assert Decimal(body["total_income"]) == 1000
    else:
        assert body["total_income"] is None
        assert body["missing_rates"][0]["reason"] == ("absent" if observed > "2026-01-01" else "stale")


async def test_different_buckets_resolve_their_own_dates(client, test_sessionmaker):
    rub = await account(client, "RUB")
    for day, quote in (("2026-01-01", "100"), ("2026-03-01", "80")):
        await post(client, rub, "100000", day, type="income")
        await rate(test_sessionmaker, "USD", "RUB", quote, day)
    body = await report(client)
    assert [Decimal(p["income"]) for p in body["points"]] == [1000, 0, 1250]
    assert Decimal(body["total_income"]) == 2250


async def test_transfers_excluded_without_any_fx(client, account_id, monkeypatch):
    usd, rub, vnd = await account(client, "USD"), await account(client, "RUB"), await account(client, "VND")
    await post(client, account_id, "10", type="transfer", transfer_account_id=usd)
    await post(client, rub, "10000", type="transfer", transfer_account_id=vnd, transfer_amount="3000000")
    forbidden = AsyncMock(side_effect=AssertionError("Transfers must not need reference FX"))
    monkeypatch.setattr(flow, "resolve_rate", forbidden)
    body = await report(client)
    assert body["complete"] and all(Decimal(p["net"]) == 0 for p in body["points"])
    assert Decimal(body["total_income"]) == Decimal(body["total_expense"]) == 0
    unbounded = await report(client, None, None)
    assert unbounded["points"] == [] and unbounded["start_date"] is unbounded["end_date"] is None
    forbidden.assert_not_called()


async def test_zero_legacy_foreign_flow_skips_fx(client, test_sessionmaker, monkeypatch):
    rub = await account(client, "RUB")
    tx = await post(client, rub, "1")
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET amount=0 WHERE id=:id"), {"id": tx})
        await session.commit()
    forbidden = AsyncMock(side_effect=AssertionError("Zero requires no FX"))
    monkeypatch.setattr(flow, "resolve_rate", forbidden)
    body = await report(client)
    assert body["complete"] and Decimal(body["total_net"]) == 0
    forbidden.assert_not_called()


async def test_inclusive_partial_month_and_future_transaction_policy(client, account_id):
    rub = await account(client, "RUB")
    await post(client, rub, "100", "2026-01-14")
    await post(client, rub, "100", "2026-02-11")
    await post(client, account_id, "100", "2026-01-15", type="income")
    await post(client, account_id, "25", "2026-02-10")
    body = await report(client, "2026-01-15", "2026-02-10")
    assert body["complete"] and Decimal(body["total_net"]) == 75
    assert len(body["points"]) == 2
    await post(client, account_id, "200", "2099-12-01", type="income")
    body = await report(client, "2099-12-01", None)
    assert body["end_date"] == "2099-12-01" and Decimal(body["total_income"]) == 200


async def test_base_change_preserves_native_rows(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", type="income")
    await rate(test_sessionmaker, "USD", "RUB", "100")
    await rate(test_sessionmaker, "USD", "VND", "30000")
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("accounts", "transactions", "fx_rates")]
    before = await snapshot()
    assert Decimal((await report(client))["total_income"]) == 1000
    await client.patch("/settings", json={"currency": "VND"})
    body = await report(client)
    assert body["currency"] == "VND" and Decimal(body["total_income"]) == 30000000
    assert await snapshot() == before


@pytest.mark.parametrize("missing", [False, True])
async def test_cache_metadata_and_offline_read_only_fx(client, test_sessionmaker, monkeypatch, missing):
    rub = await account(client, "RUB")
    for day in ("2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"):
        await post(client, rub, "100", day, type="income")
    if not missing:
        await rate(test_sessionmaker, "USD", "RUB", "100")
    calls = []
    original = flow.resolve_rate
    async def record(session, *args):
        calls.append(args)
        return await original(session, *args)
    monkeypatch.setattr(flow, "resolve_rate", record)
    forbidden = AsyncMock(side_effect=AssertionError("No provider/network from Cash Flow"))
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
        body = await report(client)
        assert body["complete"] is not missing
        assert len(calls) == len(statements) == 2 and set(Counter(calls).values()) == {1}
        if missing:
            assert len(body["missing_rates"]) == len(body["points"][0]["missing_rates"]) == 2
            assert {r["requested_date"] for r in body["missing_rates"]} == {"2026-01-01", "2026-01-02"}
        await report(client)
        assert len(calls) == len(statements) == 4
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


async def test_low_ambient_decimal_context(client, test_sessionmaker):
    rub = await account(client, "RUB")
    await post(client, rub, "123456789.123456", type="income")
    await rate(test_sessionmaker, "RUB", "USD", "0.123456789012345678")
    with localcontext() as context:
        context.prec = 96
        expected = Decimal("123456789.123456") * Decimal("0.123456789012345678")
    async with test_sessionmaker() as session:
        with localcontext() as context:
            context.prec = 6
            body = await flow.get_cash_flow(session, None, None)
    assert body.total_income == body.total_net == body.points[0].income == expected
    assert isinstance(body.total_income, Decimal)
