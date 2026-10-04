"""Authored native recurring instructions, revalidation and real row locks."""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select, text

from app.models.account import Account
from app.models.enums import RecurringFrequency
from app.services import fx_service, recurring_service as recurring
from app.services.fx_provider import FxProvider
from tests.test_native_currency import new_account, template
from tests.test_native_transfers import wait_blocked
from tests.test_monetary_precision import migrate


def payload(source, **changes):
    return dict(account_id=source, type="expense", amount="100", description="Instruction",
                frequency="monthly", anchor_date="2026-01-01", **changes)


async def read(client, id_):
    return next(r for r in (await client.get("/recurring")).json() if r["id"] == id_)


@pytest.mark.parametrize("kind,currency", [("expense", "USD"), ("income", "VND")])
async def test_source_mismatch_metadata_and_explicit_reentry(client, kind, currency):
    source = await new_account(client, currency)
    row = await template(client, source, type=kind, amount="100")
    id_ = row["id"]
    assert row["amount_currency"] == currency and row["transfer_amount"] is None
    assert (await client.patch(f"/accounts/{source}", json={"currency": "RUB"})).status_code == 200
    assert await read(client, id_) == row
    for changes in ({"description": "Renamed"}, {"frequency": "weekly"}, {"anchor_date": "2025-01-01"}):
        response = await client.patch(f"/recurring/{id_}", json=changes)
        assert response.status_code == 200 and response.json()["amount_currency"] == currency
        failed = await client.post(f"/recurring/{id_}/post")
        assert failed.status_code == 409 and failed.json()["detail"] == "recurring_currency_mismatch"
        assert (await read(client, id_))["last_posted_date"] is None
    assert (await client.get("/transactions")).json()["total"] == 0
    assert (await client.patch(f"/recurring/{id_}", json={"amount_currency": "RUB"})).status_code == 422
    response = await client.patch(f"/recurring/{id_}", json={"amount": "100"})
    assert response.status_code == 200 and response.json()["amount_currency"] == "RUB"
    posted = await client.post(f"/recurring/{id_}/post")
    assert posted.status_code == 201 and posted.json()["last_posted_date"] == date.today().isoformat()
    tx = (await client.get("/transactions")).json()["items"][0]
    assert tx["type"] == kind and Decimal(tx["amount"]) == 100
    assert (await client.patch(f"/accounts/{source}", json={"currency": "USD"})).status_code == 409
    # last_posted_date/template are not history after the actual transaction is deleted.
    await client.delete(f"/transactions/{tx['id']}")
    assert (await client.patch(f"/accounts/{source}", json={"currency": "USD"})).status_code == 200


async def test_transfer_validation_and_final_patch_state(client, account_id):
    same, rub, vnd = await new_account(client), await new_account(client, "RUB"), await new_account(client, "VND")
    row = await template(client, account_id, type="transfer", transfer_account_id=same)
    id_ = row["id"]
    assert row["amount_currency"] == row["transfer_amount_currency"] == "USD"
    assert Decimal(row["amount"]) == Decimal(row["transfer_amount"])
    assert (await client.post("/recurring", json={**payload(account_id), "type": "transfer", "transfer_account_id": same, "transfer_amount": "1"})).status_code == 400
    assert (await client.post("/recurring", json={**payload(rub), "type": "transfer", "transfer_account_id": vnd})).status_code == 400
    for changes in ({"amount_currency": "RUB"}, {"transfer_amount": "1"}, {"transfer_account_id": same}):
        assert (await client.post("/recurring", json={**payload(account_id), **changes})).status_code in (400, 422)
    assert (await client.patch(f"/recurring/{id_}", json={"transfer_account_id": vnd})).status_code == 400
    cross = await client.patch(f"/recurring/{id_}", json={"account_id": rub, "transfer_account_id": vnd, "amount": "10000.123456", "transfer_amount": "3000000.123456"})
    assert cross.status_code == 200
    assert cross.json()["amount_currency"] == "RUB" and cross.json()["transfer_amount_currency"] == "VND"
    assert Decimal(cross.json()["transfer_amount"]) == Decimal("3000000.123456")
    assert (await client.patch(f"/recurring/{id_}", json={"amount": "5"})).status_code == 400
    back = await client.patch(f"/recurring/{id_}", json={"account_id": account_id, "transfer_account_id": same, "amount": "12.123456"})
    assert back.status_code == 200 and Decimal(back.json()["transfer_amount"]) == Decimal("12.123456")
    expense = await client.patch(f"/recurring/{id_}", json={"type": "expense"})
    assert expense.status_code == 200
    assert all(expense.json()[key] is None for key in ("transfer_account_id", "transfer_amount", "transfer_amount_currency"))
    assert (await client.patch(f"/recurring/{id_}", json={"type": "transfer"})).status_code == 400
    assert (await client.patch(f"/recurring/{id_}", json={"type": "transfer", "transfer_account_id": same})).status_code == 200
    assert (await client.post(f"/recurring/{id_}/post")).status_code == 201


async def test_cross_post_copies_amounts_and_destination_mismatch(client):
    source, destination = await new_account(client, "RUB"), await new_account(client, "VND")
    row = await template(client, source, type="transfer", transfer_account_id=destination, amount="10000", transfer_amount="3000000")
    id_ = row["id"]
    assert (await client.patch(f"/accounts/{destination}", json={"currency": "USD"})).status_code == 200
    assert await read(client, id_) == row
    failure = await client.post(f"/recurring/{id_}/post")
    assert failure.status_code == 409 and failure.json()["detail"] == "recurring_currency_mismatch"
    assert (await client.patch(f"/recurring/{id_}", json={"description": "Still VND"})).json()["transfer_amount_currency"] == "VND"
    assert (await client.patch(f"/recurring/{id_}", json={"transfer_amount": "300"})).json()["transfer_amount_currency"] == "USD"
    assert (await client.patch(f"/accounts/{destination}", json={"currency": "VND"})).status_code == 200
    assert (await client.patch(f"/recurring/{id_}", json={"amount": "10000", "transfer_amount": "3000000"})).status_code == 200
    assert (await client.post(f"/recurring/{id_}/post")).status_code == 201
    tx = (await client.get("/transactions")).json()["items"][0]
    assert Decimal(tx["amount"]) == 10000 and Decimal(tx["transfer_amount"]) == 3000000
    balances = {r["id"]: Decimal(r["balance"]) for r in (await client.get("/accounts")).json()}
    assert balances[source] == -10000 and balances[destination] == 3000000


async def test_legacy_unresolved_and_deleted_destination_remain_readable(client, test_sessionmaker):
    source, destination = await new_account(client, "RUB"), await new_account(client, "VND")
    row = await template(client, source, type="transfer", transfer_account_id=destination, transfer_amount="1000")
    id_ = row["id"]
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE recurring_transactions SET transfer_amount=NULL WHERE id=:id"), {"id": id_})
        await session.commit()
    assert (await read(client, id_))["transfer_amount"] is None
    changed = await client.patch(f"/recurring/{id_}", json={"frequency": "yearly"})
    assert changed.status_code == 200 and changed.json()["transfer_amount"] is None
    failure = await client.post(f"/recurring/{id_}/post")
    assert failure.status_code == 409 and failure.json()["detail"] == "recurring_currency_revalidation_required"
    assert (await read(client, id_))["last_posted_date"] is None
    assert (await client.patch(f"/recurring/{id_}", json={"transfer_amount": "2000"})).status_code == 200
    assert (await client.delete(f"/accounts/{destination}")).status_code == 204
    assert (await read(client, id_))["transfer_account_id"] is None
    assert (await client.post(f"/recurring/{id_}/post")).status_code == 409
    assert (await client.delete(f"/accounts/{source}")).status_code == 204
    assert (await client.get("/recurring")).json() == []


@pytest.mark.parametrize("operation", ["create", "patch", "post"])
async def test_currency_change_wins_account_lock(client, account_id, test_sessionmaker, operation):
    row = await template(client, account_id) if operation != "create" else None
    async with test_sessionmaker() as blocker:
        await blocker.execute(select(Account).where(Account.id == account_id).with_for_update())
        pid = await blocker.scalar(text("SELECT pg_backend_pid()"))
        if operation == "create":
            task = asyncio.create_task(client.post("/recurring", json=payload(account_id)))
        elif operation == "patch":
            task = asyncio.create_task(client.patch(f"/recurring/{row['id']}", json={"amount": "100"}))
        else:
            task = asyncio.create_task(client.post(f"/recurring/{row['id']}/post"))
        try:
            await wait_blocked(test_sessionmaker, pid)
            await blocker.execute(text("UPDATE accounts SET currency='VND' WHERE id=:id"), {"id": account_id})
            await blocker.commit()
            response = await asyncio.wait_for(task, 5)
            if operation == "post":
                assert response.status_code == 409 and response.json()["detail"] == "recurring_currency_mismatch"
                assert (await read(client, row["id"]))["last_posted_date"] is None
            else:
                assert response.status_code == (201 if operation == "create" else 200)
                assert response.json()["amount_currency"] == "VND"
            assert (await client.get("/transactions")).json()["total"] == 0
        finally:
            await blocker.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_post_wins_currency_change_and_concurrent_due_posts(client, account_id, test_sessionmaker, monkeypatch):
    row = await template(client, account_id)
    locked, release = asyncio.Event(), asyncio.Event()
    original = recurring._lock_instruction
    async def pause(session, *args, **kwargs):
        result = await original(session, *args, **kwargs)
        locked.set()
        await release.wait()
        return result
    monkeypatch.setattr(recurring, "_lock_instruction", pause)
    first = asyncio.create_task(client.post(f"/recurring/{row['id']}/post"))
    await asyncio.wait_for(locked.wait(), 5)
    second = asyncio.create_task(client.post(f"/recurring/{row['id']}/post"))
    currency = asyncio.create_task(client.patch(f"/accounts/{account_id}", json={"currency": "VND"}))
    try:
        # Both competing calls must actually wait on the first posting's account.
        async with test_sessionmaker() as observer:
            for _ in range(300):
                count = await observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"))
                if count >= 2:
                    break
                await observer.commit()
                await asyncio.sleep(0.01)
            else:
                pytest.fail("Expected two PostgreSQL lock waits")
        release.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, currency), 5)
        assert [r.status_code for r in results] == [201, 409, 409]
        assert (await client.get("/transactions")).json()["total"] == 1
        assert (await read(client, row["id"]))["last_posted_date"] == date.today().isoformat()
    finally:
        release.set()
        for task in (first, second, currency):
            if not task.done():
                task.cancel()
        await asyncio.gather(first, second, currency, return_exceptions=True)


async def test_lock_union_and_offline_operations(client, account_id, test_sessionmaker, monkeypatch):
    other, cross = await new_account(client), await new_account(client, "VND")
    forbidden = AsyncMock(side_effect=AssertionError("Recurring must never use FX or network"))
    monkeypatch.setattr(fx_service, "resolve_rate", forbidden)
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    locks = []
    engine = test_sessionmaker.kw["bind"].sync_engine
    def inspect(conn, cursor, statement, parameters, context, executemany):
        assert "fx_rates" not in statement.lower()
        if "FROM accounts" in statement and "FOR UPDATE" in statement:
            locks.append(tuple(parameters))
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        row = await template(client, other, type="transfer", transfer_account_id=account_id)
        assert locks.pop() == tuple(sorted((other, account_id)))
        response = await client.patch(f"/recurring/{row['id']}", json={"account_id": account_id, "transfer_account_id": cross, "transfer_amount": "3000000.123456"})
        assert response.status_code == 200
        assert locks.pop() == tuple(sorted((account_id, other, cross)))
        assert (await client.post(f"/recurring/{row['id']}/post")).status_code == 201
        assert locks.pop() == tuple(sorted((account_id, cross)))
        assert (await client.get("/recurring")).status_code == 200
        assert (await client.delete(f"/recurring/{row['id']}")).status_code == 204
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()


@pytest.mark.parametrize("day,frequency,expected", [(date(2026, 1, 31), RecurringFrequency.MONTHLY, date(2026, 2, 28)),
    (date(2024, 2, 29), RecurringFrequency.YEARLY, date(2025, 2, 28)),
    (date(2026, 1, 1), RecurringFrequency.WEEKLY, date(2026, 1, 8))])
async def test_schedule_advance(day, frequency, expected):
    assert recurring._advance(day, frequency) == expected


async def test_existing_snapshot_schema(test_sessionmaker):
    async with test_sessionmaker() as session:
        rows = (await session.execute(text("SELECT column_name, data_type, character_maximum_length, is_nullable FROM information_schema.columns WHERE table_name='recurring_transactions' AND column_name IN ('amount_currency','transfer_amount_currency')"))).all()
        assert set(rows) == {("amount_currency", "character varying", 3, "NO"), ("transfer_amount_currency", "character varying", 3, "YES")}
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == "e91b7a42c603"


async def test_existing_migration_income_without_settings(test_sessionmaker, account_id):
    # Snapshots were already introduced with native invariants, not this stage.
    migrate("downgrade", "f83a2c91d607")
    try:
        async with test_sessionmaker() as session:
            await session.execute(text("DELETE FROM app_settings"))
            await session.execute(text("UPDATE accounts SET currency='VND' WHERE id=:id"), {"id": account_id})
            await session.execute(text("""INSERT INTO recurring_transactions
                (account_id,type,amount,description,frequency,anchor_date,is_active)
                VALUES (:id,'INCOME',12.123456,'Legacy','MONTHLY','2026-01-01',true)"""), {"id": account_id})
            await session.commit()
        for _ in range(2):
            migrate("upgrade", "a72e9b4c106d")
            await test_sessionmaker.kw["bind"].dispose()
            async with test_sessionmaker() as session:
                row = (await session.execute(text("SELECT amount,amount_currency,transfer_amount,transfer_amount_currency FROM recurring_transactions"))).one()
                assert tuple(row) == (Decimal("12.123456"), "VND", None, None)
            migrate("downgrade", "f83a2c91d607")
    finally:
        migrate("upgrade", "head")
