"""Native transfer amounts, final-state PATCH and real PostgreSQL row locking."""
import asyncio
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import event, select, text

from app.api.routes import transactions as routes
from app.core.money import effective_transfer_rate
from app.models.account import Account
from app.services import account_service
from tests.helpers import txn_payload


async def account(client, currency="USD"):
    response = await client.post("/accounts", json={"name": currency, "currency": currency})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def transfer(client, source, destination, **fields):
    return await client.post("/transactions", json=txn_payload(
        source, type="transfer", transfer_account_id=destination, **fields))


async def read_transaction(client, id_):
    response = await client.get("/transactions")
    assert response.status_code == 200, response.text
    return next(t for t in response.json()["items"] if t["id"] == id_)


@pytest.mark.parametrize("explicit", [False, True])
async def test_same_currency_create_normalizes_exact_amount(client, account_id, explicit):
    destination = await account(client)
    fields = {"transfer_amount": "1.123456"} if explicit else {}
    response = await transfer(client, account_id, destination, amount="1.123456", **fields)
    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(body["transfer_amount"]) == Decimal(body["amount"]) == Decimal("1.123456")
    assert body["account"]["currency"] == body["transfer_account"]["currency"] == "USD"
    assert Decimal(body["effective_rate"]) == 1
    assert body["complete"] and not body["data_issues"]
    assert Decimal((await read_transaction(client, body["id"]))["transfer_amount"]) == Decimal("1.123456")


@pytest.mark.parametrize("destination_currency,amount", [("USD", "11"), ("VND", None), ("VND", "1.1234567"), ("VND", "0"), ("VND", "-1"), ("VND", "NaN"), ("VND", "Infinity"), ("VND", "100000000000000")])
async def test_invalid_destination_amount_rejected(client, account_id, destination_currency, amount):
    destination = await account(client, destination_currency)
    response = await transfer(client, account_id, destination, transfer_amount=amount)
    assert response.status_code in (400, 422), response.text
    assert (await client.get("/transactions")).json()["total"] == 0


async def test_cross_currency_create_and_native_account_response(client):
    source, destination = await account(client, "RUB"), await account(client, "VND")
    response = await transfer(client, source, destination, amount="10000", transfer_amount="3000000")
    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(body["amount"]) == 10000
    assert Decimal(body["transfer_amount"]) == 3000000
    assert Decimal(body["effective_rate"]) == 300
    assert body["complete"] and body["missing_rates"] == body["data_issues"] == []
    # Account PATCH returns a live balance immediately, without conversion.
    response = await client.patch(f"/accounts/{destination}", json={"name": "Destination"})
    assert response.status_code == 200 and Decimal(response.json()["balance"]) == 3000000
    balances = {a["id"]: a for a in (await client.get("/accounts")).json()}
    assert Decimal(balances[source]["balance"]) == -10000
    assert Decimal(balances[destination]["balance"]) == 3000000


async def test_precise_destination_amount_and_rate(client, account_id):
    destination = await account(client, "VND")
    response = await transfer(client, account_id, destination, amount="3", transfer_amount="1.123456")
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["transfer_amount"]) == Decimal("1.123456")
    assert isinstance(response.json()["effective_rate"], str)
    with localcontext() as context:
        context.prec = 80
        assert Decimal(response.json()["effective_rate"]) == Decimal("1.123456") / Decimal(3)
    with localcontext() as context:
        context.prec = 6
        assert effective_transfer_rate(Decimal("10000"), Decimal("3000000")) == Decimal(300)
    assert effective_transfer_rate(Decimal(0), Decimal(1)) is None
    assert effective_transfer_rate(Decimal("NaN"), Decimal(1)) is None
    assert effective_transfer_rate(Decimal(1), None) is None


async def test_create_rejects_irrelevant_or_missing_accounts(client, account_id):
    for type_ in ("income", "expense"):
        response = await client.post("/transactions", json=txn_payload(account_id, type=type_, transfer_amount="1"))
        assert response.status_code == 422
    assert (await transfer(client, account_id, account_id)).status_code == 422
    assert (await transfer(client, account_id, None)).status_code == 422
    assert (await transfer(client, account_id, 999999)).status_code == 400
    assert (await transfer(client, 999999, account_id)).status_code == 400


async def test_patch_account_pairs_and_authoritative_amounts(client, account_id):
    same, cross, other_cross = await account(client), await account(client, "VND"), await account(client, "RUB")
    created = (await transfer(client, account_id, same)).json()
    url = f"/transactions/{created['id']}"
    response = await client.patch(url, json={"amount": "20.123456"})
    assert response.status_code == 200 and Decimal(response.json()["transfer_amount"]) == Decimal("20.123456")
    assert (await client.patch(url, json={"transfer_account_id": cross})).status_code == 400
    assert (await read_transaction(client, created["id"]))["transfer_account_id"] == same
    response = await client.patch(url, json={"transfer_account_id": cross, "transfer_amount": "3000"})
    assert response.status_code == 200 and Decimal(response.json()["transfer_amount"]) == 3000
    response = await client.patch(url, json={"amount": "30"})
    assert response.status_code == 200 and Decimal(response.json()["transfer_amount"]) == 3000
    assert Decimal(response.json()["effective_rate"]) == 100
    for patch in ({"transfer_account_id": other_cross}, {"account_id": other_cross}):
        assert (await client.patch(url, json=patch)).status_code == 400
    response = await client.patch(url, json={"account_id": other_cross, "transfer_amount": "4000"})
    assert response.status_code == 200
    # Cross -> same ignores the inherited credit and normalizes to source.
    response = await client.patch(url, json={"account_id": account_id, "transfer_account_id": same})
    assert response.status_code == 200 and Decimal(response.json()["transfer_amount"]) == 30
    assert (await client.patch(url, json={"transfer_amount": "31"})).status_code == 400


@pytest.mark.parametrize("type_", ["income", "expense"])
async def test_patch_type_transitions_and_ordinary_edits(client, account_id, type_):
    destination = await account(client, "VND")
    created = (await transfer(client, account_id, destination, transfer_amount="3000")).json()
    url = f"/transactions/{created['id']}"
    assert (await client.patch(url, json={"type": type_, "transfer_amount": "3000"})).status_code == 400
    response = await client.patch(url, json={"type": type_})
    assert response.status_code == 200
    assert response.json()["transfer_account_id"] is response.json()["transfer_amount"] is None
    assert response.json()["effective_rate"] is None
    response = await client.patch(url, json={"amount": "1.123456", "description": "Edited"})
    assert response.status_code == 200 and Decimal(response.json()["amount"]) == Decimal("1.123456")
    assert (await client.patch(url, json={"type": "transfer", "transfer_account_id": destination})).status_code == 400
    response = await client.patch(url, json={"type": "transfer", "transfer_account_id": destination, "transfer_amount": "123.123456"})
    assert response.status_code == 200
    for field in ("amount", "account_id", "type"):
        assert (await client.patch(url, json={field: None})).status_code == 422


async def test_legacy_unresolved_reads_and_financial_reconciliation(client, account_id, test_sessionmaker):
    destination = await account(client, "VND")
    created = (await transfer(client, account_id, destination, transfer_amount="3000")).json()
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": created["id"]})
        await session.commit()
    body = await read_transaction(client, created["id"])
    assert body["transfer_amount"] is body["effective_rate"] is None
    assert body["complete"] is False
    assert body["data_issues"] == [{"code": "transfer_amount_unresolved"}]
    balances = {a["id"]: a for a in (await client.get("/accounts")).json()}
    assert Decimal(balances[account_id]["balance"]) == -10
    assert balances[destination]["balance"] is None and balances[destination]["complete"] is False
    url = f"/transactions/{created['id']}"
    response = await client.patch(url, json={"description": "Legacy", "notes": "Retained"})
    assert response.status_code == 200 and response.json()["transfer_amount"] is None
    for patch in ({"type": "transfer"}, {"account_id": account_id}, {"transfer_account_id": destination},
                  {"amount": "10"}, {"transfer_amount": None}, {"category_id": None}, {"splits": []}, {"date": "2026-01-16"}):
        assert (await client.patch(url, json=patch)).status_code == 400, patch
    response = await client.patch(url, json={"transfer_amount": "3000"})
    assert response.status_code == 200 and response.json()["complete"] is True


async def test_bulk_validation_is_atomic(client, account_id):
    same, cross = await account(client), await account(client, "VND")
    items = [txn_payload(account_id, type="income"),
             txn_payload(account_id, type="transfer", transfer_account_id=same),
             txn_payload(account_id, type="transfer", transfer_account_id=cross, transfer_amount="3000")]
    response = await client.post("/transactions/bulk", json={"items": items})
    assert response.status_code == 201 and response.json()["created"] == 3
    items[-1].pop("transfer_amount")
    assert (await client.post("/transactions/bulk", json={"items": items})).status_code == 400
    assert (await client.get("/transactions")).json()["total"] == 3


async def test_account_delete_is_conflict_until_last_transaction_deleted(client, account_id):
    destination = await account(client)
    created = (await transfer(client, account_id, destination)).json()
    for id_ in (account_id, destination):
        response = await client.delete(f"/accounts/{id_}")
        assert response.status_code == 409 and response.json()["detail"] == "account_delete_requires_empty_history"
        assert (await client.patch(f"/accounts/{id_}", json={"is_archived": True})).status_code == 200
    assert (await client.delete(f"/transactions/{created['id']}")).status_code == 204
    for id_ in (account_id, destination):
        assert (await client.patch(f"/accounts/{id_}", json={"currency": "VND"})).status_code == 200
        assert (await client.delete(f"/accounts/{id_}")).status_code == 204


async def test_lock_order_and_patch_union(client, account_id, test_sessionmaker):
    second, third = await account(client), await account(client)
    locked_parameters = []
    engine = test_sessionmaker.kw["bind"].sync_engine

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "FROM accounts" in statement and "FOR UPDATE" in statement:
            assert "ORDER BY accounts.id" in statement
            locked_parameters.append(tuple(parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        created = await transfer(client, third, second)
        assert created.status_code == 201
        assert locked_parameters.pop() == (second, third)
        response = await client.patch(f"/transactions/{created.json()['id']}", json={"account_id": account_id})
        assert response.status_code == 200
        assert locked_parameters.pop() == (account_id, second, third)
        assert (await client.delete(f"/transactions/{created.json()['id']}")).status_code == 204
        assert locked_parameters.pop() == (account_id, second)
        response = await client.post("/transactions/bulk", json={"items": [
            txn_payload(third, type="transfer", transfer_account_id=second),
            txn_payload(second, type="transfer", transfer_account_id=account_id),
        ]})
        assert response.status_code == 201
        assert locked_parameters == [(account_id, second, third)]
    finally:
        event.remove(engine, "before_cursor_execute", capture)


async def wait_blocked(sessionmaker, blocker_pid):
    """Observe PostgreSQL's lock graph; do not infer blocking from a sleep."""
    async with sessionmaker() as observer:
        for _ in range(300):
            blocked = await observer.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid=ANY(pg_blocking_pids(pid)))"
            ), {"pid": blocker_pid})
            if blocked:
                return
            await observer.commit()  # refresh pg_stat_activity snapshot on the next poll
            await asyncio.sleep(0.01)
    pytest.fail("Expected a PostgreSQL lock wait")


@pytest.mark.parametrize("operation", ["create", "patch", "bulk", "recurring"])
async def test_writer_uses_currency_after_waiting_for_real_currency_patch(client, account_id, test_sessionmaker, monkeypatch, operation):
    target = await account(client)
    old_destination = await account(client)
    original_transaction = None
    if operation == "patch":
        original_transaction = (await transfer(client, account_id, old_destination)).json()
    recurring_id = None
    if operation == "recurring":
        response = await client.post("/recurring", json={
            "account_id": account_id, "transfer_account_id": target, "type": "transfer", "amount": "10",
            "description": "Recurring", "frequency": "monthly", "anchor_date": "2026-01-01",
        })
        assert response.status_code == 201
        recurring_id = response.json()["id"]
    acquired, release = asyncio.Event(), asyncio.Event()
    pid = None
    original_check = account_service.account_has_history

    async def hold_currency_update(session, id_):
        nonlocal pid
        if id_ == target:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            acquired.set()
            await release.wait()
        return await original_check(session, id_)

    monkeypatch.setattr(account_service, "account_has_history", hold_currency_update)
    changer = asyncio.create_task(client.patch(f"/accounts/{target}", json={"currency": "RUB"}))
    writer = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        if operation == "create":
            request = transfer(client, account_id, target)
        elif operation == "recurring":
            request = client.post(f"/recurring/{recurring_id}/post")
        elif operation == "bulk":
            request = client.post("/transactions/bulk", json={"items": [txn_payload(account_id, type="transfer", transfer_account_id=target)]})
        else:
            request = client.patch(f"/transactions/{original_transaction['id']}", json={"transfer_account_id": target})
        writer = asyncio.create_task(request)
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        assert (await asyncio.wait_for(changer, 3)).status_code == 200
        response = await asyncio.wait_for(writer, 3)
        if operation == "recurring":
            assert response.status_code == 409 and response.json()["detail"] == "recurring_currency_mismatch"
        else:
            assert response.status_code == 400 and response.json()["detail"] == "cross_currency_transfer_amount_required"
        if original_transaction:
            assert (await read_transaction(client, original_transaction["id"]))["transfer_account_id"] == old_destination
        else:
            assert (await client.get("/transactions")).json()["total"] == 0
    finally:
        release.set()
        await asyncio.gather(changer, *([writer] if writer else []), return_exceptions=True)


async def test_first_create_blocks_currency_change_until_history_is_committed(client, account_id, test_sessionmaker, monkeypatch):
    acquired, release = asyncio.Event(), asyncio.Event()
    pid = None
    original_validate = routes._ensure_category_matches_type

    async def pause_writer(session, category_id, type_):
        nonlocal pid
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        acquired.set()
        await release.wait()
        return await original_validate(session, category_id, type_)

    monkeypatch.setattr(routes, "_ensure_category_matches_type", pause_writer)
    writer = asyncio.create_task(client.post("/transactions", json=txn_payload(account_id)))
    changer = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        changer = asyncio.create_task(client.patch(f"/accounts/{account_id}", json={"currency": "RUB"}))
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        assert (await asyncio.wait_for(writer, 3)).status_code == 201
        response = await asyncio.wait_for(changer, 3)
        assert response.status_code == 409 and response.json()["detail"] == "currency_change_requires_empty_history"
    finally:
        release.set()
        await asyncio.gather(writer, *([changer] if changer else []), return_exceptions=True)


async def test_delete_holds_accounts_until_history_removal_commits(client, account_id, test_sessionmaker, monkeypatch):
    destination = await account(client)
    created = (await transfer(client, account_id, destination)).json()
    acquired, release = asyncio.Event(), asyncio.Event()
    pid = None
    original_lock = routes.lock_transaction

    async def pause_delete(session, id_, requested_ids=()):
        nonlocal pid
        result = await original_lock(session, id_, requested_ids)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        acquired.set()
        await release.wait()
        return result

    monkeypatch.setattr(routes, "lock_transaction", pause_delete)
    deleting = asyncio.create_task(client.delete(f"/transactions/{created['id']}"))
    changer = None
    try:
        await asyncio.wait_for(acquired.wait(), 3)
        changer = asyncio.create_task(client.patch(f"/accounts/{destination}", json={"currency": "VND"}))
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        assert (await asyncio.wait_for(deleting, 3)).status_code == 204
        response = await asyncio.wait_for(changer, 3)
        assert response.status_code == 200 and response.json()["has_monetary_history"] is False
    finally:
        release.set()
        await asyncio.gather(deleting, *([changer] if changer else []), return_exceptions=True)


async def test_competing_patch_reloads_references_without_extending_lock_order(client, account_id, test_sessionmaker):
    old, first, second = await account(client), await account(client), await account(client)
    created = (await transfer(client, account_id, old)).json()
    tasks = []
    async with test_sessionmaker() as blocker:
        await blocker.execute(select(Account).where(Account.id == account_id).with_for_update())
        try:
            for destination in (first, second):
                tasks.append(asyncio.create_task(client.patch(f"/transactions/{created['id']}", json={"transfer_account_id": destination})))
                # Both must discover old references before either can write.
                async with test_sessionmaker() as observer:
                    for _ in range(300):
                        count = await observer.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE cardinality(pg_blocking_pids(pid)) > 0"))
                        if count >= len(tasks):
                            break
                        await observer.commit()
                        await asyncio.sleep(0.01)
                    assert count >= len(tasks)
            await blocker.commit()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert sorted(r.status_code for r in results) == [200, 409]
            conflict = next(r for r in results if r.status_code == 409)
            assert conflict.json()["detail"] == "transaction_accounts_changed_retry"
        finally:
            await blocker.rollback()
            await asyncio.gather(*tasks, return_exceptions=True)


async def test_recurring_supported_post_joins_native_transfer_protocol(client, account_id):
    destination = await account(client)
    response = await client.post("/recurring", json={
        "account_id": account_id, "transfer_account_id": destination, "type": "transfer", "amount": "1.123456",
        "description": "Recurring", "frequency": "monthly", "anchor_date": "2026-01-01",
    })
    assert response.status_code == 201
    assert (await client.post(f"/recurring/{response.json()['id']}/post")).status_code == 201
    transaction = (await client.get("/transactions")).json()["items"][0]
    assert Decimal(transaction["transfer_amount"]) == Decimal("1.123456")
    assert transaction["complete"]


async def test_recurring_busy_template_refuses_without_inverse_lock_wait(client, account_id, test_sessionmaker):
    response = await client.post("/recurring", json={
        "account_id": account_id, "type": "expense", "amount": "10", "description": "Busy",
        "frequency": "monthly", "anchor_date": "2026-01-01",
    })
    assert response.status_code == 201
    id_ = response.json()["id"]
    async with test_sessionmaker() as editing:
        await editing.execute(text("SELECT id FROM recurring_transactions WHERE id=:id FOR UPDATE"), {"id": id_})
        response = await asyncio.wait_for(client.post(f"/recurring/{id_}/post"), 3)
        assert response.status_code == 409
        assert response.json()["detail"] == "recurring_instruction_changed_retry"
    assert (await client.get("/transactions")).json()["total"] == 0
