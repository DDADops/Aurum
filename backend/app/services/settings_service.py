from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.currency import normalize_currency
from app.models.settings import AppSettings


async def get_or_create_app_settings(session: AsyncSession) -> AppSettings:
    """Singleton row (id=1) — get-or-create so callers never hit a missing
    row even if app startup's seeding hasn't run yet (e.g. a fresh test DB)."""
    settings = await session.get(AppSettings, 1)
    if settings is None:
        settings = AppSettings(id=1, currency=get_settings().default_currency)
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def effective_default_currency(session: AsyncSession) -> str:
    """Read the singleton without committing the caller's transaction."""
    currency = await session.scalar(select(AppSettings.currency).where(AppSettings.id == 1))
    return normalize_currency(currency if currency is not None else get_settings().default_currency)
