"""Nominal goal state and its manual journal never need reference FX."""
import asyncio
from datetime import date
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select, text

from app.models.goal import Goal
from app.services import fx_service, goal_service
from app.services.fx_provider import FxProvider
from tests.test_native_transfers import wait_blocked


async def create(client, currency="USD", target="10000", **kwargs):
    payload = {"name": "Goal", "target_amount": target, **kwargs}
    if currency is not None:
        payload["currency"] = currency
    response = await client.post("/goals", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def contribute(client, id_, amount, day="2026-01-15"):
    response = await client.post(f"/goals/{id_}/contributions", json={"amount": amount, "date": day, "note": "Manual"})
    assert response.status_code == 201, response.text
    return response.json()


async def test_default_validation_and_nominal_updates(client):
    await client.patch("/settings", json={"currency": "RUB"})
    inherited = await create(client, None)
    assert inherited["currency"] == "RUB"
    goal = await create(client, "vnd", "50000000", target_date="2027-05-20")
    assert goal["currency"] == "VND"
    for currency in ("XYZ", "US", ""):
        response = await client.post("/goals", json={"name": "Bad", "target_amount": "10", "currency": currency})
        assert response.status_code == 422
    id_ = goal["id"]
    for payload in ({"currency": None}, {"currency": "XYZ"}, {"target_amount": None}):
        assert (await client.patch(f"/goals/{id_}", json=payload)).status_code == 422
    assert (await client.patch(f"/goals/{id_}", json={"currency": "USD"})).status_code == 409
    assert (await client.patch(f"/goals/{id_}", json={"currency": "VND"})).status_code == 200
    changed = await client.patch(f"/goals/{id_}", json={"currency": "usd", "target_amount": "50000000"})
    assert changed.status_code == 200
    assert changed.json()["currency"] == "USD" and Decimal(changed.json()["target_amount"]) == 50000000
    renamed = await client.patch(f"/goals/{id_}", json={"name": "Renamed"})
    assert renamed.json()["target_date"] == "2027-05-20"
    cleared = await client.patch(f"/goals/{id_}", json={"target_date": None})
    assert cleared.json()["target_date"] is None
    await contribute(client, id_, "10")
    await contribute(client, id_, "-10")
    # A net-zero journal is still monetary evidence that cannot be re-denominated.
    rejected = await client.patch(f"/goals/{id_}", json={"currency": "RUB", "target_amount": "1"})
    assert rejected.status_code == 409 and rejected.json()["detail"] == "currency_change_requires_empty_history"
    updated = await client.patch(f"/goals/{id_}", json={"currency": "usd", "target_amount": "200"})
    assert updated.status_code == 200 and Decimal(updated.json()["current_amount"]) == 0


async def test_settings_change_preserves_goal_and_journal(client, test_sessionmaker):
    goal = await create(client)
    before = await contribute(client, goal["id"], "2500")
    assert Decimal(before["remaining"]) == 7500 and before["percent"] == 25 and not before["is_reached"]
    async def snapshot():
        async with test_sessionmaker() as session:
            return [list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
                    for table in ("goals", "goal_contributions", "transactions", "fx_rates")]
    rows = await snapshot()
    await client.patch("/settings", json={"currency": "VND"})
    assert (await client.get("/goals")).json() == [before]
    assert await snapshot() == rows
    after = await contribute(client, goal["id"], "500")
    assert after["currency"] == "USD" and Decimal(after["current_amount"]) == 3000
    async with test_sessionmaker() as session:
        assert await session.scalar(text("SELECT count(*) FROM transactions")) == 0


async def test_multi_currency_crud_is_offline_without_fx(client, test_sessionmaker, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Goal nominal arithmetic needs no FX/network"))
    monkeypatch.setattr(fx_service, "resolve_rate", forbidden)
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    engine = test_sessionmaker.kw["bind"].sync_engine
    def inspect(conn, cursor, statement, parameters, context, executemany):
        assert "fx_rates" not in statement.lower(), "Goal CRUD touched FX storage"
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        ids = []
        for currency, target, amount, expected in (("USD", "10000", "2500", 25),
                                                    ("VND", "50000000", "10000000", 20),
                                                    ("RUB", "100", "125", 125)):
            goal = await create(client, currency, target)
            ids.append(goal["id"])
            result = await contribute(client, goal["id"], amount)
            assert result["currency"] == currency and result["percent"] == expected
            assert Decimal(result["current_amount"]) == Decimal(amount)
            assert result["is_reached"] is (expected >= 100)
            assert (await client.patch(f"/goals/{goal['id']}", json={"name": currency})).status_code == 200
        rows = (await client.get("/goals")).json()
        assert isinstance(rows, list) and [r["currency"] for r in rows] == ["USD", "VND", "RUB"]
        assert Decimal(rows[2]["remaining"]) == -25
        for id_ in ids:
            assert (await client.delete(f"/goals/{id_}")).status_code == 204
        assert (await client.get("/goals")).json() == []
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()
    async with test_sessionmaker() as session:
        assert await session.scalar(text("SELECT count(*) FROM goal_contributions")) == 0
        assert await session.scalar(text("SELECT count(*) FROM transactions")) == 0


@pytest.mark.parametrize("target,current,remaining,percent,reached", [
    ("0", "0", "0", 0, True), ("0", "5", "-5", 0, True),
    ("0", "-5", "5", 0, False), ("-10", "-5", "-5", 50, True),
    ("100", "-25", "125", -25, False), ("100", "100", "0", 100, True),
])
async def test_legacy_and_signed_edge_semantics(client, test_sessionmaker, target, current, remaining, percent, reached):
    goal = await create(client, target="100", target_date="2020-01-01")
    if current != "0":
        await contribute(client, goal["id"], current, "2099-12-31")
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE goals SET target_amount=:target WHERE id=:id"), {"target": Decimal(target), "id": goal["id"]})
        await session.commit()
    row = (await client.get("/goals")).json()[0]
    assert Decimal(row["current_amount"]) == Decimal(current)
    assert Decimal(row["remaining"]) == Decimal(remaining)
    assert row["percent"] == percent and row["is_reached"] is reached
    assert row["target_date"] == "2020-01-01"  # Deadline never filters the manual log.


async def test_precision_is_independent_of_ambient_context(client, test_sessionmaker):
    target, amount = Decimal("99999999999999.123456"), Decimal("12345678901234.123456")
    goal = await create(client, target=str(target))
    await contribute(client, goal["id"], str(amount))
    await contribute(client, goal["id"], "-0.000001")
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 96, ROUND_HALF_EVEN
        current = amount - Decimal("0.000001")
        remaining, percent = target - current, float(current / target * 100)
    async with test_sessionmaker() as session:
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 6, ROUND_DOWN
            rows = await goal_service.list_goals(session)
    assert rows[0].target_amount == target and rows[0].current_amount == current
    assert rows[0].remaining == remaining and rows[0].percent == percent


@pytest.mark.parametrize("first", ["contribution", "currency"])
async def test_contribution_currency_change_serialization(client, test_sessionmaker, first):
    goal = await create(client)
    id_ = goal["id"]
    async with test_sessionmaker() as blocker:
        await blocker.execute(select(Goal).where(Goal.id == id_).with_for_update())
        pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        if first == "contribution":
            task = asyncio.create_task(client.patch(f"/goals/{id_}", json={"currency": "VND", "target_amount": "20"}))
        else:
            task = asyncio.create_task(client.post(f"/goals/{id_}/contributions", json={"amount": "5", "date": "2026-01-01"}))
        try:
            await wait_blocked(test_sessionmaker, pid)
            if first == "contribution":
                await goal_service.add_contribution(blocker, id_, goal_service.GoalContributionCreate(amount="5", date=date(2026, 1, 1)))
            else:
                await goal_service.update_goal(blocker, id_, goal_service.GoalUpdate(currency="VND", target_amount="20"))
            result = await asyncio.wait_for(task, 5)
            assert result.status_code == (409 if first == "contribution" else 201)
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    row = (await client.get("/goals")).json()[0]
    assert row["currency"] == ("USD" if first == "contribution" else "VND")
    assert Decimal(row["current_amount"]) == 5
