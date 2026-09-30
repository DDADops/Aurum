"""Persistence-step guarantees; transaction-writer locking is deferred to commit 5."""
import asyncio
import builtins
import runpy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import delete, insert, select, text

from app.models.account import Account
from app.models.asset import Asset, AssetValuation
from app.models.budget import Budget
from app.models.crypto import CryptoHolding, CryptoPortfolio, CryptoTransaction
from app.models.enums import AssetClass, CryptoTransactionType, RecurringFrequency, TransactionType
from app.models.goal import Goal
from app.models.recurring import RecurringTransaction
from app.models.transaction import Transaction
from tests.helpers import txn_payload
from tests.test_monetary_precision import migrate

PREVIOUS = "f83a2c91d607"
HEAD = "a72e9b4c106d"


async def new_account(client, currency="USD"):
    response = await client.post("/accounts", json={"name": currency, "currency": currency})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def template(client, source, **overrides):
    response = await client.post("/recurring", json={
        "account_id": source, "type": "expense", "amount": "12.123456", "description": "Instruction",
        "frequency": "monthly", "anchor_date": "2026-01-01", **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def test_account_history_is_dynamic_and_includes_incoming(client, account_id):
    destination = await new_account(client)
    incoming = await client.post("/transactions", json=txn_payload(
        account_id, type="transfer", transfer_account_id=destination, amount="10"))
    assert incoming.status_code == 201
    outgoing = await client.post("/transactions", json=txn_payload(destination, type="expense", amount="10"))
    assert outgoing.status_code == 201
    accounts = {a["id"]: a for a in (await client.get("/accounts")).json()}
    assert Decimal(accounts[destination]["balance"]) == 0
    assert accounts[destination]["has_monetary_history"] is True
    for id_ in (account_id, destination):
        response = await client.patch(f"/accounts/{id_}", json={"currency": "RUB"})
        assert response.status_code == 409
        assert response.json()["detail"] == "currency_change_requires_empty_history"
    await client.delete(f"/transactions/{outgoing.json()['id']}")
    # Incoming-only history still blocks, independent of balance.
    assert (await client.patch(f"/accounts/{destination}", json={"currency": "VND"})).status_code == 409
    assert (await client.patch(f"/accounts/{destination}", json={"currency": "usd", "name": "Rename"})).status_code == 200
    await client.delete(f"/transactions/{incoming.json()['id']}")
    changed = await client.patch(f"/accounts/{destination}", json={"currency": "vnd"})
    assert changed.status_code == 200 and changed.json()["currency"] == "VND"
    assert changed.json()["has_monetary_history"] is False


async def test_unused_recurring_snapshot_does_not_lock_account(client, account_id):
    instruction = await template(client, account_id)
    assert instruction["amount_currency"] == "USD"
    response = await client.patch(f"/accounts/{account_id}", json={"currency": "rub"})
    assert response.status_code == 200
    assert response.json()["has_monetary_history"] is False
    current = (await client.get("/recurring")).json()[0]
    assert current["amount_currency"] == "USD"
    assert (await client.post(f"/recurring/{instruction['id']}/post")).status_code == 409
    assert (await client.get("/transactions")).json()["total"] == 0


@pytest.mark.parametrize("code", ["USD", "RUB", "VND", "usd", "rub", "vnd"])
async def test_supported_currency_normalization(client, code):
    response = await client.post("/accounts", json={"name": "Currency", "currency": code})
    assert response.status_code == 201
    assert response.json()["currency"] == code.upper()
    response = await client.patch("/settings", json={"currency": code})
    assert response.status_code == 200 and response.json()["currency"] == code.upper()


@pytest.mark.parametrize("code", ["ZZZ", "12$", "US", "USDD", " usd ", None])
async def test_invalid_currency_is_rejected(client, account_id, code):
    assert (await client.patch(f"/accounts/{account_id}", json={"currency": code})).status_code == 422
    assert (await client.patch("/settings", json={"currency": code})).status_code == 422


async def test_budget_goal_currencies_are_persistent(client, categories):
    await client.patch("/settings", json={"currency": "RUB"})
    budget = await client.post("/budgets", json={"category_id": categories["Groceries"]["id"], "monthly_limit": "10"})
    goal = await client.post("/goals", json={"name": "Goal", "target_amount": "20"})
    assert budget.status_code == goal.status_code == 201
    assert budget.json()["currency"] == goal.json()["currency"] == "RUB"
    await client.patch("/settings", json={"currency": "VND"})
    assert (await client.get("/budgets")).json()[0]["currency"] == "RUB"
    assert (await client.get("/goals")).json()[0]["currency"] == "RUB"
    # A new limit explicitly replaces the old instruction; contributions cannot be relabelled.
    changed = await client.patch(f"/budgets/{budget.json()['id']}", json={"currency": "vnd", "monthly_limit": "30"})
    assert changed.status_code == 200 and changed.json()["currency"] == "VND"
    id_ = goal.json()["id"]
    assert (await client.patch(f"/goals/{id_}", json={"currency": "VND"})).status_code == 409
    assert (await client.patch(f"/goals/{id_}", json={"currency": "vnd", "target_amount": "30"})).status_code == 200
    await client.post(f"/goals/{id_}/contributions", json={"amount": "1", "date": "2026-01-01"})
    assert (await client.patch(f"/goals/{id_}", json={"currency": "RUB", "target_amount": "40"})).status_code == 409


async def test_asset_history_cash_flow_and_crypto_cache(client, test_sessionmaker):
    created = await client.post("/assets", json={"name": "Asset", "asset_class": "other", "value": "0", "monthly_cash_flow": "0"})
    assert created.status_code == 201
    id_ = created.json()["id"]
    assert created.json()["has_monetary_history"] is True  # zero valuation is history
    assert (await client.patch(f"/assets/{id_}", json={"currency": "RUB"})).status_code == 409
    async with test_sessionmaker() as session:
        await session.execute(delete(AssetValuation).where(AssetValuation.asset_id == id_))
        portfolio = CryptoPortfolio(name="Empty")
        session.add(portfolio)
        await session.flush()
        session.add(CryptoHolding(asset_id=id_, portfolio_id=portfolio.id, coingecko_id="bitcoin", symbol="BTC", name="Empty", last_price=Decimal("123"), price_change_24h=Decimal("10")))
        await session.commit()
    assert (await client.get("/assets")).json()[0]["has_monetary_history"] is False
    response = await client.patch(f"/assets/{id_}", json={"currency": "RUB"})
    assert response.status_code == 409
    assert response.json()["detail"] == "currency_change_requires_cash_flow_replacement"
    response = await client.patch(f"/assets/{id_}", json={"currency": "RUB", "monthly_cash_flow": "-1.123456"})
    assert response.status_code == 200 and response.json()["has_monetary_history"] is False
    async with test_sessionmaker() as session:
        holding = await session.get(CryptoHolding, id_)
        assert holding.last_price is None and holding.price_change_24h is None
        session.add(CryptoTransaction(asset_id=id_, type=CryptoTransactionType.BUY, quantity=Decimal("1"), price_per_unit=Decimal("1"), date=date.today()))
        await session.commit()
    assert (await client.get("/assets")).json()[0]["has_monetary_history"] is True
    assert (await client.patch(f"/assets/{id_}", json={"currency": "VND", "monthly_cash_flow": None})).status_code == 409
    async with test_sessionmaker() as session:
        await session.execute(delete(CryptoTransaction).where(CryptoTransaction.asset_id == id_))
        await session.commit()
    response = await client.patch(f"/assets/{id_}", json={"currency": "VND", "monthly_cash_flow": None})
    assert response.status_code == 200 and response.json()["monthly_cash_flow"] is None


async def test_account_update_waits_for_parent_lock_then_checks_history(client, account_id, test_sessionmaker):
    # This writer deliberately acquires the future protocol's lock. Real
    # transaction endpoints join that protocol in commit 5, not in this test.
    task = None
    async with test_sessionmaker() as writer:
        await writer.execute(select(Account).where(Account.id == account_id).with_for_update())
        pid = await writer.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(client.patch(f"/accounts/{account_id}", json={"currency": "RUB"}))
        try:
            async with test_sessionmaker() as observer:
                for _ in range(100):
                    blocked = await observer.scalar(text("SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE :pid=ANY(pg_blocking_pids(pid)))"), {"pid": pid})
                    if blocked:
                        break
                    await asyncio.sleep(0.01)
                assert blocked, "currency update must wait on the parent row lock"
            writer.add(Transaction(account_id=account_id, type=TransactionType.INCOME, amount=Decimal("1"), description="First use", date=date.today()))
            await writer.commit()
            response = await asyncio.wait_for(task, 3)
            assert response.status_code == 409
        finally:
            await writer.rollback()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)


async def seed_legacy(sessionmaker, account_id, category_id):
    """Create fixtures while the actual previous schema is installed."""
    await sessionmaker.kw["bind"].dispose()
    async with sessionmaker() as session:
        await session.execute(text("UPDATE accounts SET currency='RUB' WHERE id=:id"), {"id": account_id})
        await session.execute(text("UPDATE app_settings SET currency='VND' WHERE id=1"))
        same = Account(name="RUB destination", currency="RUB")
        cross = Account(name="VND destination", currency="VND")
        session.add_all([same, cross])
        await session.flush()
        for type_, destination in [(TransactionType.TRANSFER, same.id), (TransactionType.TRANSFER, cross.id), (TransactionType.TRANSFER, None), (TransactionType.EXPENSE, None)]:
            await session.execute(insert(Transaction.__table__).values(
                account_id=account_id, transfer_account_id=destination, type=type_, amount=Decimal("12.123456"),
                description="Legacy", date=date.today()))
            await session.execute(insert(RecurringTransaction.__table__).values(
                account_id=account_id, transfer_account_id=destination, type=type_, amount=Decimal("12.123456"),
                description="Legacy", frequency=RecurringFrequency.MONTHLY, anchor_date=date.today()))
        await session.execute(insert(Budget.__table__).values(category_id=category_id, monthly_limit=Decimal("19.123456")))
        await session.execute(insert(Goal.__table__).values(name="Legacy goal", target_amount=Decimal("21.123456")))
        await session.commit()


async def database_snapshot(sessionmaker):
    await sessionmaker.kw["bind"].dispose()
    async with sessionmaker() as session:
        rows = {}
        for table in ("transactions", "recurring_transactions", "budgets", "goals"):
            rows[table] = list((await session.execute(text(f"SELECT row_to_json(t)::text FROM {table} t ORDER BY id"))).scalars())
        types = (await session.execute(text("SELECT table_name,column_name,data_type,numeric_precision,numeric_scale,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"))).all()
        revision = await session.scalar(text("SELECT version_num FROM alembic_version"))
        return rows, types, revision


async def test_native_currency_migration_backfill_and_safe_downgrade(test_sessionmaker, account_id, categories):
    migrate("downgrade", PREVIOUS)
    try:
        await seed_legacy(test_sessionmaker, account_id, categories["Groceries"]["id"])
        old = await database_snapshot(test_sessionmaker)
        migrate("upgrade", HEAD)
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            transactions = (await session.execute(select(Transaction).order_by(Transaction.id))).scalars().all()
            recurring = (await session.execute(select(RecurringTransaction).order_by(RecurringTransaction.id))).scalars().all()
            assert [r.transfer_amount for r in transactions] == [Decimal("12.123456"), None, None, None]
            assert [r.transfer_amount for r in recurring] == [Decimal("12.123456"), None, None, None]
            assert [r.transfer_amount_currency for r in recurring] == ["RUB", "VND", None, None]
            assert all(r.amount_currency == "RUB" for r in recurring)
            assert all(r.amount == Decimal("12.123456") for r in [*transactions, *recurring])
            budget = (await session.execute(select(Budget))).scalar_one()
            goal = (await session.execute(select(Goal))).scalar_one()
            assert budget.currency == goal.currency == "VND"
            assert budget.monthly_limit == Decimal("19.123456")
            assert goal.target_amount == Decimal("21.123456")
        migrated = await database_snapshot(test_sessionmaker)
        migrate("downgrade", PREVIOUS)
        assert await database_snapshot(test_sessionmaker) == old
        migrate("upgrade", HEAD)
        assert await database_snapshot(test_sessionmaker) == migrated
        # Each case is independent and must leave the entire schema/revision
        # intact after rejection, even if an earlier table passed preflight.
        cases = [
            ("transactions", "transfer_amount", "100", 2, None),
            ("transactions", "transfer_amount", "12.123456", 2, None),  # cross-currency 1:1 is still authoritative
            ("recurring_transactions", "transfer_amount", "100", 2, None),
            ("budgets", "currency", "RUB", 1, "VND"),
            ("goals", "currency", "RUB", 1, "VND"),
            ("recurring_transactions", "amount_currency", "USD", 1, "RUB"),
            ("recurring_transactions", "transfer_amount_currency", "USD", 1, "RUB"),
        ]
        for table, column, unsafe, id_, original in cases:
            value = Decimal(unsafe) if column == "transfer_amount" else unsafe
            async with test_sessionmaker() as session:
                await session.execute(text(f"UPDATE {table} SET {column}=:value WHERE id=:id"), {"value": value, "id": id_})
                await session.commit()
            before = await database_snapshot(test_sessionmaker)
            assert "Cannot downgrade native currencies" in migrate("downgrade", PREVIOUS, success=False)
            assert await database_snapshot(test_sessionmaker) == before
            async with test_sessionmaker() as session:
                await session.execute(text(f"UPDATE {table} SET {column}=:value WHERE id=:id"), {"value": original, "id": id_})
                await session.commit()
    finally:
        migrate("upgrade", HEAD)


async def test_migration_missing_settings_uses_frozen_default(test_sessionmaker, account_id, categories, monkeypatch):
    migrate("downgrade", PREVIOUS)
    try:
        await seed_legacy(test_sessionmaker, account_id, categories["Groceries"]["id"])
        async with test_sessionmaker() as session:
            await session.execute(text("DELETE FROM app_settings WHERE id=1"))
            await session.commit()
        monkeypatch.setenv("AURUM_DEFAULT_CURRENCY", "RUB")
        migrate("upgrade", HEAD)
        await test_sessionmaker.kw["bind"].dispose()
        async with test_sessionmaker() as session:
            assert (await session.execute(select(Budget.currency))).scalar_one() == "USD"
            assert (await session.execute(select(Goal.currency))).scalar_one() == "USD"
        migrate("downgrade", PREVIOUS)
        migrate("upgrade", HEAD)
    finally:
        migrate("upgrade", HEAD)


async def test_backup_v1_extension_preserves_authoritative_and_unresolved_fields(client, account_id, categories, test_sessionmaker):
    destination = await new_account(client, "VND")
    response = await client.post("/transactions", json=txn_payload(account_id, type="transfer", transfer_account_id=destination, amount="1"))
    id_ = response.json()["id"]
    async with test_sessionmaker() as session:
        transaction = await session.get(Transaction, id_)
        transaction.transfer_amount = Decimal("25000.123456")
        await session.commit()
    await client.post("/budgets", json={"category_id": categories["Groceries"]["id"], "monthly_limit": "12", "currency": "RUB"})
    await client.post("/goals", json={"name": "Native goal", "target_amount": "30", "currency": "VND"})
    await template(client, account_id, type="transfer", transfer_account_id=destination,
                   amount_currency="USD", transfer_amount="25000.123456", transfer_amount_currency="VND")
    original = (await client.get("/backup/export")).json()
    assert original["aurum_backup_version"] == 1
    assert Decimal(original["transactions"][0]["transfer_amount"]) == Decimal("25000.123456")
    assert (await client.post("/backup/import", json=original)).status_code == 200
    restored = (await client.get("/backup/export")).json()
    for key in ("transactions", "budgets", "goals", "recurring_transactions"):
        assert restored[key] == original[key]
    # Old files omit the fields. Infer only known same-currency transfers;
    # cross-currency amounts stay unresolved, and use the backup's settings.
    for row in original["transactions"]:
        row.pop("transfer_amount")
    for key in ("budgets", "goals"):
        for row in original[key]:
            row.pop("currency")
    for row in original["recurring_transactions"]:
        for key in ("amount_currency", "transfer_amount", "transfer_amount_currency"):
            row.pop(key)
    original["app_settings"]["currency"] = "RUB"
    assert (await client.post("/backup/import", json=original)).status_code == 200
    restored = (await client.get("/backup/export")).json()
    assert restored["transactions"][0]["transfer_amount"] is None
    assert restored["budgets"][0]["currency"] == restored["goals"][0]["currency"] == "RUB"
    assert restored["recurring_transactions"][0]["amount_currency"] == "USD"
    assert restored["recurring_transactions"][0]["transfer_amount"] is None
    assert restored["recurring_transactions"][0]["transfer_amount_currency"] == "VND"
    # Explicit null in an extended v1 file must never be filled as 1:1.
    restored["accounts"] = [{**row, "currency": "USD"} for row in restored["accounts"]]
    assert (await client.post("/backup/import", json=restored)).status_code == 200
    assert (await client.get("/backup/export")).json()["transactions"][0]["transfer_amount"] is None


async def test_recurring_persists_destination_instruction_without_executing_it(client, account_id):
    same = await new_account(client)
    cross = await new_account(client, "VND")
    same_template = await template(client, account_id, type="transfer", transfer_account_id=same)
    assert same_template["transfer_amount_currency"] == "USD"
    assert Decimal(same_template["transfer_amount"]) == Decimal(same_template["amount"])
    unresolved = await template(client, account_id, type="transfer", transfer_account_id=cross)
    assert unresolved["transfer_amount"] is None
    assert unresolved["transfer_amount_currency"] == "VND"
    assert (await client.post(f"/recurring/{unresolved['id']}/post")).status_code == 409
    updated = await client.patch(f"/recurring/{unresolved['id']}", json={"transfer_amount": "25000.123456", "transfer_amount_currency": "vnd"})
    assert updated.status_code == 200
    assert Decimal(updated.json()["transfer_amount"]) == Decimal("25000.123456")
    # An authoritative cross-currency amount can be stored, but the old writer
    # is deliberately not allowed to silently ignore it.
    assert (await client.post(f"/recurring/{unresolved['id']}/post")).status_code == 409
    updated = await client.patch(f"/recurring/{unresolved['id']}", json={"type": "expense", "transfer_account_id": None})
    assert updated.status_code == 200
    assert updated.json()["transfer_amount"] is updated.json()["transfer_amount_currency"] is None
    assert (await client.patch(f"/recurring/{unresolved['id']}", json={"amount_currency": None})).status_code == 422


async def test_legacy_backup_same_currency_backfill(client, account_id):
    destination = await new_account(client)
    await client.post("/transactions", json=txn_payload(account_id, type="transfer", transfer_account_id=destination, amount="1.123456"))
    await template(client, account_id, type="transfer", transfer_account_id=destination)
    backup = (await client.get("/backup/export")).json()
    backup["transactions"][0].pop("transfer_amount")
    for field in ("amount_currency", "transfer_amount", "transfer_amount_currency"):
        backup["recurring_transactions"][0].pop(field)
    assert (await client.post("/backup/import", json=backup)).status_code == 200
    restored = (await client.get("/backup/export")).json()
    assert Decimal(restored["transactions"][0]["transfer_amount"]) == Decimal("1.123456")
    recurring = restored["recurring_transactions"][0]
    assert recurring["amount_currency"] == recurring["transfer_amount_currency"] == "USD"
    assert Decimal(recurring["transfer_amount"]) == Decimal(recurring["amount"])


async def test_migration_currency_helper_needs_no_runtime_imports(test_sessionmaker, monkeypatch):
    original_import = builtins.__import__

    def without_application_imports(name, *args, **kwargs):
        if name == "app" or name.startswith("app."):
            raise AssertionError(f"Historical migration imported runtime module: {name}")
        return original_import(name, *args, **kwargs)

    migration_path = Path(__file__).resolve().parents[1] / "alembic/versions/a72e9b4c106d_persist_native_currency_invariants.py"
    async with test_sessionmaker() as session:
        connection = await session.connection()
        with monkeypatch.context() as isolated:
            isolated.setattr(builtins, "__import__", without_application_imports)
            migration = runpy.run_path(str(migration_path))
            currency = migration["_legacy_currency"]
            await session.execute(text("UPDATE app_settings SET currency='vnd' WHERE id=1"))
            assert await connection.run_sync(currency) == "VND"
            await session.execute(text("DELETE FROM app_settings WHERE id=1"))
            assert await connection.run_sync(currency) == "USD"


async def test_migration_invalid_existing_settings_fails_without_changes(test_sessionmaker, account_id, categories):
    migrate("downgrade", PREVIOUS)
    try:
        await seed_legacy(test_sessionmaker, account_id, categories["Groceries"]["id"])
        async with test_sessionmaker() as session:
            await session.execute(text("UPDATE app_settings SET currency='ZZZ' WHERE id=1"))
            await session.commit()
        before = await database_snapshot(test_sessionmaker)
        error = migrate("upgrade", HEAD, success=False)
        assert "requires a valid legacy default currency" in error
        assert await database_snapshot(test_sessionmaker) == before
        async with test_sessionmaker() as session:
            assert await session.scalar(text("SELECT currency FROM app_settings WHERE id=1")) == "ZZZ"
    finally:
        async with test_sessionmaker() as session:
            await session.execute(text("UPDATE app_settings SET currency='VND' WHERE id=1"))
            await session.commit()
        migrate("upgrade", HEAD)
