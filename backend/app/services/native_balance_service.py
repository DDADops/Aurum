"""Native account legs and balances. No display currency or reference FX."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import sum_money
from app.models.enums import TransactionType
from app.models.transaction import Transaction


def native_legs(
    kind: TransactionType, amount: Decimal, source: int,
    destination: int | None, transfer_amount: Decimal | None,
) -> tuple[tuple[int, Decimal | None], ...]:
    """None marks an unknown destination credit, never an unknown source debit."""
    if kind == TransactionType.INCOME:
        return ((source, amount),)
    if kind == TransactionType.EXPENSE:
        return ((source, amount.copy_negate()),)
    if kind == TransactionType.TRANSFER:
        debit = (source, amount.copy_negate())
        return (debit,) if destination is None else (debit, (destination, transfer_amount))
    return ()


@dataclass(frozen=True)
class NativeBalance:
    balance: Decimal | None = Decimal("0")
    last_activity: date | None = None

    @property
    def has_monetary_history(self) -> bool:
        return self.last_activity is not None


async def account_balances(session: AsyncSession) -> dict[int, NativeBalance]:
    """Balance and history come from the same transaction snapshot; absent = empty."""
    rows = await session.execute(select(
        Transaction.type, Transaction.amount, Transaction.account_id,
        Transaction.transfer_account_id, Transaction.transfer_amount, Transaction.date,
    ))
    balances: dict[int, NativeBalance] = {}
    for kind, amount, source, destination, transfer_amount, day in rows:
        for account_id, delta in native_legs(kind, amount, source, destination, transfer_amount):
            previous = balances.get(account_id, NativeBalance())
            balance = None if previous.balance is None or delta is None else sum_money((previous.balance, delta))
            balances[account_id] = NativeBalance(balance, max(previous.last_activity or day, day))
    return balances
