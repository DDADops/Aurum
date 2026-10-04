"""Daily denomination is immutable; all writers serialize on the parent Asset."""
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import normalize_currency
from app.models.asset import Asset, AssetValuation


async def lock_asset(session: AsyncSession, asset_id: int) -> Asset:
    asset = await session.scalar(select(Asset).where(Asset.id == asset_id)
                                 .with_for_update().execution_options(populate_existing=True))
    if asset is None:
        raise HTTPException(404, "Asset not found")
    return asset


async def write_valuation(session: AsyncSession, asset_id: int, value: Decimal,
                          as_of_date: date, currency: str) -> None:
    # Parent lock also serializes the absent-row case; UNIQUE remains the final
    # DB protection. Every production writer follows Asset -> holding -> valuation.
    await lock_asset(session, asset_id)
    currency = normalize_currency(currency)
    row = await session.scalar(select(AssetValuation).where(
        AssetValuation.asset_id == asset_id, AssetValuation.as_of_date == as_of_date,
    ).with_for_update().execution_options(populate_existing=True))
    if row is not None:
        if not row.currency_confirmed or row.currency != currency:
            raise HTTPException(409, "valuation_provenance_conflict")
        row.value = value
    else:
        session.add(AssetValuation(asset_id=asset_id, value=value, as_of_date=as_of_date,
                                   currency=currency, currency_confirmed=True))
    await session.flush()
