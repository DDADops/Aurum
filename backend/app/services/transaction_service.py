"""Native transfer validation and the shared Account locking protocol.

All account locks precede transaction row locks. A changed account reference
between discovery and locking returns a retryable conflict, never extends a
partially held lock set out of order.
"""
from collections.abc import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.currency import normalize_currency
from app.models.account import Account
from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.transaction import transfer_rule_violation

# Presence, not value differences: even resending an unchanged financial field
# requires reconciliation of a legacy row. Date is financial for dated reports.
FINANCIAL_PATCH_FIELDS = frozenset({
    "type", "account_id", "transfer_account_id", "amount", "transfer_amount",
    "category_id", "splits", "date",
})


async def lock_accounts(session: AsyncSession, ids: Iterable[int | None]) -> dict[int, Account]:
    ordered = sorted({id_ for id_ in ids if id_ is not None})
    rows = await session.execute(
        select(Account).where(Account.id.in_(ordered)).order_by(Account.id)
        .with_for_update().execution_options(populate_existing=True)
    )
    return {account.id: account for account in rows.scalars()}


def normalize_transfer(fields: dict, accounts: dict[int, Account]) -> None:
    """Mutate only transfer_amount, using currencies read under Account locks."""
    violation = transfer_rule_violation(
        type=fields["type"], account_id=fields["account_id"],
        transfer_account_id=fields.get("transfer_account_id"), category_id=fields.get("category_id"),
    )
    if violation:
        raise HTTPException(400, violation)
    source = accounts.get(fields["account_id"])
    if source is None:
        raise HTTPException(400, "Source account not found")
    if fields["type"] != TransactionType.TRANSFER:
        if fields.get("transfer_amount") is not None:
            raise HTTPException(400, "transfer_amount is only valid for transfer transactions")
        fields["transfer_amount"] = None
        return
    destination = accounts.get(fields["transfer_account_id"])
    if destination is None:
        raise HTTPException(400, "Destination account not found")
    amount = fields.get("transfer_amount")
    if normalize_currency(source.currency) == normalize_currency(destination.currency):
        if amount is not None and amount != fields["amount"]:
            raise HTTPException(400, "same_currency_transfer_amount_must_equal_amount")
        fields["transfer_amount"] = fields["amount"]
    elif amount is None:
        raise HTTPException(400, "cross_currency_transfer_amount_required")


async def lock_transaction(
    session: AsyncSession, transaction_id: int, requested_ids: Iterable[int | None] = (),
) -> tuple[Transaction, dict[int, Account]]:
    snapshot = (await session.execute(select(
        Transaction.account_id, Transaction.transfer_account_id,
    ).where(Transaction.id == transaction_id))).one_or_none()
    if snapshot is None:
        raise HTTPException(404, "Transaction not found")
    accounts = await lock_accounts(session, [*snapshot, *requested_ids])
    transaction = await session.scalar(
        select(Transaction).where(Transaction.id == transaction_id)
        .options(selectinload(Transaction.tags), selectinload(Transaction.splits))
        .with_for_update().execution_options(populate_existing=True)
    )
    if transaction is None:
        raise HTTPException(404, "Transaction not found")
    if (transaction.account_id, transaction.transfer_account_id) != tuple(snapshot):
        raise HTTPException(409, "transaction_accounts_changed_retry")
    return transaction, accounts
