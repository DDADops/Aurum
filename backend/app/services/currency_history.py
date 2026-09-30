"""Dynamic history predicates, shared by currency guards and read metadata.

Account updates lock their parent row before these queries. Transaction writers
share that protocol through transaction_service; predicates alone are not locks.
No recurring instruction or empty holding is historical money.
"""
from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetValuation
from app.models.crypto import CryptoTransaction
from app.models.transaction import Transaction


async def account_has_history(session: AsyncSession, account_id: int) -> bool:
    return bool(await session.scalar(select(exists().where(or_(
        Transaction.account_id == account_id, Transaction.transfer_account_id == account_id,
    )))))


async def asset_has_history(session: AsyncSession, asset_id: int) -> bool:
    return bool(await session.scalar(select(or_(
        exists().where(AssetValuation.asset_id == asset_id),
        exists().where(CryptoTransaction.asset_id == asset_id),
    ))))
