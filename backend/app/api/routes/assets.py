from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_session
from app.core.currency import normalize_currency
from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding
from app.schemas.asset import AssetCreate, AssetRead, AssetUpdate, AssetValuationCreate, AssetValuationRead
from app.services.currency_history import asset_has_history

router = APIRouter(prefix="/assets", tags=["assets"])

_EAGER = (selectinload(Asset.valuations),)


async def _to_read(session: AsyncSession, asset: Asset) -> AssetRead:
    latest = asset.valuations[-1] if asset.valuations else None
    return AssetRead(
        id=asset.id,
        has_monetary_history=await asset_has_history(session, asset.id),
        name=asset.name,
        asset_class=asset.asset_class,
        currency=asset.currency,
        notes=asset.notes,
        capital_role=asset.capital_role,
        monthly_cash_flow=asset.monthly_cash_flow,
        risk_level=asset.risk_level,
        current_value=latest.value if latest else 0,
        as_of_date=latest.as_of_date if latest else asset.created_at.date(),
    )


@router.get("", response_model=list[AssetRead])
async def list_assets(session: AsyncSession = Depends(get_session)) -> list[AssetRead]:
    result = await session.execute(select(Asset).options(*_EAGER).order_by(Asset.name))
    return [await _to_read(session, asset) for asset in result.scalars().all()]


@router.post("", response_model=AssetRead, status_code=201)
async def create_asset(payload: AssetCreate, session: AsyncSession = Depends(get_session)) -> AssetRead:
    asset = Asset(
        name=payload.name,
        asset_class=payload.asset_class,
        currency=payload.currency,
        notes=payload.notes,
        capital_role=payload.capital_role,
        monthly_cash_flow=payload.monthly_cash_flow,
        risk_level=payload.risk_level,
    )
    session.add(asset)
    await session.flush()
    session.add(AssetValuation(asset_id=asset.id, value=payload.value, as_of_date=payload.as_of_date))
    await session.commit()

    refreshed = await session.execute(select(Asset).options(*_EAGER).where(Asset.id == asset.id))
    return await _to_read(session, refreshed.scalar_one())


@router.patch("/{asset_id}", response_model=AssetRead)
async def update_asset(asset_id: int, payload: AssetUpdate, session: AsyncSession = Depends(get_session)) -> AssetRead:
    result = await session.execute(
        select(Asset).options(*_EAGER).where(Asset.id == asset_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if payload.currency is not None and payload.currency != normalize_currency(asset.currency):
        if await asset_has_history(session, asset_id):
            raise HTTPException(status_code=409, detail="currency_change_requires_empty_history")
        if asset.monthly_cash_flow is not None and "monthly_cash_flow" not in payload.model_fields_set:
            raise HTTPException(status_code=409, detail="currency_change_requires_cash_flow_replacement")
        holding = await session.get(CryptoHolding, asset_id)
        if holding is not None:
            for field in ("last_price", "price_change_1h", "price_change_24h", "price_change_7d", "price_change_30d", "price_change_1y"):
                setattr(holding, field, None)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    await session.commit()
    await session.refresh(asset, attribute_names=["valuations"])
    return await _to_read(session, asset)


@router.post("/{asset_id}/valuations", response_model=AssetRead)
async def add_asset_valuation(
    asset_id: int, payload: AssetValuationCreate, session: AsyncSession = Depends(get_session)
) -> AssetRead:
    """Records (or corrects) an asset's value as of a date. Re-submitting the
    same date updates that day's value instead of erroring, so users can fix
    a typo without needing a separate edit flow."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")

    upsert_stmt = (
        pg_insert(AssetValuation)
        .values(asset_id=asset_id, value=payload.value, as_of_date=payload.as_of_date)
        .on_conflict_do_update(
            index_elements=[AssetValuation.asset_id, AssetValuation.as_of_date],
            set_={"value": payload.value},
        )
    )
    await session.execute(upsert_stmt)
    await session.commit()

    refreshed = await session.execute(select(Asset).options(*_EAGER).where(Asset.id == asset_id))
    return await _to_read(session, refreshed.scalar_one())


@router.get("/{asset_id}/valuations", response_model=list[AssetValuationRead])
async def list_asset_valuations(asset_id: int, session: AsyncSession = Depends(get_session)) -> list[AssetValuation]:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    result = await session.execute(
        select(AssetValuation).where(AssetValuation.asset_id == asset_id).order_by(AssetValuation.as_of_date)
    )
    return list(result.scalars().all())


@router.delete("/{asset_id}", status_code=204)
async def delete_asset(asset_id: int, session: AsyncSession = Depends(get_session)) -> None:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    await session.delete(asset)
    await session.commit()
