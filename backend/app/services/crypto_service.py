"""Crypto price sync + position accounting: CoinGecko Demo API calls,
written into the *existing* Asset/AssetValuation engine (see
models/asset.py) rather than a parallel value-tracking system. A holding's
value is an AssetValuation row with its own denomination. Aggregate FX
integration is deferred; unknown or mixed quote money is not summed as known.

Quantity and average buy price are never stored — they're derived from the
CryptoTransaction log every time (see _compute_position), the same
"you record it, we derive it" shape as Goal/GoalContribution.

Refreshes are deliberately never automatic in the background — same
reasoning as recurring transactions (see services/recurring_service.py):
there's no scheduler/worker in this stack, and a rate-limited free API key
makes "poll constantly" actively counterproductive anyway. Three triggers
only: the "Refresh prices" button (force=True), a lazy once-a-day check
that piggybacks on GET /crypto/holdings (force=False), and adding a brand
new holding (a one-off seed fetch, justified by the explicit user action).
Editing quantity via a buy/sell transaction never calls CoinGecko — it
reuses the last cached price.
"""
import asyncio
from datetime import date as date_
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from itertools import groupby
from typing import NamedTuple

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding, CryptoPortfolio, CryptoSyncState, CryptoTransaction
from app.models.enums import AssetClass, CapitalRole, CryptoTransactionType
from app.schemas.crypto import (
    CryptoHistoryPoint,
    CryptoHistoryResponse,
    CryptoHoldingCreate,
    CryptoHoldingRead,
    CryptoHoldingUpdate,
    CryptoPerformancePoint,
    CryptoPerformanceResponse,
    CryptoPortfolioCreate,
    CryptoPortfolioRead,
    CryptoPortfolioUpdate,
    CryptoSearchResult,
    CryptoSyncResult,
    CryptoTransactionCreate,
    CryptoTransactionUpdate,
)
from app.services.settings_service import effective_default_currency, get_or_create_app_settings
from app.services.valuation_service import lock_asset, write_valuation

# Same 8-hue, colorblind-safe categorical set app/db/seed.py assigns default
# categories from — reused here (cycling by creation order) so portfolio tab
# dots are visually distinct out of the box, with no manual color picker to
# build for a v1 feature.
PORTFOLIO_PALETTE = [
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#4a3aa7",
    "#e34948",
]

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
# CoinGecko's own Demo-tier data only refreshes every 60s server-side
# (see docs.coingecko.com/reference/simple-price) — polling more often than
# once a day here buys nothing and only spends rate-limit budget for no
# reason (see module docstring on why there's no background poller at all).
AUTO_REFRESH_INTERVAL = timedelta(hours=24)

# Deliberately no "24h" option, unlike Net Worth's own RANGE_DAYS — our price
# history is only ever as dense as our sync cadence (at most a few points a
# day, see AUTO_REFRESH_INTERVAL above), so a 24h chart would show one or
# two points, not the smooth intraday line a denser data source could draw.
CRYPTO_RANGE_DAYS: dict[str, int] = {"7d": 7, "30d": 30, "90d": 90}

_EAGER = (selectinload(CryptoHolding.transactions), selectinload(CryptoHolding.asset))


def _require_api_key() -> str:
    api_key = get_settings().coingecko_api_key
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "CoinGecko API key not configured — set AURUM_COINGECKO_API_KEY in .env "
                "(free Demo key, no card required: coingecko.com/en/api/pricing)."
            ),
        )
    return api_key


class _MarketPoint(NamedTuple):
    price: Decimal
    change_1h: Decimal | None
    change_24h: Decimal | None
    change_7d: Decimal | None
    # Defaulted so existing call sites/tests built before these two existed
    # don't have to name them. 1y stands in for "all time" on the Best/Worst
    # Performer stat (see get_90d_performance's docstring on why 90d alone
    # needs a different, on-demand code path).
    change_30d: Decimal | None = None
    change_1y: Decimal | None = None


def _decimal_or_none(value: object) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


async def _fetch_market_data(coingecko_ids: list[str], vs_currency: str) -> dict[str, _MarketPoint]:
    """One CoinGecko call for every tracked coin at once (up to 250 ids per
    /coins/markets call) — never one call per coin, that's what would
    actually burn through a free-tier rate limit. Also returns 1h/24h/7d/30d/1y
    % change in the same call, so this fully replaces a separate
    /simple/price lookup. (90d isn't offered here at all — CoinGecko's own
    supported windows skip straight from 30d to 200d; see
    get_90d_performance for how the Crypto tab's 90d Best/Worst Performer
    view is computed instead.)"""
    if not coingecko_ids:
        return {}
    api_key = _require_api_key()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{COINGECKO_BASE_URL}/coins/markets",
            params={
                "vs_currency": vs_currency,
                "ids": ",".join(coingecko_ids),
                "price_change_percentage": "1h,24h,7d,30d,1y",
            },
            headers={"x-cg-demo-api-key": api_key},
        )
        response.raise_for_status()
        data = response.json(parse_float=Decimal)

    points: dict[str, _MarketPoint] = {}
    for coin in data:
        price = _decimal_or_none(coin.get("current_price"))
        if price is None:
            continue
        points[coin["id"]] = _MarketPoint(
            price=price,
            change_1h=_decimal_or_none(coin.get("price_change_percentage_1h_in_currency")),
            change_24h=_decimal_or_none(coin.get("price_change_percentage_24h_in_currency")),
            change_7d=_decimal_or_none(coin.get("price_change_percentage_7d_in_currency")),
            change_30d=_decimal_or_none(coin.get("price_change_percentage_30d_in_currency")),
            change_1y=_decimal_or_none(coin.get("price_change_percentage_1y_in_currency")),
        )
    return points


async def search_coins(query: str) -> list[CryptoSearchResult]:
    """Backs the "add a coin" picker — the whole reason a user can find the
    right coingecko_id without knowing it by heart, same as typing a name
    into CoinMarketCap's own search box."""
    api_key = _require_api_key()
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{COINGECKO_BASE_URL}/search",
            params={"query": query},
            headers={"x-cg-demo-api-key": api_key},
        )
        response.raise_for_status()
        data = response.json()
    return [
        CryptoSearchResult(
            coingecko_id=coin["id"],
            symbol=str(coin["symbol"]).upper(),
            name=coin["name"],
            thumb_url=coin.get("thumb"),
        )
        for coin in data.get("coins", [])[:20]
    ]


def _compute_position(transactions: list[CryptoTransaction]) -> tuple[Decimal, Decimal | None]:
    """Replay quantities; average cost is meaningful only in one known quote.

    Buys blend weighted cost; sells keep the remaining average unchanged.
    Mixed/unknown quotes require Stage 2 conversion or explicit reconciliation.
    """
    with localcontext() as context:
        context.prec = 96
        quotes = {t.quote_currency for t in transactions}
        known_quote = len(quotes) == 1 and None not in quotes
        quantity = Decimal("0")
        avg_price: Decimal | None = None
        for tx in sorted(transactions, key=lambda t: (t.date, t.id)):
            if tx.type == CryptoTransactionType.BUY:
                existing_cost = (avg_price or Decimal("0")) * quantity
                quantity += tx.quantity
                if known_quote:
                    avg_price = (existing_cost + tx.price_per_unit * tx.quantity) / quantity if quantity else None
            else:
                quantity -= tx.quantity
                if quantity <= 0:
                    quantity = Decimal("0")
                    avg_price = None
        return quantity, avg_price


def _to_read(holding: CryptoHolding) -> CryptoHoldingRead:
    with localcontext() as context:
        context.prec = 96
        quantity, avg_buy_price = _compute_position(holding.transactions)
        current_price = holding.last_price if _known_price(holding) else None
        value = current_price * quantity if current_price is not None else None
        cost_basis = avg_buy_price * quantity if avg_buy_price is not None else None
        quote = next(iter({t.quote_currency for t in holding.transactions}), None)
        profit_loss = (value - cost_basis) if value is not None and cost_basis is not None and quote == holding.last_price_currency else None
        profit_loss_percent = float(profit_loss / cost_basis * 100) if profit_loss is not None and cost_basis else None
        return CryptoHoldingRead(
            asset_id=holding.asset_id,
            portfolio_id=holding.portfolio_id,
            coingecko_id=holding.coingecko_id,
            symbol=holding.symbol,
            name=holding.name,
            thumb_url=holding.thumb_url,
            risk_level=holding.asset.risk_level,
            network=holding.network,
            quantity=quantity,
            avg_buy_price=avg_buy_price,
            cost_basis_currency=quote if avg_buy_price is not None else None,
            current_price=current_price,
            last_price_currency=holding.last_price_currency,
            last_price_as_of_date=holding.last_price_as_of_date,
            price_change_1h=holding.price_change_1h,
            price_change_24h=holding.price_change_24h,
            price_change_7d=holding.price_change_7d,
            price_change_30d=holding.price_change_30d,
            price_change_1y=holding.price_change_1y,
            value=value,
            cost_basis=cost_basis,
            profit_loss=profit_loss,
            profit_loss_percent=profit_loss_percent,
        )


def _sort_by_invested(reads: list[CryptoHoldingRead]) -> list[CryptoHoldingRead]:
    """Biggest cost basis (money actually put in, not current market value)
    first, so the Holdings table reads as "what am I most committed to"
    rather than an arbitrary/alphabetical order. A fully-exited position
    (cost_basis None — everything's been sold) sorts last, not first: -1 can
    never collide with a real cost basis, which is never negative."""
    return sorted(reads, key=lambda r: r.cost_basis if r.cost_basis is not None else Decimal("-1"), reverse=True)


def _portfolio_to_read(portfolio: CryptoPortfolio) -> CryptoPortfolioRead:
    return CryptoPortfolioRead.model_validate(portfolio)


async def list_portfolios(session: AsyncSession, include_archived: bool) -> list[CryptoPortfolioRead]:
    stmt = select(CryptoPortfolio).order_by(CryptoPortfolio.id)
    if not include_archived:
        stmt = stmt.where(CryptoPortfolio.is_archived.is_(False))
    portfolios = (await session.execute(stmt)).scalars().all()
    return [_portfolio_to_read(p) for p in portfolios]


async def get_or_create_default_portfolio(session: AsyncSession) -> CryptoPortfolio:
    """The portfolio a new holding lands in when the caller doesn't specify
    one (see CryptoHoldingCreate.portfolio_id) — the earliest-created
    portfolio, auto-created the first time it's needed. Same self-healing
    "create on first use" shape as seed_default_app_settings: a portfolio can
    later be deleted once empty (see delete_portfolio), so this can't just
    assume row id=1 always exists."""
    existing = (
        await session.execute(select(CryptoPortfolio).order_by(CryptoPortfolio.id).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    portfolio = CryptoPortfolio(name="Main Portfolio", color=PORTFOLIO_PALETTE[0])
    session.add(portfolio)
    await session.flush()
    return portfolio


async def create_portfolio(session: AsyncSession, payload: CryptoPortfolioCreate) -> CryptoPortfolioRead:
    count = (await session.execute(select(CryptoPortfolio.id))).scalars().all()
    color = PORTFOLIO_PALETTE[len(count) % len(PORTFOLIO_PALETTE)]
    portfolio = CryptoPortfolio(name=payload.name, color=color)
    session.add(portfolio)
    await session.commit()
    await session.refresh(portfolio)
    return _portfolio_to_read(portfolio)


async def update_portfolio(
    session: AsyncSession, portfolio_id: int, payload: CryptoPortfolioUpdate
) -> CryptoPortfolioRead:
    portfolio = await session.get(CryptoPortfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Crypto portfolio not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(portfolio, field, value)
    await session.commit()
    await session.refresh(portfolio)
    return _portfolio_to_read(portfolio)


async def delete_portfolio(session: AsyncSession, portfolio_id: int) -> None:
    portfolio = await session.get(CryptoPortfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Crypto portfolio not found")
    has_holding = (
        await session.execute(select(CryptoHolding.asset_id).where(CryptoHolding.portfolio_id == portfolio_id).limit(1))
    ).first()
    if has_holding is not None:
        raise HTTPException(status_code=400, detail="Move or delete its coins before deleting a portfolio")
    await session.delete(portfolio)
    await session.commit()


async def list_holdings(session: AsyncSession, portfolio_id: int | None = None) -> list[CryptoHolding]:
    stmt = select(CryptoHolding).options(*_EAGER).order_by(CryptoHolding.name)
    if portfolio_id is not None:
        stmt = stmt.where(CryptoHolding.portfolio_id == portfolio_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _get_holding_or_404(session: AsyncSession, asset_id: int) -> CryptoHolding:
    result = await session.execute(select(CryptoHolding).options(*_EAGER).where(CryptoHolding.asset_id == asset_id))
    holding = result.scalar_one_or_none()
    if holding is None:
        raise HTTPException(status_code=404, detail="Crypto holding not found")
    return holding


async def get_or_create_sync_state(session: AsyncSession) -> CryptoSyncState:
    state = await session.get(CryptoSyncState, 1)
    if state is None:
        state = CryptoSyncState(id=1, last_synced_at=None)
        session.add(state)
        await session.commit()
        await session.refresh(state)
    return state


def _known_price(holding: CryptoHolding) -> bool:
    return (holding.last_price is not None and holding.last_price_currency is not None
            and holding.last_price_as_of_date is not None)


def _value(quantity: Decimal, price: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 96
        return quantity * price


async def _lock_holding(session: AsyncSession, asset_id: int) -> CryptoHolding:
    await lock_asset(session, asset_id)
    holding = await session.scalar(select(CryptoHolding).where(CryptoHolding.asset_id == asset_id)
        .options(*_EAGER).with_for_update().execution_options(populate_existing=True))
    if holding is None:
        raise HTTPException(404, "Crypto holding not found")
    return holding


async def _cache_valuation(session: AsyncSession, holding: CryptoHolding) -> None:
    if _known_price(holding):
        quantity, _ = _compute_position(holding.transactions)
        await write_valuation(session, holding.asset_id, _value(quantity, holding.last_price),
                              date_.today(), holding.last_price_currency)


async def _store_observation(session: AsyncSession, holding: CryptoHolding,
                             point: _MarketPoint, currency: str, day: date_) -> None:
    quantity, _ = _compute_position(holding.transactions)
    existing = await session.scalar(select(AssetValuation.id).where(
        AssetValuation.asset_id == holding.asset_id, AssetValuation.as_of_date == day))
    if quantity > 0 or existing is not None:
        await write_valuation(session, holding.asset_id, _value(quantity, point.price), day, currency)
    holding.last_price = point.price
    holding.last_price_currency = currency
    # Local successful-fetch date, not an exchange historical close.
    holding.last_price_as_of_date = day
    for period in ("1h", "24h", "7d", "30d", "1y"):
        setattr(holding, f"price_change_{period}", getattr(point, f"change_{period}"))


async def refresh_prices(session: AsyncSession, *, force: bool, portfolio_id: int | None = None) -> CryptoSyncResult:
    """Sync always covers every holding regardless of `portfolio_id` — a
    stale price on a coin the user isn't currently looking at would still be
    wrong the next time they switch tabs. `portfolio_id` only narrows what's
    returned in the response's `holdings` list, for the Crypto tab's
    portfolio filter."""
    state = await get_or_create_sync_state(session)
    now = datetime.now(timezone.utc)

    if not force and state.last_synced_at is not None and now - state.last_synced_at < AUTO_REFRESH_INTERVAL:
        holdings = await list_holdings(session, portfolio_id)
        return CryptoSyncResult(
            synced=False, last_synced_at=state.last_synced_at, holdings=_sort_by_invested([_to_read(h) for h in holdings])
        )

    holdings = await list_holdings(session)
    # Freeze request assumptions before HTTP. No row locks are acquired here.
    requested = {h.asset_id: (h.coingecko_id, h.asset.currency) for h in holdings}
    quote = await effective_default_currency(session)
    try:
        market_data = await _fetch_market_data([h.coingecko_id for h in holdings], quote.lower())
    except httpx.HTTPError:
        return CryptoSyncResult(synced=False, last_synced_at=state.last_synced_at,
            error_key="unreachable", holdings=_sort_by_invested([
                _to_read(h) for h in holdings if portfolio_id is None or h.portfolio_id == portfolio_id]))
    day = date_.today()
    try:
        # Stable Asset -> Holding order; quantities are re-read after locking.
        for asset_id in sorted(requested):
            holding = await _lock_holding(session, asset_id)
            coin, native = requested[asset_id]
            if (holding.coingecko_id, holding.asset.currency) != (coin, native):
                raise HTTPException(409, "crypto_refresh_state_changed_retry")
            point = market_data.get(coin)
            if point is not None:
                # A settings change during HTTP does not relabel this observation:
                # persist the exact requested quote, subject to daily conflicts.
                await _store_observation(session, holding, point, quote, day)
        state.last_synced_at = datetime.now(timezone.utc)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    visible = await list_holdings(session, portfolio_id)
    return CryptoSyncResult(synced=True, last_synced_at=state.last_synced_at,
                           holdings=_sort_by_invested([_to_read(h) for h in visible]))


async def create_holding(session: AsyncSession, payload: CryptoHoldingCreate) -> CryptoHoldingRead:
    quote = await effective_default_currency(session)
    state = await get_or_create_sync_state(session)
    # Seed HTTP precedes all Asset/Holding/Transaction inserts and row locks.
    try:
        market_data = await _fetch_market_data([payload.coingecko_id], quote.lower())
    except httpx.HTTPError:
        market_data = None
    day = date_.today()

    if payload.portfolio_id is not None:
        portfolio = await session.get(CryptoPortfolio, payload.portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=400, detail="Crypto portfolio not found")
        portfolio_id = portfolio.id
    else:
        portfolio_id = (await get_or_create_default_portfolio(session)).id

    asset = Asset(
        name=payload.name,
        asset_class=AssetClass.CRYPTO,
        currency=quote,
        capital_role=CapitalRole.NEUTRAL,
        # User-chosen at add time (see CryptoHoldingCreate), defaulting to
        # HIGH — not the Asset model's own MEDIUM default — since this app's
        # own risk-level copy names crypto as the textbook HIGH example (see
        # lib/i18n.ts's netWorth.riskLevelFormHint.high). Editable later via
        # PATCH /assets/{asset_id}, same as any other Asset.
        risk_level=payload.risk_level,
    )
    session.add(asset)
    await session.flush()

    holding = CryptoHolding(
        asset_id=asset.id,
        portfolio_id=portfolio_id,
        coingecko_id=payload.coingecko_id,
        symbol=payload.symbol.upper(),
        name=payload.name,
        thumb_url=payload.thumb_url,
        network=payload.network,
    )
    # Wired explicitly rather than left for a lazy load off asset_id — under
    # async SQLAlchemy, a relationship access with no eager load and no prior
    # assignment raises MissingGreenlet, and _to_read (called at the end of
    # this function) reads holding.asset.risk_level.
    holding.asset = asset
    session.add(holding)

    holding.transactions.append(
        CryptoTransaction(
            type=CryptoTransactionType.BUY,
            quantity=payload.quantity,
            price_per_unit=payload.price_per_unit,
            quote_currency=quote,
            date=payload.date,
            note=payload.note,
        )
    )
    await session.flush()

    await _lock_holding(session, asset.id)
    if market_data is not None:
        point = market_data.get(payload.coingecko_id)
        if point is not None:
            await _store_observation(session, holding, point, quote, day)
        state.last_synced_at = datetime.now(timezone.utc)

    await session.commit()
    return _to_read(holding)


async def update_holding(session: AsyncSession, asset_id: int, payload: CryptoHoldingUpdate) -> CryptoHoldingRead:
    """Updates CryptoHolding-only metadata — currently just `network`.
    Quantity/price/date go through add_transaction/update_transaction
    instead; risk_level lives on the Asset via PATCH /assets/{asset_id}."""
    holding = await _lock_holding(session, asset_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(holding, field, value)
    await session.commit()
    return _to_read(holding)


async def add_transaction(session: AsyncSession, asset_id: int, payload: CryptoTransactionCreate) -> CryptoHoldingRead:
    """A buy or sell against an existing holding — never calls CoinGecko
    (see module docstring): value is recomputed from the last cached
    price, same principle as the old quantity-only edit it replaces."""
    holding = await _lock_holding(session, asset_id)
    quote = payload.quote_currency or await effective_default_currency(session)

    if payload.type == CryptoTransactionType.SELL:
        current_quantity, _ = _compute_position(holding.transactions)
        if payload.quantity > current_quantity:
            raise HTTPException(status_code=400, detail="Cannot sell more than you currently hold")

    holding.transactions.append(
        CryptoTransaction(
            type=payload.type,
            quantity=payload.quantity,
            price_per_unit=payload.price_per_unit,
            quote_currency=quote,
            date=payload.date,
            note=payload.note,
        )
    )
    await session.flush()

    await _cache_valuation(session, holding)

    await session.commit()
    return _to_read(holding)


async def update_transaction(
    session: AsyncSession, transaction_id: int, payload: CryptoTransactionUpdate
) -> CryptoHoldingRead:
    """Edit an existing buy/sell entry — the "view/edit a transaction" flow
    a plain delete-and-recreate can't offer (you'd lose the original id and
    any history a future feature might key off it). Never calls CoinGecko,
    same as add_transaction: value is recomputed from the last cached price."""
    transaction = await session.get(CryptoTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Crypto transaction not found")
    asset_id = transaction.asset_id
    holding = await _lock_holding(session, asset_id)

    transaction = next((t for t in holding.transactions if t.id == transaction_id), None)
    if transaction is None:
        raise HTTPException(404, "Crypto transaction not found")
    updates = payload.model_dump(exclude_unset=True)
    if "quote_currency" in updates and "price_per_unit" not in updates:
        raise HTTPException(422, "crypto_price_reentry_required")
    if any(updates.get(k, 1) is None for k in ("type", "quantity", "price_per_unit", "date")):
        raise HTTPException(422, "Crypto monetary fields cannot be null")
    if "price_per_unit" in updates:
        updates["quote_currency"] = updates.get("quote_currency") or await effective_default_currency(session)
    effective_type = updates.get("type", transaction.type)
    effective_quantity = updates.get("quantity", transaction.quantity)

    if effective_type == CryptoTransactionType.SELL:
        # Same guard as add_transaction, checked against every *other*
        # transaction on this holding — editing this one in place must not
        # let it sell more than what the rest of the log would leave held.
        others = [t for t in holding.transactions if t.id != transaction_id]
        quantity_without_this, _ = _compute_position(others)
        if effective_quantity > quantity_without_this:
            raise HTTPException(status_code=400, detail="Cannot sell more than you currently hold")

    for field, value in updates.items():
        setattr(transaction, field, value)
    await session.flush()

    if {"quantity", "price_per_unit", "type", "date"} & payload.model_fields_set:
        await _cache_valuation(session, holding)

    await session.commit()
    return _to_read(holding)


async def list_transactions(session: AsyncSession, asset_id: int) -> list[CryptoTransaction]:
    await _get_holding_or_404(session, asset_id)  # 404s if the holding itself doesn't exist
    result = await session.execute(
        select(CryptoTransaction)
        .where(CryptoTransaction.asset_id == asset_id)
        .order_by(CryptoTransaction.date.desc(), CryptoTransaction.id.desc())
    )
    return list(result.scalars().all())


async def delete_transaction(session: AsyncSession, transaction_id: int) -> None:
    transaction = await session.get(CryptoTransaction, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Crypto transaction not found")
    asset_id = transaction.asset_id
    holding = await _lock_holding(session, asset_id)
    transaction = next((t for t in holding.transactions if t.id == transaction_id), None)
    if transaction is None:
        raise HTTPException(404, "Crypto transaction not found")
    holding.transactions.remove(transaction)
    await session.flush()
    await _cache_valuation(session, holding)

    await session.commit()


async def get_crypto_history(
    session: AsyncSession, range_key: str, portfolio_id: int | None = None
) -> CryptoHistoryResponse:
    """Total crypto holdings value over time, for the History chart on the
    Crypto tab. Same "cumulative point events, forward-filled" approach as
    net_worth_service.py's own asset series — duplicated here rather than
    imported, so this can't destabilize the already-tested Net Worth engine,
    and scoped to crypto-class assets only (a deleted holding's
    AssetValuation rows are gone via cascade, so its history naturally
    drops out here too, same as it already does for Net Worth). Further
    scoped to one portfolio's assets when `portfolio_id` is given, for the
    Crypto tab's portfolio filter."""
    today = date_.today()

    asset_stmt = (
        select(Asset.id)
        .join(CryptoHolding, CryptoHolding.asset_id == Asset.id)
        .where(Asset.asset_class == AssetClass.CRYPTO)
    )
    if portfolio_id is not None:
        asset_stmt = asset_stmt.where(CryptoHolding.portfolio_id == portfolio_id)
    crypto_asset_ids = set((await session.execute(asset_stmt)).scalars().all())
    if not crypto_asset_ids:
        return CryptoHistoryResponse(range=range_key, current=Decimal("0"), change_amount=Decimal("0"), change_percent=None, series=[])

    # Read values and provenance in the same statement snapshot.
    rows = (await session.execute(select(
        AssetValuation.asset_id, AssetValuation.as_of_date, AssetValuation.value,
        AssetValuation.currency, AssetValuation.currency_confirmed,
    ).where(AssetValuation.asset_id.in_(crypto_asset_ids))
     .order_by(AssetValuation.as_of_date))).all()
    if any(not row.currency_confirmed or row.currency is None for row in rows):
        raise HTTPException(409, "valuation_currency_unconfirmed")
    currencies = {row.currency for row in rows}
    if len(currencies) > 1:
        raise HTTPException(409, "crypto_aggregate_conversion_required")
    history_currency = next(iter(currencies), None)
    valuations = [(row.asset_id, row.as_of_date, row.value) for row in rows]
    if not valuations:
        return CryptoHistoryResponse(range=range_key, current=Decimal("0"), change_amount=Decimal("0"), change_percent=None, series=[])

    current_by_asset: dict[int, Decimal] = {}
    events: list[tuple[date_, Decimal]] = []
    for day, group in groupby(valuations, key=lambda row: row[1]):
        for asset_id, _, value in group:
            current_by_asset[asset_id] = value
        events.append((day, sum(current_by_asset.values(), Decimal("0"))))

    start = today - timedelta(days=CRYPTO_RANGE_DAYS[range_key] - 1) if range_key in CRYPTO_RANGE_DAYS else events[0][0]

    points: list[CryptoHistoryPoint] = []
    idx, n = 0, len(events)
    running = Decimal("0")
    day = start
    while day <= today:
        while idx < n and events[idx][0] <= day:
            running = events[idx][1]
            idx += 1
        points.append(CryptoHistoryPoint(date=day, value=running))
        day += timedelta(days=1)

    current = points[-1].value if points else Decimal("0")
    start_value = points[0].value if points else Decimal("0")
    change_amount = current - start_value
    change_percent = float(change_amount / start_value * 100) if start_value else None

    return CryptoHistoryResponse(
        range=range_key,
        current=current,
        change_amount=change_amount,
        change_percent=change_percent,
        series=points,
        currency=history_currency,
    )


async def _fetch_90d_change(client: httpx.AsyncClient, api_key: str, coingecko_id: str, vs_currency: str) -> float | None:
    """A coin's own real price 90 days ago vs now, straight from CoinGecko's
    market_chart series — the closest and last point in the returned series,
    respectively. Any failure (rate limit, outage, a coin CoinGecko has less
    than 90 days of data for) returns None rather than raising: one coin's
    hiccup shouldn't blank out every other tile (same "best data on hand"
    principle as refresh_prices' own CoinGecko-outage handling)."""
    try:
        response = await client.get(
            f"{COINGECKO_BASE_URL}/coins/{coingecko_id}/market_chart",
            params={"vs_currency": vs_currency, "days": 90},
            headers={"x-cg-demo-api-key": api_key},
        )
        response.raise_for_status()
        prices = response.json(parse_float=Decimal).get("prices", [])
    except httpx.HTTPError:
        return None
    if len(prices) < 2:
        return None
    first_price, last_price = Decimal(str(prices[0][1])), Decimal(str(prices[-1][1]))
    if not first_price:
        return None
    return float((last_price - first_price) / first_price * 100)


async def get_90d_performance(session: AsyncSession, portfolio_id: int | None) -> CryptoPerformanceResponse:
    """Real 90-day % price change per currently-held coin, for the Crypto
    tab's Best/Worst Performer stat when the 90d range is picked. Unlike
    1h/24h/7d/30d/1y (all one field on the same batched /coins/markets sync
    call, see _fetch_market_data), CoinGecko has no 90-day window on that
    endpoint at all — the only way to get it is one market_chart call per
    coin, so this is deliberately never part of the regular sync and only
    ever runs on an explicit "look at 90d" user action, same justification
    as the seed fetch on adding a new holding. Bounded concurrency (5 at
    once) keeps a big portfolio from firing dozens of requests in the same
    instant."""
    holdings = await list_holdings(session, portfolio_id)
    held = [h for h in holdings if _compute_position(h.transactions)[0] > 0]
    if not held:
        return CryptoPerformanceResponse(items=[])

    settings = await get_or_create_app_settings(session)
    api_key = _require_api_key()
    semaphore = asyncio.Semaphore(5)

    async def fetch_one(client: httpx.AsyncClient, holding: CryptoHolding) -> CryptoPerformancePoint:
        async with semaphore:
            change = await _fetch_90d_change(client, api_key, holding.coingecko_id, settings.currency.lower())
        return CryptoPerformancePoint(asset_id=holding.asset_id, price_change_percent=change)

    async with httpx.AsyncClient(timeout=10.0) as client:
        items = await asyncio.gather(*(fetch_one(client, h) for h in held))
    return CryptoPerformanceResponse(items=list(items))
