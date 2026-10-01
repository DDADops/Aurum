"""Native balance semantics and the remaining legacy balance consumers."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import event, text

from app.schemas.fx import FxObservation
from app.services import fx_service
from app.services.financial_data_service import capital_status
from app.services.fx_provider import FxProvider
from app.services.insights_service import _idle_cash_account_count
from app.services.native_balance_service import account_balances
from app.services.net_worth_service import CASH_ACCOUNT_TYPES, _cash_cumulative_events
from tests.helpers import txn_payload


async def account(client, currency="USD", type="checking"):
    response = await client.post("/accounts", json={"name": currency, "currency": currency, "type": type})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["currency"] == currency and Decimal(body["balance"]) == 0
    assert body["complete"] and not body["has_monetary_history"] and body["data_issues"] == []
    return body["id"]


async def post(client, source, **kwargs):
    response = await client.post("/transactions", json=txn_payload(source, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def accounts(client, **params):
    response = await client.get("/accounts", params=params)
    assert response.status_code == 200, response.text
    return {row["id"]: row for row in response.json()}


def check(row, balance, history=True):
    assert row["has_monetary_history"] is history
    assert row["complete"] is (balance is not None)
    assert row["data_issues"] == ([] if balance is not None else [{"code": "transfer_amount_unresolved"}])
    assert row["missing_rates"] == []
    if balance is None:
        assert row["balance"] is None
    else:
        assert Decimal(row["balance"]) == Decimal(balance)


async def unresolved(sessionmaker, transaction_id, missing_destination=False):
    async with sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": transaction_id})
        if missing_destination:
            await session.execute(text("UPDATE transactions SET transfer_account_id=NULL WHERE id=:id"), {"id": transaction_id})
        await session.commit()


@pytest.mark.parametrize("currency", ["USD", "VND"])
async def test_empty_account_list_patch_contract(client, currency):
    id_ = await account(client, currency)
    check((await accounts(client))[id_], "0", False)
    updated = await client.patch(f"/accounts/{id_}", json={"name": "Renamed"})
    assert updated.status_code == 200
    check(updated.json(), "0", False)
    assert updated.json()["currency"] == currency


@pytest.mark.parametrize("currency,kind,amount,expected", [("USD", "income", "100", "100"), ("RUB", "expense", "250", "-250")])
async def test_native_income_expense(client, currency, kind, amount, expected):
    id_ = await account(client, currency)
    await post(client, id_, type=kind, amount=amount)
    check((await accounts(client))[id_], expected)


@pytest.mark.parametrize("source_currency,target_currency,amount,credit", [("USD", "USD", "10", "10"), ("RUB", "VND", "10000", "3000000")])
async def test_native_transfer_sides(client, source_currency, target_currency, amount, credit):
    source, destination = await account(client, source_currency), await account(client, target_currency)
    await post(client, source, type="transfer", amount=amount, transfer_account_id=destination, transfer_amount=credit)
    rows = await accounts(client)
    check(rows[source], str(-Decimal(amount)))
    check(rows[destination], credit)
    assert rows[source]["currency"] == source_currency and rows[destination]["currency"] == target_currency
    patch = await client.patch(f"/accounts/{destination}", json={"name": "Updated"})
    check(patch.json(), credit)


async def test_multiple_destination_amounts_and_exact_zero(client):
    rub, usd, vnd = await account(client, "RUB"), await account(client), await account(client, "VND")
    await post(client, rub, type="transfer", amount="10000", transfer_account_id=vnd, transfer_amount="3000000.123456")
    await post(client, usd, type="transfer", amount="2", transfer_account_id=vnd, transfer_amount="50000.000001")
    check((await accounts(client))[vnd], "3050000.123457")
    await post(client, vnd, amount="3050000.123457")
    check((await accounts(client))[vnd], "0")


@pytest.mark.parametrize("same_currency", [False, True])
async def test_unknown_incoming_is_not_partial_or_zero(client, test_sessionmaker, same_currency):
    source = await account(client, "VND" if same_currency else "RUB")
    destination, unrelated = await account(client, "VND"), await account(client)
    await post(client, destination, type="income", amount="50")
    for _ in range(2):
        tx = await post(client, source, type="transfer", amount="10", transfer_account_id=destination,
                        transfer_amount="10" if same_currency else "3000")
        await unresolved(test_sessionmaker, tx)
    rows = await accounts(client)
    check(rows[source], "-20")
    check(rows[destination], None)
    check(rows[unrelated], "0", False)
    patch = await client.patch(f"/accounts/{destination}", json={"name": "Still unknown"})
    check(patch.json(), None)
    async with test_sessionmaker() as session:
        status = await capital_status(session, "VND", account_types=CASH_ACCOUNT_TYPES)
        assert not status.complete
        assert [issue.code for issue in status.data_issues] == ["transfer_amount_unresolved"]
    summary = (await client.get("/net-worth/summary?range=30d")).json()
    assert summary["current"] is None and not summary["complete"]
    assert all(p["value"] is None and not p["complete"] for p in summary["series"])


async def test_missing_destination_keeps_source_complete(client, test_sessionmaker):
    source, destination = await account(client), await account(client)
    tx = await post(client, source, type="transfer", amount="10", transfer_account_id=destination)
    await unresolved(test_sessionmaker, tx, missing_destination=True)
    rows = await accounts(client)
    check(rows[source], "-10")
    check(rows[destination], "0", False)
    async with test_sessionmaker() as session:
        assert (await capital_status(session, "USD")).complete
    summary = (await client.get("/net-worth/summary?range=all")).json()
    assert summary["complete"] and Decimal(summary["current"]) == -10
    # This remains a transaction diagnostic, not an unknown existing balance.
    transactions = (await client.get("/transactions")).json()
    assert {"code": "transfer_destination_missing"} in transactions["items"][0]["data_issues"]


async def test_deleting_last_unresolved_transfer_restores_empty_accounts(client, test_sessionmaker):
    source, destination = await account(client, "RUB"), await account(client, "VND")
    tx = await post(client, source, type="transfer", amount="10", transfer_account_id=destination, transfer_amount="3000")
    await unresolved(test_sessionmaker, tx)
    check((await accounts(client))[destination], None)
    assert (await client.delete(f"/transactions/{tx}")).status_code == 204
    rows = await accounts(client)
    for id_ in (source, destination):
        check(rows[id_], "0", False)
    assert (await client.patch(f"/accounts/{destination}", json={"currency": "EUR"})).status_code == 200


async def test_archived_balances_preserve_inclusion_rules(client, test_sessionmaker):
    source, destination = await account(client, "RUB"), await account(client, "VND")
    tx = await post(client, source, type="transfer", amount="10", transfer_account_id=destination, transfer_amount="3000")
    patch = await client.patch(f"/accounts/{destination}", json={"is_archived": True})
    check(patch.json(), "3000")
    assert destination not in await accounts(client)
    check((await accounts(client, include_archived=True))[destination], "3000")
    await unresolved(test_sessionmaker, tx)
    check((await accounts(client, include_archived=True))[destination], None)


async def test_cash_events_credit_native_amount_and_guard_mixed_totals(client, test_sessionmaker):
    source, destination = await account(client, "RUB", "credit_card"), await account(client, "VND")
    tx = await post(client, source, type="transfer", amount="10000", transfer_account_id=destination, transfer_amount="3000000")
    async with test_sessionmaker() as session:
        assert (await _cash_cumulative_events(session))[destination][-1][1] == Decimal("3000000")
        status = await capital_status(session, "VND", account_types=CASH_ACCOUNT_TYPES)
        assert status.complete and not status.data_issues
    await client.patch("/settings", json={"currency": "VND"})
    summary = (await client.get("/net-worth/summary?range=all")).json()
    assert summary["complete"] and Decimal(summary["current"]) == 3000000
    # Including both native sides does not license a mixed-currency sum.
    await client.patch(f"/accounts/{source}", json={"type": "checking"})
    summary = (await client.get("/net-worth/summary?range=all")).json()
    assert not summary["complete"] and summary["current"] is None
    assert summary["missing_rates"] and summary["data_issues"] == []
    await unresolved(test_sessionmaker, tx)
    await post(client, destination, type="income", amount="100", date="2026-02-01")
    async with test_sessionmaker() as session:
        assert [value for _, value in (await _cash_cumulative_events(session))[destination]] == [None, None]


async def test_unknown_non_cash_destination_does_not_invalidate_cash_source(client, test_sessionmaker):
    source, destination = await account(client), await account(client, "USD", "credit_card")
    tx = await post(client, source, type="transfer", amount="10", transfer_account_id=destination)
    await unresolved(test_sessionmaker, tx)
    async with test_sessionmaker() as session:
        assert (await capital_status(session, "USD", account_types=CASH_ACCOUNT_TYPES)).complete
        assert not (await capital_status(session, "USD")).complete
        assert (await _cash_cumulative_events(session))[source][-1][1] == -10


async def test_idle_cash_native_credit_threshold_and_unknown(client, test_sessionmaker):
    source, destination = await account(client, "RUB", "credit_card"), await account(client, "USD")
    tx = await post(client, source, type="transfer", amount="100000", transfer_account_id=destination,
                    transfer_amount="500", date=(date.today() - timedelta(days=100)).isoformat())
    async with test_sessionmaker() as session:
        assert await _idle_cash_account_count(session, Decimal(1000), 60, currency="USD") == 0
        assert await _idle_cash_account_count(session, Decimal(400), 60, currency="USD") == 1
    await unresolved(test_sessionmaker, tx)
    async with test_sessionmaker() as session:
        assert await _idle_cash_account_count(session, Decimal(400), 60, currency="USD") is None


async def test_idle_cash_foreign_balance_cannot_trigger_usd_alert(client, test_sessionmaker):
    destination = await account(client, "VND")
    await post(client, destination, type="income", amount="3000000", date=(date.today() - timedelta(days=100)).isoformat())
    async with test_sessionmaker() as session:
        assert await _idle_cash_account_count(session, Decimal(1000), 60, currency="USD") is None
    body = (await client.get("/insights/alerts")).json()
    assert not body["complete"] and body["alerts"] == []
    # Archived accounts remain excluded from the per-account idle-cash check.
    await client.patch(f"/accounts/{destination}", json={"is_archived": True})
    async with test_sessionmaker() as session:
        assert await _idle_cash_account_count(session, Decimal(1000), 60, currency="USD") == 0


async def test_balances_are_independent_of_decimal_context(client, test_sessionmaker):
    source, destination = await account(client, "RUB"), await account(client, "VND")
    await post(client, source, type="transfer", amount="123456789.123456", transfer_account_id=destination,
               transfer_amount="987654321.654321")
    async with test_sessionmaker() as session:
        with localcontext() as context:
            context.prec = 6
            balances = await account_balances(session)
        assert balances[source].balance == Decimal("-123456789.123456")
        assert balances[destination].balance == Decimal("987654321.654321")


async def test_fx_never_changes_or_participates_in_account_balances(client, test_sessionmaker, monkeypatch):
    source, destination = await account(client, "RUB"), await account(client, "VND")
    await post(client, source, type="transfer", amount="10000", transfer_account_id=destination, transfer_amount="3000000")
    before = await accounts(client)
    async with test_sessionmaker() as session:
        await fx_service.upsert_observation(session, FxObservation(
            base_currency="RUB", quote_currency="VND", rate=Decimal("999"),
            as_of_date=date.today(), source="irrelevant", fetched_at=datetime.now(timezone.utc),
        ))
        await session.commit()
    resolve = AsyncMock(side_effect=AssertionError("No FX resolution for native balances"))
    convert = Mock(side_effect=AssertionError("No FX conversion for native balances"))
    fetch = AsyncMock(side_effect=AssertionError("No FX provider for native balances"))
    monkeypatch.setattr(fx_service, "resolve_rate", resolve)
    monkeypatch.setattr(fx_service, "convert", convert)
    monkeypatch.setattr(FxProvider, "fetch_observations", fetch)
    engine = test_sessionmaker.kw["bind"].sync_engine

    def reject_fx_query(conn, cursor, statement, parameters, context, executemany):
        assert "fx_rates" not in statement.lower()

    event.listen(engine, "before_cursor_execute", reject_fx_query)
    try:
        assert await accounts(client) == before
        response = await client.patch(f"/accounts/{destination}", json={"name": "Still native"})
        check(response.json(), "3000000")
        await account(client, "VND")
    finally:
        event.remove(engine, "before_cursor_execute", reject_fx_query)
    resolve.assert_not_called()
    convert.assert_not_called()
    fetch.assert_not_called()
