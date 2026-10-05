"""Dependency-scoped decisions over authoritative converted inputs."""
from datetime import date
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, text

from app.schemas.cash_flow import CashFlowPoint
from app.schemas.money import FinancialDataIssue, FinancialDataStatus, MissingRate
from app.schemas.net_worth import NetWorthPoint, NetWorthSummary, RiskLevelSummary
from app.services import insights_service as insights, advice_service as advice, crypto_service as crypto
from app.services.fx_provider import FxProvider
from tests.test_dashboard_fx import account, post, rate
from tests.test_crypto_fx import holding


class Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 6, 15)


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(insights, "date", Today)
    monkeypatch.setattr(advice, "date", Today)


def missing(reason="absent"):
    return FinancialDataStatus(complete=False, missing_rates=[MissingRate(
        base_currency="RUB", quote_currency="USD", requested_date=date(2026, 5, 1),
        reason=reason, max_age_days=7,
    )])


def nw(points=(), status=None, risky=True):
    status = status or FinancialDataStatus()
    return NetWorthSummary(
        currency="USD", range="all", current=Decimal(100), change_amount=None, change_percent=None,
        breakdown=[], capital_roles=[], metric_status={"risk_levels": status},
        risk_levels=[RiskLevelSummary(risk_level="high", label="High", color="#112233",
                                     total_value=Decimal(100), percent=100, items=[])] if risky else [],
        series=[NetWorthPoint(date=date(2026, month, 28), value=value,
                              **(missing().model_dump() if value is None else {}))
                for month, value in points],
    )


def flow(values):
    return SimpleNamespace(points=[CashFlowPoint(
        year=2026, month=month, income=Decimal(0), expense=None, net=value,
        metric_status={"net": missing() if value is None else FinancialDataStatus()},
        # Unrelated expense/root status must not override the net metric.
        complete=False,
    ) for month, value in values])


@pytest.mark.parametrize("values,expected,complete", [
    ([(1, None), (2, 100), (3, 110), (4, 90), (5, 80)], 2, True),
    ([(3, 110), (4, None), (5, 80)], None, False),
    ([(3, 110), (4, 90), (5, 80), (6, None)], 2, True),
    ([], 0, True),
])
def test_decline_uses_only_required_month_end_points(clock, values, expected, complete):
    statuses = []
    assert insights._net_worth_decline_streak(nw(values), statuses) == expected
    assert all(s.complete for s in statuses) is complete
    if not complete:
        assert any(s.missing_rates for s in statuses)


@pytest.mark.parametrize("values,expected,complete", [
    ([(1, None), (2, 10), (3, 0), (4, -10), (5, -20)], 2, True),
    ([(3, 0), (4, None), (5, -20)], None, False),
    ([(5, 0)], 0, True),
    ([], None, False),
])
async def test_flow_uses_only_required_net_periods(clock, monkeypatch, values, expected, complete):
    mock = AsyncMock(return_value=flow(values))
    monkeypatch.setattr(insights, "get_cash_flow", mock)
    statuses = []
    assert await insights._negative_cash_flow_streak(None, statuses) == expected
    assert all(s.complete for s in statuses) is complete
    assert mock.call_args.args[1:] == (date(2024, 6, 1), date(2026, 5, 31))


async def test_alerts_keep_independent_complete_rules(clock, client, monkeypatch):
    monkeypatch.setattr(insights, "get_cash_flow", AsyncMock(return_value=flow([(3, 0), (4, -10), (5, -20)])))
    monkeypatch.setattr(insights, "get_net_worth_summary", AsyncMock(return_value=nw([(3, 100), (4, 90), (5, 80)])))
    await client.patch("/settings", json={"negative_cash_flow_threshold_months": 2,
                                         "net_worth_decline_threshold_months": 2})
    body = (await client.get("/insights/alerts")).json()
    assert body["complete"]
    assert {a["key"] for a in body["alerts"]} == {
        "negative_cash_flow_streak", "net_worth_decline_streak", "risky_allocation_exceeded"}
    assert len(body["metric_status"]) == 5


@pytest.mark.parametrize("reason", ["absent", "stale", "precision_unrepresentable", "provenance"])
async def test_unknown_risk_preserves_complete_flow_and_reasons(clock, client, monkeypatch, reason):
    status = (FinancialDataStatus(complete=False, data_issues=[FinancialDataIssue(code="crypto_quote_currency_unknown")])
              if reason == "provenance" else missing(reason))
    monkeypatch.setattr(insights, "get_net_worth_summary", AsyncMock(return_value=nw([(4, 90), (5, None)], status, False)))
    monkeypatch.setattr(insights, "get_cash_flow", AsyncMock(return_value=flow([(3, 0), (4, -10), (5, -20)])))
    await client.patch("/settings", json={"negative_cash_flow_threshold_months": 2})
    body = (await client.get("/insights/alerts")).json()
    assert not body["complete"]
    assert [a["key"] for a in body["alerts"]] == ["negative_cash_flow_streak"]
    assert not body["metric_status"]["risky_allocation_exceeded"]["complete"]
    if reason == "provenance":
        assert {"code": "crypto_quote_currency_unknown"} in body["data_issues"]
    else:
        assert status.missing_rates[0].model_dump(mode="json") in body["missing_rates"]


async def test_budgets_independently_evaluated_without_currency_sum(client, account_id, categories):
    groceries, dining = categories["Groceries"]["id"], categories["Dining Out"]["id"]
    for cat, currency, limit in [(groceries, "USD", "50"), (dining, "VND", "1000000")]:
        result = await client.post("/budgets", json={"category_id": cat, "currency": currency, "monthly_limit": limit})
        assert result.status_code == 201, result.text
        await post(client, account_id, "100", date.today().isoformat(), category_id=cat)
    body = (await client.get("/insights/alerts")).json()
    assert {"key": "budget_exceeded", "severity": "warning", "params": {"count": 1}} in body["alerts"]
    assert not body["metric_status"]["budget_exceeded"]["complete"]
    assert body["missing_rates"]


async def test_foreign_idle_cash_does_not_suppress_budget(client, account_id, categories, test_sessionmaker):
    cat = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await post(client, rub, "100000", "2025-01-01", type="income")
    await post(client, account_id, "100", date.today().isoformat(), category_id=cat)
    await client.post("/budgets", json={"category_id": cat, "currency": "USD", "monthly_limit": "50"})
    await rate(test_sessionmaker, "USD", "RUB", "100", date.today().isoformat())
    body = (await client.get("/insights/alerts")).json()
    assert not body["metric_status"]["idle_cash"]["complete"]
    assert "idle_cash" not in {a["key"] for a in body["alerts"]}
    assert "budget_exceeded" in {a["key"] for a in body["alerts"]}


async def test_advice_dependency_windows_and_unknown_not_zero(clock, client, account_id, categories):
    cat = categories["Groceries"]["id"]
    rub = await account(client, "RUB")
    await post(client, rub, "100000", "2026-03-01", category_id=cat)
    await post(client, account_id, "120", "2026-06-01", category_id=cat)
    body = (await client.get("/advice")).json()
    assert not body["complete"]
    assert not body["metric_status"]["rising_category"]["complete"]
    assert body["metric_status"]["unbudgeted_top_category"]["complete"]
    assert body["metric_status"]["savings_rate_trend"]["complete"]
    assert body["items"] == [{"key": "unbudgeted_top_category", "tone": "neutral",
                              "params": {"category": "Groceries", "amount": "120.000000", "currency": "USD"}}]


async def test_irrelevant_old_category_and_income_do_not_block_category_advice(clock, client, account_id, categories):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", "2026-03-01", category_id=categories["Dining Out"]["id"])
    await post(client, rub, "100000", "2026-06-01", type="income")
    cat = categories["Groceries"]["id"]
    await post(client, account_id, "30", "2026-03-01", category_id=cat)
    await post(client, account_id, "120", "2026-06-01", category_id=cat)
    body = (await client.get("/advice")).json()
    assert not body["complete"]
    assert body["metric_status"]["rising_category"]["complete"]
    assert not body["metric_status"]["savings_rate_trend"]["complete"]
    rising = next(a for a in body["items"] if a["key"] == "rising_category")
    assert Decimal(rising["params"]["average"]) == 10
    assert rising["params"]["percent"] == 1100


async def test_unknown_current_category_prevents_confident_rank(clock, client, account_id, categories):
    rub = await account(client, "RUB")
    await post(client, rub, "100000", "2026-06-01", category_id=categories["Groceries"]["id"])
    await post(client, account_id, "30", "2026-06-01", category_id=categories["Dining Out"]["id"])
    body = (await client.get("/advice")).json()
    assert body["items"] == []
    assert not body["metric_status"]["unbudgeted_top_category"]["complete"]
    assert body["missing_rates"]


async def test_complete_savings_and_known_empty_results(clock, client, account_id):
    empty = (await client.get("/advice")).json()
    assert empty["complete"] and empty["items"] == []
    for month, expense in [(5, "90"), (6, "50")]:
        await post(client, account_id, "100", f"2026-{month:02d}-01", type="income")
        await post(client, account_id, expense, f"2026-{month:02d}-01")
    body = (await client.get("/advice")).json()
    assert {"key": "savings_rate_trend", "tone": "positive", "params": {"rate": 50, "diff": 40}} in body["items"]


async def test_converted_advice_offline_readonly_precision_and_settings(clock, client, account_id, categories,
                                                                      test_sessionmaker, monkeypatch):
    cat = categories["Groceries"]["id"]
    await post(client, account_id, "123.123456", "2026-06-01", category_id=cat)
    await post(client, account_id, "30.123456", "2026-03-01", category_id=cat)
    for day in ["2026-03-01", "2026-06-01"]:
        await rate(test_sessionmaker, "USD", "VND", "26000", day)
    async def snapshot():
        async with test_sessionmaker() as session:
            return [(await session.execute(text(f"SELECT * FROM {table} ORDER BY 1"))).all()
                    for table in ("accounts", "transactions", "assets", "asset_valuations", "crypto_holdings",
                                  "crypto_transactions", "fx_rates")]
    await holding(test_sessionmaker, price="1500000000", price_currency="VND",
                  price_date=date(2026, 6, 1), observations=[(date(2026, 6, 1), "60000", "USD", True)])
    before = await snapshot()
    forbidden = AsyncMock(side_effect=AssertionError("GET must be offline"))
    monkeypatch.setattr(crypto, "_fetch_market_data", forbidden)
    monkeypatch.setattr(FxProvider, "fetch_observations", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    engine = test_sessionmaker.kw["bind"].sync_engine
    statements = []
    def inspect(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().lower())
    event.listen(engine, "before_cursor_execute", inspect)
    try:
        usd = (await client.get("/advice")).json()
        assert usd["currency"] == "USD"
        await client.patch("/settings", json={"currency": "VND"})
        statements.clear()
        with localcontext() as context:
            context.prec = 6
            low = (await client.get("/advice")).json()
            alerts_low = (await client.get("/insights/alerts")).json()
        high = (await client.get("/advice")).json()
        assert low == high
        assert alerts_low == (await client.get("/insights/alerts")).json()
        params = next(a["params"] for a in high["items"] if a["key"] == "rising_category")
        assert params["currency"] == "VND"
        with localcontext() as context:
            context.prec = 96
            assert Decimal(params["current"]) == Decimal("123.123456") * 26000
            assert Decimal(params["average"]) == Decimal("30.123456") * 26000 / 3
        assert not any(s.startswith(("insert", "update", "delete")) for s in statements)
        assert await snapshot() == before
    finally:
        event.remove(engine, "before_cursor_execute", inspect)
    forbidden.assert_not_called()



async def test_budgeted_unknown_does_not_block_unbudgeted_rank(clock, client, account_id, categories):
    rub = await account(client, "RUB")
    cat = categories["Groceries"]["id"]
    await post(client, rub, "100000", "2026-06-01", category_id=cat)
    await client.post("/budgets", json={"category_id": cat, "currency": "RUB", "monthly_limit": "50000"})
    await post(client, account_id, "30", "2026-06-01", category_id=categories["Dining Out"]["id"])
    body = (await client.get("/advice")).json()
    assert body["metric_status"]["unbudgeted_top_category"]["complete"]
    assert [a["key"] for a in body["items"]] == ["unbudgeted_top_category"]
    assert body["items"][0]["params"]["category"] == "Dining Out"


async def test_goal_currencies_do_not_enter_rules(clock, client):
    urls = ("/insights/alerts", "/advice")
    before = [(await client.get(url)).json() for url in urls]
    for currency in ("USD", "VND"):
        goal = await client.post("/goals", json={"name": currency, "currency": currency, "target_amount": "1000"})
        assert goal.status_code == 201, goal.text
        result = await client.post(f"/goals/{goal.json()['id']}/contributions", json={"amount": "500", "date": "2026-06-01"})
        assert result.status_code == 201, result.text
    assert [(await client.get(url)).json() for url in urls] == before


async def test_crypto_provider_native_price_is_not_risk_amount(client, account_id, test_sessionmaker):
    today = date.today().isoformat()
    await post(client, account_id, "1000", today, type="income")
    asset_id = await holding(test_sessionmaker, price="1000000", price_currency="VND", price_date=date.today(),
                            observations=[(date.today(), "1000000", "VND", True)])
    async with test_sessionmaker() as session:
        await session.execute(text("UPDATE assets SET risk_level='HIGH' WHERE id=:id"), {"id": asset_id})
        await session.commit()
    await rate(test_sessionmaker, "VND", "USD", "0.00004", today)
    body = (await client.get("/insights/alerts")).json()
    assert body["metric_status"]["risky_allocation_exceeded"]["complete"]
    assert "risky_allocation_exceeded" not in {a["key"] for a in body["alerts"]}  # 40 / 1040, not 1,000,000 / 1001000


async def test_unconfirmed_crypto_valuation_only_blocks_dependent_alerts(clock, client, account_id, categories,
                                                                       test_sessionmaker):
    await holding(test_sessionmaker, observations=[(date(2026, 6, 1), "60000", "USD", False)])
    await post(client, account_id, "100", "2026-06-01", category_id=categories["Groceries"]["id"])
    alerts = (await client.get("/insights/alerts")).json()
    assert not alerts["metric_status"]["risky_allocation_exceeded"]["complete"]
    assert {"code": "valuation_currency_unconfirmed"} in alerts["data_issues"]
    body = (await client.get("/advice")).json()
    assert body["complete"]  # Advice has no capital/crypto dependency.
    assert [a["key"] for a in body["items"]] == ["unbudgeted_top_category"]


async def test_unknown_cash_flow_exposes_status_but_keeps_risk_alert(clock, client, monkeypatch):
    monkeypatch.setattr(insights, "get_cash_flow", AsyncMock(return_value=flow([(5, None)])))
    monkeypatch.setattr(insights, "get_net_worth_summary", AsyncMock(return_value=nw()))
    body = (await client.get("/insights/alerts")).json()
    assert not body["complete"]
    assert not body["metric_status"]["negative_cash_flow_streak"]["complete"]
    assert body["metric_status"]["negative_cash_flow_streak"]["missing_rates"]
    assert [a["key"] for a in body["alerts"]] == ["risky_allocation_exceeded"]


async def test_same_currency_unresolved_idle_credit_preserves_reason(clock, client, account_id, monkeypatch):
    from app.services.native_balance_service import NativeBalance
    monkeypatch.setattr(insights, "account_balances", AsyncMock(return_value={
        account_id: NativeBalance(None, date(2026, 1, 1)),
    }))
    body = (await client.get("/insights/alerts")).json()
    assert body["metric_status"]["idle_cash"] == {
        "complete": False, "missing_rates": [], "data_issues": [{"code": "transfer_amount_unresolved"}],
    }
    assert "idle_cash" not in {a["key"] for a in body["alerts"]}


async def test_category_ranking_uses_converted_mixed_currency_contributions(clock, client, account_id,
                                                                          categories, test_sessionmaker):
    vnd = await account(client, "VND")
    groceries, dining = categories["Groceries"]["id"], categories["Dining Out"]["id"]
    await rate(test_sessionmaker, "USD", "VND", "26000", "2026-06-01")
    await post(client, vnd, "5200000", "2026-06-01", category_id=groceries)
    await post(client, account_id, "50", "2026-06-01", category_id=groceries)
    await post(client, account_id, "300", "2026-06-01", category_id=dining)
    body = (await client.get("/advice")).json()
    assert body["complete"]
    top = next(a for a in body["items"] if a["key"] == "unbudgeted_top_category")
    assert top["params"]["category"] == "Dining Out"  # 300 USD > 250 USD, not 5,200,050 nominal units.
    assert Decimal(top["params"]["amount"]) == 300
    assert top["params"]["currency"] == "USD"
