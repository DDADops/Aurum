"""Transitional API safety without FX storage or conversion fixtures."""
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.schemas.money import FinancialDataIssue, FinancialDataStatus, MissingRate
from tests.helpers import txn_payload


async def account(client, currency):
    response = await client.post("/accounts", json={"name": currency, "type": "checking", "currency": currency})
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def post(client, account_id, **kwargs):
    response = await client.post("/transactions", json=txn_payload(account_id, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()


async def test_complete_zero_and_explicit_display_currency(client):
    await client.patch("/settings", json={"currency": "VND"})
    for url in ("/dashboard/summary?year=2026&month=1", "/cash-flow", "/net-worth/summary?range=30d",
                "/reports/category-ranking"):
        response = await client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["currency"] == "VND"
        assert body["complete"] is True
        assert body["missing_rates"] == body["data_issues"] == []
        field = next(k for k in ("net", "total_net", "current", "total_amount") if k in body)
        assert Decimal(body[field]) == 0


async def test_dashboard_independent_metrics_and_missing_rate_shape(client, account_id, categories):
    rub = await account(client, "RUB")
    await post(client, account_id, type="income", amount="100", category_id=categories["Salary"]["id"])
    await post(client, rub, amount="250", category_id=categories["Groceries"]["id"])
    body = (await client.get("/dashboard/summary?year=2026&month=1")).json()
    assert body["currency"] == "USD"
    assert body["complete"] is False
    assert Decimal(body["real_income"]) == 100
    assert body["spent"] is None and body["net"] is None
    assert Decimal(body["transferred_out"]) == 0
    assert body["metric_status"]["real_income"]["complete"] is True
    assert body["metric_status"]["spent"]["complete"] is False
    assert body["spending_by_category"] == []
    assert body["missing_rates"] == [{
        "base_currency": "RUB", "quote_currency": "USD", "requested_date": "2026-01-15",
        "reason": "absent", "max_age_days": 7, "latest_available_date": None,
    }]
    # No unrelated month contamination.
    empty = (await client.get("/dashboard/summary?year=2026&month=2")).json()
    assert empty["complete"] is True and Decimal(empty["spent"]) == 0


async def test_foreign_income_does_not_hide_native_expense(client, account_id, categories):
    rub = await account(client, "RUB")
    await post(client, rub, type="income", amount="100000", category_id=categories["Salary"]["id"])
    await post(client, account_id, amount="25", category_id=categories["Groceries"]["id"])
    body = (await client.get("/dashboard/summary?year=2026&month=1")).json()
    assert body["real_income"] is None and body["net"] is None
    assert Decimal(body["spent"]) == 25
    assert body["spending_by_category"]
    assert body["metric_status"]["spending_by_category"]["complete"] is True


async def test_cash_flow_and_reports_are_scoped_and_never_mix(client, account_id, categories):
    rub = await account(client, "RUB")
    groceries = categories["Groceries"]["id"]
    salary = categories["Salary"]["id"]
    await post(client, account_id, type="income", amount="100", category_id=salary)
    await post(client, rub, amount="250", category_id=groceries)
    for url, field in (("/cash-flow", "total_net"),
                       (f"/reports/category-spending?category_id={groceries}", "total_amount"),
                       ("/reports/category-ranking?kind=expense", "total_amount")):
        response = await client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["complete"] is False and body[field] is None
    for url in (f"/reports/category-spending?category_id={salary}", "/reports/category-ranking?kind=income"):
        body = (await client.get(url)).json()
        assert body["complete"] is True
        assert Decimal(body["total_amount"]) == 100
    body = (await client.get("/cash-flow?start_date=2026-02-01&end_date=2026-02-28")).json()
    assert body["complete"] is True and Decimal(body["total_net"]) == 0


@pytest.mark.parametrize("foreign", [False, True])
async def test_net_worth_asset_currency(client, foreign):
    response = await client.post("/assets", json={
        "name": "Asset", "asset_class": "other", "currency": "RUB" if foreign else "USD",
        "value": "100", "as_of_date": "2026-01-01",
    })
    assert response.status_code == 201, response.text
    body = (await client.get("/net-worth/summary?range=30d")).json()
    assert body["complete"] is not foreign
    if foreign:
        assert body["current"] is None and body["change_amount"] is None
        assert body["breakdown"] == body["capital_roles"] == body["risk_levels"] == []
        assert all(p["value"] is None and not p["complete"] for p in body["series"])
    else:
        assert Decimal(body["current"]) == 100


async def test_legacy_transfer_destination_is_unresolved_but_source_metric_is_known(client, account_id, test_sessionmaker):
    rub = await account(client, "RUB")
    created = await post(client, account_id, type="transfer", amount="100", transfer_account_id=rub, transfer_amount="1000")
    # Legacy state can be restored/migrated, but cannot be created by the new API.
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE transactions SET transfer_amount=NULL WHERE id=:id"), {"id": created["id"]})
        await session.commit()
    body = (await client.get("/net-worth/summary?range=30d")).json()
    assert body["complete"] is False and body["current"] is None
    assert {"code": "transfer_amount_unresolved"} in body["data_issues"]
    dashboard = (await client.get("/dashboard/summary?year=2026&month=1")).json()
    assert dashboard["complete"] is True
    assert Decimal(dashboard["transferred_out"]) == 100
    flow = (await client.get("/cash-flow")).json()
    assert flow["complete"] is True and Decimal(flow["total_net"]) == 0


async def test_foreign_account_prevents_fake_net_worth(client):
    rub = await account(client, "RUB")
    await post(client, rub, type="income", amount="100000")
    body = (await client.get("/net-worth/summary?range=30d")).json()
    assert body["current"] is None and body["complete"] is False


async def test_incomplete_inputs_do_not_produce_confident_advice(client, categories):
    rub = await account(client, "RUB")
    await post(client, rub, amount="100000", category_id=categories["Groceries"]["id"], date=date.today().isoformat())
    for url, field in (("/insights/alerts", "alerts"), ("/advice", "items")):
        response = await client.get(url)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["complete"] is False and body[field] == []
        assert body["missing_rates"]


@pytest.mark.parametrize("reason", ["absent", "stale", "precision_unrepresentable"])
async def test_status_serialization(reason):
    status = FinancialDataStatus(complete=False, missing_rates=[MissingRate(
        base_currency="RUB", quote_currency="USD", requested_date=date(2026, 1, 1),
        reason=reason, max_age_days=7,
    )], data_issues=[FinancialDataIssue(code="transfer_amount_unresolved")])
    body = status.model_dump(mode="json")
    assert body["missing_rates"][0]["reason"] == reason
    assert body["missing_rates"][0]["requested_date"] == "2026-01-01"
    assert body["data_issues"] == [{"code": "transfer_amount_unresolved"}]


async def test_foreign_split_is_guarded_in_category_reports(client, categories):
    rub = await account(client, "RUB")
    parent = categories["Groceries"]["id"]
    child_response = await client.post("/categories", json={
        "name": "Fruit", "kind": "expense", "color": "#112233", "parent_id": parent,
    })
    assert child_response.status_code == 201, child_response.text
    child = child_response.json()["id"]
    await post(client, rub, amount="30", splits=[
        {"category_id": parent, "amount": "10"}, {"category_id": child, "amount": "20"},
    ])
    for url in (f"/reports/category-spending?category_id={child}", "/reports/category-ranking?kind=expense"):
        body = (await client.get(url)).json()
        assert body["complete"] is False and body["total_amount"] is None


async def test_uncategorized_foreign_expense_does_not_poison_category_ranking(client, account_id, categories):
    rub = await account(client, "RUB")
    await post(client, rub, amount="100")
    await post(client, account_id, amount="10", category_id=categories["Groceries"]["id"])
    body = (await client.get("/reports/category-ranking?kind=expense")).json()
    assert body["complete"] is True and Decimal(body["total_amount"]) == 10
