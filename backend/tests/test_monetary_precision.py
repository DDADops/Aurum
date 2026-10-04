"""Precision boundaries at API and PostgreSQL migration boundaries."""
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, text

from app.core.money import Money, Valuation, sum_money
from app.models.asset import Asset, AssetValuation
from app.models.budget import Budget
from app.models.crypto import CryptoHolding, CryptoPortfolio, CryptoTransaction
from app.models.enums import AssetClass, CryptoTransactionType, RecurringFrequency, TransactionType
from app.models.goal import Goal, GoalContribution
from app.models.recurring import RecurringTransaction
from app.models.transaction import Transaction, TransactionSplit
from app.schemas.asset import AssetUpdate, AssetValuationCreate
from app.schemas.goal import GoalContributionCreate
from app.schemas.settings import AppSettingsUpdate

PREVIOUS = "d1a6f4c8b729"
HEAD = "f83a2c91d607"
COLUMNS = [
    ("transactions", "amount", 20, 6),
    ("transaction_splits", "amount", 20, 6),
    ("recurring_transactions", "amount", 20, 6),
    ("assets", "monthly_cash_flow", 20, 6),
    ("asset_valuations", "value", 38, 18),
    ("budgets", "monthly_limit", 20, 6),
    ("goals", "target_amount", 20, 6),
    ("goal_contributions", "amount", 20, 6),
    ("app_settings", "idle_cash_threshold_amount", 20, 6),
]


def test_shared_decimal_bounds_and_domain_rules():
    money = TypeAdapter(Money)
    for value in ["0", "-1.123456", "99999999999999.999999", "1.1234560"]:
        assert money.validate_python(value) == Decimal(value)
    for value in ["1.1234567", "100000000000000", "NaN", "Infinity", "-Infinity"]:
        with pytest.raises(ValidationError):
            money.validate_python(value)
    valuation = TypeAdapter(Valuation)
    value = "99999999999999999999.123456789123456789"
    assert valuation.validate_python(value) == Decimal(value)
    for value in ["0.0000000000000000001", "100000000000000000000", "NaN", "Infinity"]:
        with pytest.raises(ValidationError):
            valuation.validate_python(value)
    assert AssetUpdate(monthly_cash_flow="-0.000001").monthly_cash_flow == Decimal("-0.000001")
    assert AssetValuationCreate(value="0").value == 0
    assert AppSettingsUpdate(idle_cash_threshold_amount="0").idle_cash_threshold_amount == 0
    assert GoalContributionCreate(amount="-0.000001", date=date.today()).amount < 0
    with pytest.raises(ValidationError):
        GoalContributionCreate(amount="0", date=date.today())
    with localcontext() as ctx:
        ctx.prec = 6
        assert sum_money([Decimal("99999999999999.000001"), Decimal("0.999998")]) == Decimal("99999999999999.999999")


async def test_transaction_precision_roundtrip_and_splits(client, account_id, categories):
    payload = dict(account_id=account_id, type="expense", amount="1.123456", description="Precision", date="2026-01-01")
    response = await client.post("/transactions", json=payload)
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["amount"]) == Decimal("1.123456")
    for value in ["1.1234567", "100000000000000", "NaN", "Infinity", "0", "-1"]:
        assert (await client.post("/transactions", json={**payload, "amount": value})).status_code == 422
    response = await client.post("/transactions", json={**payload, "amount": "99999999999999.999999"})
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["amount"]) == Decimal("99999999999999.999999")
    ids = [c["id"] for c in categories.values() if c["kind"] == "expense"][:2]
    child = await client.post("/categories", json=dict(name="Precision child", kind="expense", color="#112233", parent_id=ids[0]))
    assert child.status_code == 201, child.text
    ids[1] = child.json()["id"]
    payload["splits"] = [dict(category_id=ids[0], amount="0.123456"), dict(category_id=ids[1], amount="1.000000")]
    response = await client.post("/transactions", json=payload)
    assert response.status_code == 201, response.text
    assert [Decimal(s["amount"]) for s in response.json()["splits"]] == [Decimal("0.123456"), Decimal("1")]
    assert (await client.patch(f"/transactions/{response.json()['id']}", json={"amount": "1.123457"})).status_code == 400
    payload["amount"] = "1.123457"
    assert (await client.post("/transactions", json=payload)).status_code == 422


async def test_valuation_high_precision_roundtrip(client):
    value = "99999999999999999999.123456789123456789"
    response = await client.post("/assets", json=dict(name="Precision", asset_class="other", value=value, monthly_cash_flow="-1.123456"))
    assert response.status_code == 201, response.text
    assert Decimal(response.json()["current_value"]) == Decimal(value)
    assert Decimal(response.json()["monthly_cash_flow"]) == Decimal("-1.123456")
    response = await client.get(f"/assets/{response.json()['id']}/valuations")
    assert Decimal(response.json()[0]["value"]) == Decimal(value)


async def test_precision_backup_roundtrip_and_rejection(client, account_id):
    response = await client.post("/transactions", json=dict(
        account_id=account_id, type="expense", amount="1.123456",
        description="Precision backup", date="2026-01-01",
    ))
    assert response.status_code == 201
    value = "99999999999999999999.123456789123456789"
    response = await client.post("/assets", json=dict(name="Precision", asset_class="other", value=value))
    assert response.status_code == 201
    payload = (await client.get("/backup/export")).json()
    assert payload["aurum_backup_version"] == 1
    response = await client.post("/backup/import", json=payload)
    assert response.status_code == 200, response.text
    restored = (await client.get("/backup/export")).json()
    assert restored["transactions"] == payload["transactions"]
    assert restored["asset_valuations"] == payload["asset_valuations"]
    payload["transactions"][0]["amount"] = "1.1234567"
    assert (await client.post("/backup/import", json=payload)).status_code == 422
    assert (await client.get("/backup/export")).json()["transactions"] == restored["transactions"]


def migrate(direction, revision, success=True):
    result = subprocess.run(
        [sys.executable, "-m", "alembic", direction, revision],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "AURUM_POSTGRES_DB": "aurum_test"},
        capture_output=True, text=True,
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return result.stderr


async def snapshot(sessionmaker):
    # Reconnect after external DDL; asyncpg caches prepared result metadata.
    await sessionmaker.kw["bind"].dispose()
    async with sessionmaker() as session:
        values = {}
        types = {}
        for table, column, _, _ in COLUMNS:
            values[table] = list((await session.execute(text(f'SELECT "{column}" FROM "{table}" ORDER BY id'))).scalars())
            types[table] = (await session.execute(text("SELECT numeric_precision, numeric_scale, is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name=:table AND column_name=:column"), dict(table=table, column=column))).one()
        revision = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
        crypto = (await session.execute(text("SELECT quantity, price_per_unit FROM crypto_transactions"))).one()
        return values, types, revision, crypto


async def test_precision_migration_roundtrip_and_loss_preflight(test_sessionmaker, account_id, categories):
    # Dedicated aurum_test database only; all sessions close before DDL.
    migrate("downgrade", PREVIOUS)
    try:
        async with test_sessionmaker() as session:
            category_id = next(c["id"] for c in categories.values() if c["kind"] == "expense")
            asset = Asset(name="Old asset", asset_class=AssetClass.OTHER, monthly_cash_flow=Decimal("-12.34"))
            portfolio = CryptoPortfolio(name="Precision")
            transaction_id = await session.scalar(insert(Transaction.__table__).values(
                account_id=account_id, type=TransactionType.EXPENSE, amount=Decimal("123.45"),
                description="Old data", date=date.today()).returning(Transaction.id))
            goal_id = await session.scalar(insert(Goal.__table__).values(
                name="Old goal", target_amount=Decimal("999999999999.99")).returning(Goal.id))
            await session.execute(insert(Budget.__table__).values(category_id=category_id, monthly_limit=Decimal("100")))
            await session.execute(insert(RecurringTransaction.__table__).values(
                account_id=account_id, type=TransactionType.EXPENSE, amount=Decimal("123.45"),
                description="Old template", frequency=RecurringFrequency.MONTHLY, anchor_date=date.today()))
            session.add_all([asset, portfolio, Asset(name="Null flow", asset_class=AssetClass.OTHER)])
            await session.flush()
            session.add_all([
                TransactionSplit(transaction_id=transaction_id, category_id=category_id, amount=Decimal("123.45")),
                GoalContribution(goal_id=goal_id, amount=Decimal("-12.34"), date=date.today()),
            ])
            await session.flush()
            # Historical schema predates provenance; do not use current ORM columns.
            await session.execute(text("INSERT INTO asset_valuations (asset_id,value,as_of_date) VALUES (:id,999999999999.99,:day)"), {"id": asset.id, "day": date.today()})
            await session.execute(text("INSERT INTO crypto_holdings (asset_id,portfolio_id,coingecko_id,symbol,name,last_price) VALUES (:id,:portfolio,'bitcoin','BTC','Bitcoin',1.123456789123456789)"), {"id": asset.id, "portfolio": portfolio.id})
            await session.execute(text("INSERT INTO crypto_transactions (asset_id,type,quantity,price_per_unit,date) VALUES (:id,'BUY',0.123456789123456789,1.123456789123456789,:day)"), {"id": asset.id, "day": date.today()})
            await session.commit()
        old_values, old_types, revision, crypto = await snapshot(test_sessionmaker)
        assert revision == PREVIOUS
        assert all(t[:2] == (14, 2) for t in old_types.values())
        migrate("upgrade", HEAD)
        values, types, revision, new_crypto = await snapshot(test_sessionmaker)
        assert values == old_values and crypto == new_crypto and revision == HEAD
        for table, _, precision, scale in COLUMNS:
            assert types[table] == (precision, scale, old_types[table][2])
        migrate("downgrade", PREVIOUS)
        assert await snapshot(test_sessionmaker) == (old_values, old_types, PREVIOUS, crypto)
        migrate("upgrade", HEAD)
        # Exercise every widened column, including the last one in the preflight:
        # a failure must leave all column types and the revision unchanged.
        for table, column, _, scale in COLUMNS:
            for unsafe in ["1.123456" if scale == 6 else "1.000000000000000001", "1000000000000"]:
                async with test_sessionmaker() as session:
                    await session.execute(text(f'UPDATE "{table}" SET "{column}"=:value WHERE id=(SELECT min(id) FROM "{table}")'), {"value": Decimal(unsafe)})
                    await session.commit()
                before = await snapshot(test_sessionmaker)
                error = migrate("downgrade", PREVIOUS, success=False)
                assert f"Cannot downgrade {table}.{column}" in error
                assert await snapshot(test_sessionmaker) == before
                async with test_sessionmaker() as session:
                    await session.execute(text(f'UPDATE "{table}" SET "{column}"=:value WHERE id=(SELECT min(id) FROM "{table}")'), {"value": old_values[table][0]})
                    await session.commit()
    finally:
        migrate("upgrade", "head")
