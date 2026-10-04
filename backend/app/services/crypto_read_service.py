"""Crypto read arithmetic only: no provider calls and no monetary writes."""
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding, CryptoTransaction
from app.models.enums import AssetClass, CryptoTransactionType
from app.schemas.crypto import CryptoHoldingRead, CryptoHistoryPoint, CryptoHistoryResponse
from app.schemas.money import FinancialDataStatus
from app.services.financial_data_service import merge_statuses
from app.services.settings_service import get_or_create_app_settings
from app.services.valuation_read_service import ReferenceValues, ValuationHistory, issue


async def reference_values(session: AsyncSession) -> ReferenceValues:
    settings = await get_or_create_app_settings(session)
    return ReferenceValues(session, settings.currency, settings.fx_max_age_days)


async def position(transactions: list[CryptoTransaction], fx: ReferenceValues) -> tuple[Decimal, Decimal | None, FinancialDataStatus]:
    """Weighted remaining cost; a fully closed position clears prior issues.

    SELL quotes/prices are irrelevant to remaining cost. Preserve the existing
    journal's zero clamp on oversell; do not invent realized gain accounting.
    """
    quantity, cost = Decimal(0), Decimal(0)
    status = FinancialDataStatus()
    for tx in sorted(transactions, key=lambda row: (row.date, row.id)):
        if tx.type == CryptoTransactionType.BUY:
            if tx.quote_currency is None:
                converted, buy_status = None, issue("crypto_transaction_quote_currency_unconfirmed")
            else:
                converted, buy_status = await fx.value(tx.quantity * tx.price_per_unit, tx.quote_currency, tx.date)
            quantity += tx.quantity
            status = merge_statuses(status, buy_status)
            cost = cost + converted if status.complete else None
        else:
            remaining = quantity - tx.quantity
            if remaining <= 0:
                quantity, cost, status = Decimal(0), Decimal(0), FinancialDataStatus()
            else:
                cost = cost * remaining / quantity if status.complete else None
                quantity = remaining
    # Existing response semantics: no average/cost for an exited position.
    return quantity, cost if quantity else None, status


async def holding_read(holding: CryptoHolding, fx: ReferenceValues) -> CryptoHoldingRead:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        quantity, cost, cost_status = await position(holding.transactions, fx)
        known = (holding.last_price is not None and holding.last_price_currency is not None
                 and holding.last_price_as_of_date is not None)
        price_status = FinancialDataStatus() if known else issue("crypto_price_provenance_unconfirmed")
        if known:
            value, value_status = await fx.value(quantity * holding.last_price,
                holding.last_price_currency, holding.last_price_as_of_date)
        else:
            value, value_status = None, price_status
        pnl_status = merge_statuses(cost_status, value_status)
        pnl = value - cost if pnl_status.complete and cost is not None else None
        metrics = {
            "quantity": FinancialDataStatus(), "current_price": price_status,
            "avg_buy_price": cost_status, "cost_basis": cost_status,
            "value": value_status, "profit_loss": pnl_status, "profit_loss_percent": pnl_status,
        }
        return CryptoHoldingRead(
            asset_id=holding.asset_id, portfolio_id=holding.portfolio_id,
            coingecko_id=holding.coingecko_id, symbol=holding.symbol, name=holding.name,
            thumb_url=holding.thumb_url, risk_level=holding.asset.risk_level, network=holding.network,
            quantity=quantity, avg_buy_price=cost / quantity if cost is not None and quantity else None,
            cost_basis=cost, cost_basis_currency=fx.currency, currency=fx.currency,
            # Keep current_price provider-native; value/cost/P&L are in target.
            current_price=holding.last_price if known else None,
            last_price_currency=holding.last_price_currency, last_price_as_of_date=holding.last_price_as_of_date,
            **{f"price_change_{period}": getattr(holding, f"price_change_{period}")
               for period in ("1h", "24h", "7d", "30d", "1y")},
            value=value, profit_loss=pnl,
            profit_loss_percent=float(pnl / cost * 100) if pnl is not None and cost else None,
            metric_status=metrics, **merge_statuses(*metrics.values()).model_dump(),
        )


async def holding_reads(session: AsyncSession, holdings: list[CryptoHolding]) -> list[CryptoHoldingRead]:
    fx = await reference_values(session)
    reads = [await holding_read(holding, fx) for holding in holdings]
    # Complete target-currency invested amounts first; stable ID breaks ties.
    return sorted(reads, key=lambda row: (row.cost_basis is None,
        row.cost_basis.copy_negate() if row.cost_basis is not None else Decimal(0), row.asset_id))


async def history(session: AsyncSession, range_key: str, portfolio_id: int | None,
                  today: date, ranges: dict[str, int]) -> CryptoHistoryResponse:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        fx = await reference_values(session)
        stmt = select(Asset.id).join(CryptoHolding, CryptoHolding.asset_id == Asset.id).where(Asset.asset_class == AssetClass.CRYPTO)
        if portfolio_id is not None:
            stmt = stmt.where(CryptoHolding.portfolio_id == portfolio_id)
        ids = set((await session.scalars(stmt)).all())
        events = {id_: [] for id_ in ids}
        for row in await session.execute(select(AssetValuation.asset_id, AssetValuation.as_of_date,
            AssetValuation.value, AssetValuation.currency, AssetValuation.currency_confirmed)
            .where(AssetValuation.asset_id.in_(ids)).order_by(AssetValuation.as_of_date)):
            events[row.asset_id].append((row.as_of_date, row.value, row.currency, row.currency_confirmed))
        histories = [ValuationHistory(rows) for rows in events.values()]
        dates = [event[0] for rows in events.values() for event in rows if event[0] <= today]
        start = today - timedelta(days=ranges[range_key] - 1) if range_key in ranges else min(dates, default=today)
        points = []
        day = start
        while ids and day <= today:
            values, statuses = [], []
            for item in histories:
                value, status = await item.at(day, fx)
                statuses.append(status)
                if value is not None:
                    values.append(value)
            status = merge_statuses(*statuses)
            points.append(CryptoHistoryPoint(date=day, value=sum(values, Decimal(0)) if status.complete else None,
                                            **status.model_dump()))
            day += timedelta(days=1)
        current_status = FinancialDataStatus(**points[-1].model_dump(exclude={"date", "value"})) if points else FinancialDataStatus()
        start_status = FinancialDataStatus(**points[0].model_dump(exclude={"date", "value"})) if points else FinancialDataStatus()
        change_status = merge_statuses(current_status, start_status)
        current = points[-1].value if points else Decimal(0)
        initial = points[0].value if points else Decimal(0)
        change = current - initial if change_status.complete else None
        series_status = merge_statuses(*points)
        return CryptoHistoryResponse(currency=fx.currency, range=range_key, current=current,
            change_amount=change, change_percent=float(change / initial * 100) if change is not None and initial else None,
            series=points, metric_status={"current": current_status, "change_amount": change_status,
                "change_percent": change_status, "series": series_status}, **series_status.model_dump())
