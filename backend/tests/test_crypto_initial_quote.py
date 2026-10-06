"""Initial execution quote is client-owned; omission is legacy compatibility."""
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.services import crypto_service as crypto
from tests.test_crypto import _fake_fetch, _point


BODY = {"coingecko_id": "bitcoin", "symbol": "btc", "name": "Bitcoin",
        "quantity": "0.500000000000000001", "price_per_unit": "100000.123456789123456789",
        "date": "2026-01-01"}


@pytest.mark.parametrize("quote", ["rub", "VND"])
async def test_explicit_initial_quote(client, test_sessionmaker, monkeypatch, quote):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Explicit execution quote must not read default currency")

    monkeypatch.setattr(crypto, "effective_default_currency", forbidden)
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({}))
    assert (await client.patch("/settings", json={"currency": "USD"})).status_code == 200
    result = await client.post("/crypto/holdings", json={**BODY, "quote_currency": quote})
    assert result.status_code == 201, result.text
    path = f"/crypto/holdings/{result.json()['asset_id']}/transactions"
    tx = (await client.get(path)).json()[0]
    assert tx["quote_currency"] == quote.upper()
    assert Decimal(tx["quantity"]) == Decimal(BODY["quantity"])
    assert Decimal(tx["price_per_unit"]) == Decimal(BODY["price_per_unit"])
    async with test_sessionmaker() as session:
        assert await session.scalar(text("SELECT count(*) FROM fx_rates")) == 0


async def test_settings_change_during_create_and_legacy_unknown(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({}))
    old = await client.post("/crypto/holdings", json=BODY)
    assert old.status_code == 201
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE crypto_transactions SET quote_currency=NULL"))
        await session.commit()

    async def seed(ids, currency):
        # Independent committed Settings change while creation is in flight.
        assert (await client.patch("/settings", json={"currency": "VND"})).status_code == 200
        assert currency == "rub"
        return {}

    monkeypatch.setattr(crypto, "_fetch_market_data", seed)
    result = await client.post("/crypto/holdings", json={**BODY, "quote_currency": "RUB"})
    assert result.status_code == 201, result.text
    rows = (await client.get(f"/crypto/holdings/{result.json()['asset_id']}/transactions")).json()
    assert rows[0]["quote_currency"] == "RUB"
    assert Decimal(rows[0]["price_per_unit"]) == Decimal(BODY["price_per_unit"])
    old_rows = (await client.get(f"/crypto/holdings/{old.json()['asset_id']}/transactions")).json()
    assert old_rows[0]["quote_currency"] is None


@pytest.mark.parametrize("extra", [{}, {"quote_currency": None}])
async def test_legacy_default_snapshot(client, monkeypatch, extra):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({}))
    await client.patch("/settings", json={"currency": "VND"})
    result = await client.post("/crypto/holdings", json={**BODY, **extra})
    assert result.status_code == 201
    tx = (await client.get(f"/crypto/holdings/{result.json()['asset_id']}/transactions")).json()[0]
    assert tx["quote_currency"] == "VND"


@pytest.mark.parametrize("quote", ["BAD", "", " rub ", 123])
async def test_invalid_initial_quote_matches_transaction_validation(client, quote):
    body = {**BODY, "quote_currency": quote}
    initial = await client.post("/crypto/holdings", json=body)
    transaction = await client.post("/crypto/holdings/999/transactions", json={**body, "type": "buy"})
    assert initial.status_code == transaction.status_code == 422
    assert initial.json()["detail"][0]["loc"] == transaction.json()["detail"][0]["loc"] == ["body", "quote_currency"]


async def test_initial_purchase_rolls_back_on_failure(client, test_sessionmaker, monkeypatch):
    monkeypatch.setattr(crypto, "_fetch_market_data", _fake_fetch({"bitcoin": _point("10")}))

    async def fail_after_insert(session, holding, *args):
        assert await session.scalar(text("SELECT count(*) FROM crypto_transactions")) == 1
        raise HTTPException(409, "valuation_provenance_conflict")

    monkeypatch.setattr(crypto, "_store_observation", fail_after_insert)
    result = await client.post("/crypto/holdings", json={**BODY, "quote_currency": "RUB"})
    assert result.status_code == 409
    async with test_sessionmaker() as session:
        for table in ("assets", "crypto_holdings", "crypto_transactions", "asset_valuations"):
            assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
