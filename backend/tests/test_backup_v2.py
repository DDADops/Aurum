"""Portable authoritative snapshots, conservative v1 and atomic replacement."""
import asyncio
from copy import deepcopy
from datetime import date
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, select, text

from app.models.account import Account
from app.schemas.backup import BACKUP_ROWS, BackupPayload
from app.services import backup_service as backup, crypto_service as crypto
from app.services.fx_provider import FxProvider
from app.services.native_balance_service import account_balances

DAY = "2026-01-01"


def row(key, **values):
    return BACKUP_ROWS[key].model_validate(values).model_dump(mode="json")


async def export(client):
    response = await client.get("/backup/export")
    assert response.status_code == 200, response.text
    return response.json()


def semantic(payload):
    result = deepcopy(payload)
    result.pop("exported_at")
    return result


@pytest.fixture
async def rich(client, account_id, categories):
    payload = await export(client)
    usd = account_id
    payload["accounts"].append(row("accounts", id=101, name="VND", type="checking", currency="VND",
                                   color=None, is_archived=False))
    cat = categories["Groceries"]["id"]
    def transaction(id_, **values):
        return row("transactions", id=id_, account_id=usd, category_id=None, transfer_account_id=None,
                   type="income", amount="1000.123456", description="Fixture", merchant=None, notes=None,
                   date=DAY, **values)
    income = transaction(101)
    expense = {**transaction(102), "type": "expense", "category_id": cat, "amount": "10.123456"}
    cross = {**transaction(103), "type": "transfer", "transfer_account_id": 101,
             "amount": "20.123456", "transfer_amount": "520000.123456"}
    unresolved = {**cross, "id": 104, "transfer_amount": None}
    payload["transactions"] = [income, expense, cross, unresolved]
    payload["recurring_transactions"] = [row("recurring_transactions", id=101, account_id=usd,
        category_id=None, transfer_account_id=101, type="transfer", amount="5.123456", amount_currency="RUB",
        transfer_amount=None, transfer_amount_currency="VND", description="Mismatched snapshot",
        merchant=None, notes=None, frequency="monthly", anchor_date=DAY, last_posted_date=None, is_active=True)]
    payload["budgets"] = [row("budgets", id=101, category_id=cat, currency="VND", monthly_limit="1000000")]
    payload["goals"] = [row("goals", id=101, name="Goal", currency="JPY", target_amount="1000.123456", target_date=None)]
    payload["goal_contributions"] = [row("goal_contributions", id=101, goal_id=101, amount="100.123456", date=DAY, note=None)]
    payload["assets"] = [row("assets", id=101, name="Crypto", asset_class="crypto", currency="RUB", notes=None,
                              monthly_cash_flow="-0.123456"),
                         row("assets", id=102, name="Property", asset_class="real_estate", currency="VND", notes=None)]
    payload["asset_valuations"] = [row("asset_valuations", id=101, asset_id=101, value="1.123456789123456789",
                                       as_of_date=DAY, currency=None, currency_confirmed=False),
                                  row("asset_valuations", id=102, asset_id=102, value="26000000.123456789123456789",
                                       as_of_date=DAY, currency="VND", currency_confirmed=True),
                                  row("asset_valuations", id=103, asset_id=102, value="1200.123456789123456789",
                                       as_of_date="2026-01-02", currency="USD", currency_confirmed=True)]
    payload["crypto_portfolios"] = [row("crypto_portfolios", id=101, name="Portfolio", color=None, is_archived=False)]
    payload["crypto_holdings"] = [row("crypto_holdings", asset_id=101, portfolio_id=101, coingecko_id="bitcoin",
        symbol="BTC", name="Bitcoin", thumb_url=None, network="Bitcoin", last_price="1.123456789123456789",
        price_change_1h="-1.2345")]
    payload["crypto_transactions"] = [row("crypto_transactions", id=101, asset_id=101, type="buy",
        quantity="2.123456789123456789", price_per_unit="1.123456789123456789", quote_currency=None, date=DAY, note=None)]
    payload["fx_rates"] = [row("fx_rates", id=101, base_currency="USD", quote_currency="VND",
                               rate="26000.123456789123456789", as_of_date=DAY, source="fixture",
                               fetched_at="2026-01-01T12:34:56Z"),
                           row("fx_rates", id=102, base_currency="USD", quote_currency="JPY", rate="150",
                               as_of_date="2026-01-02", source="other", fetched_at="2026-01-02T12:34:56Z")]
    payload["app_settings"].update(currency="USD", idle_cash_threshold_amount="10000.123456", fx_max_age_days=9)
    payload["crypto_sync_state"] = {"id": 1, "last_synced_at": "2026-01-02T12:34:56Z"}
    return payload


async def snapshot(sessions):
    async with sessions() as session:
        result = {}
        for model in (*backup.MODELS, backup.AppSettings, backup.CryptoSyncState):
            columns = [c for c in model.__table__.columns if c.name not in ("created_at", "updated_at")]
            result[model.__tablename__] = (await session.execute(select(*columns).order_by(*model.__table__.primary_key.columns))).all()
        result["transaction_tags"] = (await session.execute(select(backup.transaction_tags).order_by("transaction_id", "tag_id"))).all()
        return result


async def restore(client, payload):
    response = await client.post("/backup/import", json=payload)
    assert response.status_code == 200, response.text


async def test_v2_exact_roundtrip_low_precision_offline_and_derived_reads(client, rich, test_sessionmaker, monkeypatch):
    forbidden = AsyncMock(side_effect=AssertionError("Backup must never call a provider"))
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(crypto, "_fetch_market_data", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    await restore(client, rich)
    before = await snapshot(test_sessionmaker)
    first = await export(client)
    assert first["aurum_backup_version"] == 2
    for key, rows in first.items():
        if key not in BACKUP_ROWS:
            continue
        for item in rows:
            for field, definition in BACKUP_ROWS[key].model_fields.items():
                if "Decimal" in str(definition.annotation) and item[field] is not None:
                    assert isinstance(item[field], str)
    assert first["app_settings"]["idle_cash_threshold_amount"] == "10000.123456"
    engine = test_sessionmaker.kw["bind"].sync_engine
    statements = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().lower())
    event.listen(engine, "before_cursor_execute", observe)
    try:
        await export(client)
        assert not any(s.startswith(("insert", "update", "delete")) for s in statements)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    with localcontext() as context:
        context.prec = 6
        await restore(client, first)
        second = await export(client)
    assert semantic(first) == semantic(second)
    assert await snapshot(test_sessionmaker) == before
    assert second["transactions"][3]["transfer_amount"] is None
    assert second["recurring_transactions"][0]["amount_currency"] == "RUB"
    assert second["crypto_transactions"][0]["quote_currency"] is None
    assert second["crypto_holdings"][0]["last_price_currency"] is None
    assert len(second["fx_rates"]) == 2
    async with test_sessionmaker() as session:
        balances = await account_balances(session)
        assert balances[101].balance is None
        assert balances[rich["accounts"][0]["id"]].balance == Decimal("949.753088")
    dashboard = (await client.get("/dashboard/summary?year=2026&month=1")).json()
    assert Decimal(dashboard["net"]) == 990
    flow = (await client.get("/cash-flow?start_date=2026-01-01&end_date=2026-01-31")).json()
    assert Decimal(flow["total_net"]) == 990
    budget = (await client.get("/budgets/status?year=2026&month=1")).json()
    assert budget["items"][0]["currency"] == "VND"
    with localcontext() as context:
        context.prec = 96
        assert Decimal(budget["items"][0]["spent"]) == Decimal("10.123456") * Decimal("26000.123456789123456789")
    goals = (await client.get("/goals")).json()
    assert goals[0]["currency"] == "JPY" and Decimal(goals[0]["current_amount"]) == Decimal("100.123456")
    holdings = (await client.get("/crypto/holdings")).json()
    assert not holdings["holdings"][0]["complete"]
    nw = (await client.get("/net-worth/summary?range=all")).json()
    assert not nw["complete"] and nw["data_issues"]
    await client.patch("/settings", json={"currency": "VND"})
    dashboard = (await client.get("/dashboard/summary?year=2026&month=1")).json()
    report = (await client.get("/reports/category-ranking?kind=expense&start_date=2026-01-01&end_date=2026-01-31")).json()
    with localcontext() as context:
        context.prec = 96
        assert Decimal(dashboard["net"]) == 990 * Decimal(rich["fx_rates"][0]["rate"])
        assert Decimal(report["total_amount"]) == Decimal("10.123456") * Decimal(rich["fx_rates"][0]["rate"])
    forbidden.assert_not_called()


@pytest.mark.parametrize("key,field,value", [
    ("transactions", "amount", "NaN"), ("transactions", "amount", "1.0000001"),
    ("transactions", "amount", "not-money"), ("transactions", "amount", 1.25),
    ("transactions", "amount", "100000000000000"), ("transactions", "account_id", 999999),
    ("accounts", "currency", "ZZZ"), ("accounts", "name", "x" * 101),
    ("fx_rates", "rate", "Infinity"), ("fx_rates", "rate", "-1"),
    ("fx_rates", "rate", "0.0000000000000000001"), ("fx_rates", "base_currency", "VND"),
    ("fx_rates", "fetched_at", "yesterday"), ("asset_valuations", "currency_confirmed", True),
    ("crypto_holdings", "last_price_currency", "USD"),
    ("recurring_transactions", "account_id", 999999),
    ("recurring_transactions", "transfer_account_id", 999999),
    ("transactions", "date", "2026-99-99"), ("transactions", "type", "invalid"),
])
async def test_v2_invalid_before_any_delete(client, rich, test_sessionmaker, key, field, value):
    before = await snapshot(test_sessionmaker)
    rich[key][0][field] = value
    statements = []
    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower().lstrip())
    engine = test_sessionmaker.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", observe)
    try:
        response = await client.post("/backup/import", json=rich)
        assert response.status_code in (400, 422), response.text
        assert not any(s.startswith(("delete", "insert", "update", "lock")) for s in statements)
        assert await snapshot(test_sessionmaker) == before
    finally:
        event.remove(engine, "before_cursor_execute", observe)


@pytest.mark.parametrize("key", ["accounts", "fx_rates", "asset_valuations", "budgets", "tags"])
async def test_duplicate_keys_rejected(client, rich, test_sessionmaker, key):
    before = await snapshot(test_sessionmaker)
    if key == "tags":
        rich[key] = [{"id": 101, "name": "duplicate"}, {"id": 102, "name": "duplicate"}]
    else:
        duplicate = deepcopy(rich[key][0])
        if key != "accounts":
            duplicate["id"] = 999
        rich[key].append(duplicate)
    response = await client.post("/backup/import", json=rich)
    assert response.status_code == 400, response.text
    assert await snapshot(test_sessionmaker) == before


@pytest.mark.parametrize("version", [0, 3, True, "2", None])
async def test_invalid_version(client, rich, test_sessionmaker, version):
    before = await snapshot(test_sessionmaker)
    rich["aurum_backup_version"] = version
    assert (await client.post("/backup/import", json=rich)).status_code == 422
    assert await snapshot(test_sessionmaker) == before


async def test_v2_requires_explicit_provenance_fields(client, rich, test_sessionmaker):
    before = await snapshot(test_sessionmaker)
    rich["transactions"][0].pop("transfer_amount")
    assert (await client.post("/backup/import", json=rich)).status_code == 422
    assert await snapshot(test_sessionmaker) == before


def legacy(rich):
    result = deepcopy(rich)
    result["aurum_backup_version"] = 1
    for key in ("transactions", "recurring_transactions"):
        for item in result[key]:
            item.pop("transfer_amount")
            item.pop("amount_currency", None)
            item.pop("transfer_amount_currency", None)
    for key in ("budgets", "goals"):
        for item in result[key]:
            item.pop("currency")
    return result


@pytest.mark.parametrize("currency", ["VND", None])
async def test_v1_conservative_deterministic_normalization(client, rich, test_sessionmaker, monkeypatch, currency):
    payload = legacy(rich)
    if currency is None:
        payload["app_settings"].pop("currency")
    else:
        payload["app_settings"]["currency"] = currency
    original = deepcopy(payload)
    monkeypatch.setenv("AURUM_DEFAULT_CURRENCY", "RUB")
    await client.patch("/settings", json={"currency": "JPY"})
    normalized = BackupPayload.model_validate(payload).model_dump(mode="json")
    assert payload == original
    await restore(client, payload)
    result = await export(client)
    expected = currency or "USD"
    assert result["app_settings"]["currency"] == result["budgets"][0]["currency"] == result["goals"][0]["currency"] == expected
    assert Decimal(result["app_settings"]["idle_cash_threshold_amount"]) == Decimal("10000.123456")
    assert result["fx_rates"] == []
    assert all(v["currency"] is None and not v["currency_confirmed"] for v in result["asset_valuations"])
    assert result["crypto_transactions"][0]["quote_currency"] is None
    assert result["crypto_holdings"][0]["last_price_currency"] is None
    assert result["crypto_holdings"][0]["last_price_as_of_date"] is None
    assert result["recurring_transactions"][0]["amount_currency"] == "USD"
    assert result["recurring_transactions"][0]["transfer_amount_currency"] == "VND"
    assert result["recurring_transactions"][0]["transfer_amount"] is None
    assert all(t["transfer_amount"] is None for t in result["transactions"] if t["type"] == "transfer")
    monkeypatch.setenv("AURUM_DEFAULT_CURRENCY", "EUR")
    assert BackupPayload.model_validate(payload).model_dump(mode="json") == normalized


@pytest.mark.parametrize("field", ["account_id", "transfer_account_id"])
async def test_v1_recurring_dangling_account_rejected_before_delete(client, rich, test_sessionmaker, field):
    payload = legacy(rich)
    payload["recurring_transactions"][0][field] = 999999
    before = await snapshot(test_sessionmaker)
    response = await client.post("/backup/import", json=payload)
    assert response.status_code == 422, response.text
    assert await snapshot(test_sessionmaker) == before


@pytest.mark.parametrize("version", [1, 2])
async def test_null_destination_is_not_a_dangling_fk(client, rich, version):
    payload = legacy(rich) if version == 1 else rich
    payload["recurring_transactions"][0]["transfer_account_id"] = None
    if version == 2:
        payload["recurring_transactions"][0]["transfer_amount_currency"] = None
    await restore(client, payload)
    result = await export(client)
    template = result["recurring_transactions"][0]
    assert template["transfer_account_id"] is None
    assert template["transfer_amount_currency"] is None and template["transfer_amount"] is None
    await restore(client, result)
    assert semantic(result) == semantic(await export(client))


async def test_v1_same_currency_and_authoritative_cross_amount(client, rich):
    payload = legacy(rich)
    payload["accounts"][1]["currency"] = "USD"
    await restore(client, payload)
    result = await export(client)
    assert result["recurring_transactions"][0]["amount_currency"] == "USD"
    assert Decimal(result["recurring_transactions"][0]["transfer_amount"]) == Decimal("5.123456")
    assert all(t["transfer_amount"] == t["amount"] for t in result["transactions"] if t["type"] == "transfer")
    payload = legacy(rich)
    payload["recurring_transactions"][0]["transfer_amount"] = "123.456789"
    await restore(client, payload)
    assert Decimal((await export(client))["recurring_transactions"][0]["transfer_amount"]) == Decimal("123.456789")


async def test_v1_pre_portfolio_compatibility(client, rich):
    payload = legacy(rich)
    payload.pop("crypto_portfolios")
    payload["crypto_holdings"][0].pop("portfolio_id")
    await restore(client, payload)
    result = await export(client)
    assert result["crypto_holdings"][0]["portfolio_id"] == result["crypto_portfolios"][0]["id"]


async def test_mid_restore_failure_rolls_back_rows_and_sequences(client, rich, test_sessionmaker, monkeypatch):
    before = await snapshot(test_sessionmaker)
    async with test_sessionmaker() as session:
        sequence_before = (await session.execute(text("SELECT last_value, is_called FROM accounts_id_seq"))).one()
    reset = backup._reset_sequence
    async def fail_after_restart(session, model):
        await reset(session, model)
        if model is Account:
            raise RuntimeError("Injected late restore failure")
    monkeypatch.setattr(backup, "_reset_sequence", fail_after_restart)
    assert (await client.post("/backup/import", json=rich)).status_code == 400
    assert await snapshot(test_sessionmaker) == before
    async with test_sessionmaker() as session:
        assert (await session.execute(text("SELECT last_value, is_called FROM accounts_id_seq"))).one() == sequence_before


async def test_sequences_are_ready_for_next_inserts(client, rich, test_sessionmaker):
    await restore(client, rich)
    async with test_sessionmaker() as session:
        for model in backup.MODELS:
            if "id" not in model.__table__.primary_key:
                continue
            next_id = await session.scalar(text(f"SELECT nextval(pg_get_serial_sequence('{model.__tablename__}', 'id'))"))
            assert next_id > max((item["id"] for item in rich[model.__tablename__]), default=0)
    result = await client.post("/accounts", json={"name": "After restore", "currency": "USD"})
    assert result.status_code == 201 and result.json()["id"] > 101


async def test_restore_locks_all_tables_and_blocks_financial_writer(client, rich, test_sessionmaker, monkeypatch):
    locked, proceed = asyncio.Event(), asyncio.Event()
    original_lock = backup._lock_dataset
    async def paused(session, *, export=False):
        await original_lock(session, export=export)
        if not export:
            locked.set()
            await proceed.wait()
    monkeypatch.setattr(backup, "_lock_dataset", paused)
    async def restore_task():
        async with test_sessionmaker() as session:
            await backup.restore_backup(session, BackupPayload.model_validate(rich))
    async def writer():
        async with test_sessionmaker() as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            writer_pid.set_result(pid)
            session.add(Account(name="Concurrent", currency="USD"))
            await session.commit()
    task = asyncio.create_task(restore_task())
    await asyncio.wait_for(locked.wait(), 5)
    writer_pid = asyncio.get_running_loop().create_future()
    write = asyncio.create_task(writer())
    pid = await writer_pid
    try:
        async with test_sessionmaker() as session:
            for _ in range(100):
                blocked = await session.scalar(text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": pid})
                if blocked:
                    break
                await asyncio.sleep(0.01)
            assert blocked and not write.done()
            locks = set((await session.scalars(text("""SELECT c.relname FROM pg_locks l
                JOIN pg_class c ON c.oid=l.relation WHERE l.mode='AccessExclusiveLock' AND l.granted"""))).all())
            assert set(backup.LOCK_TABLES) <= locks
    finally:
        proceed.set()
        await asyncio.wait_for(asyncio.gather(task, write), 10)
    assert any(a["name"] == "Concurrent" for a in (await export(client))["accounts"])


async def test_known_crypto_cache_quote_and_unconfirmed_currency_roundtrip(client, rich):
    rich["crypto_holdings"][0].update(last_price_currency="VND", last_price_as_of_date=DAY)
    rich["crypto_transactions"][0]["quote_currency"] = "JPY"
    rich["asset_valuations"][0].update(currency="RUB", currency_confirmed=False)
    await restore(client, rich)
    first = await export(client)
    await restore(client, first)
    second = await export(client)
    assert semantic(first) == semantic(second)
    assert second["crypto_holdings"][0]["last_price_currency"] == "VND"
    assert second["crypto_transactions"][0]["quote_currency"] == "JPY"
    assert second["asset_valuations"][0]["currency"] == "RUB"
    assert not second["asset_valuations"][0]["currency_confirmed"]


async def test_restored_known_valuation_uses_observation_currency(client, rich):
    # Asset is VND-native but its newest valuation is USD; neither the native
    # currency nor the old VND observation may reinterpret this USD amount.
    rich["accounts"] = []
    rich["transactions"] = []
    rich["recurring_transactions"] = []
    rich["assets"] = rich["assets"][1:]
    rich["asset_valuations"] = rich["asset_valuations"][1:]
    rich["crypto_holdings"] = []
    rich["crypto_transactions"] = []
    rich["app_settings"]["fx_max_age_days"] = 4000
    await restore(client, rich)
    nw = (await client.get("/net-worth/summary?range=all")).json()
    assert nw["complete"]
    assert Decimal(nw["current"]) == Decimal("1200.123456789123456789")
    historical = next(point for point in nw["series"] if point["date"] == DAY)
    with localcontext() as context:
        context.prec = 96
        # The authoritative resolver has its own approved representable rate
        # precision; use the restored Reports/FX service, not nominal VND.
        assert Decimal(historical["value"]) < 2000
        assert Decimal(historical["value"]) > 900


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("bad", [None, 4, "not-an-array", [None], [{}]])
async def test_malformed_structure_never_reaches_replace(client, rich, test_sessionmaker, version, bad):
    payload = deepcopy(rich)
    payload["aurum_backup_version"] = version
    payload["transactions"] = bad
    before = await snapshot(test_sessionmaker)
    assert (await client.post("/backup/import", json=payload)).status_code == 422
    assert await snapshot(test_sessionmaker) == before


async def test_v1_genuinely_old_settings_missing(client, rich):
    payload = legacy(rich)
    payload.pop("app_settings")
    await client.patch("/settings", json={"currency": "RUB", "idle_cash_threshold_amount": "999"})
    await restore(client, payload)
    result = await export(client)
    assert result["app_settings"]["currency"] == "USD"
    assert Decimal(result["app_settings"]["idle_cash_threshold_amount"]) == 1000
    assert result["app_settings"]["fx_max_age_days"] == 7


async def test_nullable_transaction_destination_roundtrips(client, rich):
    rich["transactions"][2]["transfer_account_id"] = None
    await restore(client, rich)
    first = await export(client)
    await restore(client, first)
    assert semantic(first) == semantic(await export(client))
    assert first["transactions"][2]["transfer_account_id"] is None
    assert Decimal(first["transactions"][2]["transfer_amount"]) == Decimal("520000.123456")


def test_backup_field_coverage_matches_persisted_models():
    # Guard future additions against silently losing authoritative columns.
    # The established backup contract deliberately excludes ORM timestamps.
    for model in backup.MODELS:
        fields = set(BACKUP_ROWS[model.__tablename__].model_fields)
        columns = set(model.__table__.columns.keys()) - {"created_at", "updated_at"}
        assert columns <= fields
    from app.schemas.backup import AppSettingsBackup, CryptoSyncStateBackup
    assert set(backup.AppSettings.__table__.columns.keys()) == set(AppSettingsBackup.model_fields)
    assert set(backup.CryptoSyncState.__table__.columns.keys()) == set(CryptoSyncStateBackup.model_fields)


@pytest.mark.parametrize("currency,amount", [("USD", "10000"), ("VND", "10000000")])
async def test_v2_idle_threshold_denomination_is_settings_pair(client, rich, currency, amount):
    rich["app_settings"].update(currency=currency, idle_cash_threshold_amount=amount)
    await client.patch("/settings", json={"currency": "EUR", "idle_cash_threshold_amount": "12"})
    await restore(client, rich)
    first = await export(client)
    assert first["app_settings"]["currency"] == currency
    assert Decimal(first["app_settings"]["idle_cash_threshold_amount"]) == Decimal(amount)
    assert "idle_cash_threshold_currency" not in first["app_settings"]
    await restore(client, first)
    assert (await export(client))["app_settings"] == first["app_settings"]


async def test_v1_invalid_explicit_settings_currency_is_not_missing(client, rich, test_sessionmaker):
    payload = legacy(rich)
    payload["app_settings"]["currency"] = "INVALID"
    before = await snapshot(test_sessionmaker)
    assert (await client.post("/backup/import", json=payload)).status_code == 422
    assert await snapshot(test_sessionmaker) == before
