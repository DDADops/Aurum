"""Manual valuation intent is checked under the native-currency parent lock."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select, text

from app.api.routes import assets as routes
from app.models.asset import Asset
from app.services import fx_service
from tests.test_native_transfers import wait_blocked


async def empty_asset(sessions, currency="RUB"):
    async with sessions() as session:
        asset = Asset(name="Empty", asset_class="other", currency=currency)
        session.add(asset)
        await session.commit()
        return asset.id


@pytest.mark.parametrize("currency", ["RUB", "VND"])
async def test_matching_intent_preserves_decimal(client, test_sessionmaker, monkeypatch, currency):
    lookup = AsyncMock(side_effect=AssertionError("No FX lookup"))
    convert = Mock(side_effect=AssertionError("No conversion"))
    monkeypatch.setattr(fx_service, "resolve_rate", lookup)
    monkeypatch.setattr(fx_service, "convert", convert)
    id_ = await empty_asset(test_sessionmaker, currency)
    value = "12345678901234567890.123456789123456789"
    result = await client.post(f"/assets/{id_}/valuations", json={"value": value, "expected_currency": currency.lower()})
    assert result.status_code == 200, result.text
    assert Decimal(result.json()["current_value"]) == Decimal(value)
    assert result.json()["valuation_currency"] == currency
    assert result.json()["valuation_currency_confirmed"] is True
    lookup.assert_not_called()
    convert.assert_not_called()


async def test_mismatch_and_missing_intent_never_insert(client, test_sessionmaker, monkeypatch):
    lookup = AsyncMock(side_effect=AssertionError("No FX lookup"))
    monkeypatch.setattr(fx_service, "resolve_rate", lookup)
    id_ = await empty_asset(test_sessionmaker, "VND")
    path = f"/assets/{id_}/valuations"
    for body, status in [({"value": "100000", "expected_currency": "RUB"}, 409), ({"value": "100000"}, 422)]:
        result = await client.post(path, json=body)
        assert result.status_code == status
        if status == 409:
            assert result.json()["detail"] == "asset_currency_mismatch"
        assert (await client.get(path)).json() == []
    lookup.assert_not_called()


async def test_currency_change_wins_locked_reread(client, test_sessionmaker):
    id_ = await empty_asset(test_sessionmaker)
    async with test_sessionmaker() as changer:
        await changer.execute(select(Asset).where(Asset.id == id_).with_for_update())
        pid = await changer.scalar(text("SELECT pg_backend_pid()"))
        task = asyncio.create_task(client.post(f"/assets/{id_}/valuations", json={"value": "100000", "expected_currency": "RUB"}))
        try:
            await wait_blocked(test_sessionmaker, pid)
            await changer.execute(text("UPDATE assets SET currency='VND' WHERE id=:id"), {"id": id_})
            await changer.commit()
            result = await asyncio.wait_for(task, 5)
            assert result.status_code == 409
            assert result.json()["detail"] == "asset_currency_mismatch"
            assert (await client.get(f"/assets/{id_}/valuations")).json() == []
        finally:
            await changer.rollback()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_valuation_wins_currency_patch_rejected(client, test_sessionmaker, monkeypatch):
    id_ = await empty_asset(test_sessionmaker)
    locked, release = asyncio.Event(), asyncio.Event()
    original = routes.lock_asset
    pid = None

    async def pause(session, asset_id):
        nonlocal pid
        asset = await original(session, asset_id)
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        locked.set()
        await release.wait()
        return asset

    monkeypatch.setattr(routes, "lock_asset", pause)
    valuation = asyncio.create_task(client.post(f"/assets/{id_}/valuations", json={"value": "100000", "expected_currency": "RUB"}))
    changer = None
    try:
        await asyncio.wait_for(locked.wait(), 5)
        changer = asyncio.create_task(client.patch(f"/assets/{id_}", json={"currency": "VND"}))
        await wait_blocked(test_sessionmaker, pid)
        release.set()
        result = await asyncio.wait_for(valuation, 5)
        assert result.status_code == 200
        assert result.json()["valuation_currency"] == "RUB"
        assert result.json()["valuation_currency_confirmed"] is True
        result = await asyncio.wait_for(changer, 5)
        assert result.status_code == 409
        assert result.json()["detail"] == "currency_change_requires_empty_history"
    finally:
        release.set()
        await asyncio.gather(*(t for t in (valuation, changer) if t is not None), return_exceptions=True)
