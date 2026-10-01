"""Account CRUD, plus each account's live balance — summed from its
Transaction rows (income adds, expense subtracts, transfers debit source amount
and credit destination amount in each account's native currency) rather than
stored, the same "derive it, don't duplicate it" approach
net_worth_service.py uses for Cash.
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.currency import normalize_currency
from app.models.account import Account
from app.schemas.account import AccountCreate, AccountUpdate, AccountWithBalance
from app.schemas.money import FinancialDataIssue
from app.services.currency_history import account_has_history
from app.services.native_balance_service import NativeBalance, account_balances


def _to_read(account: Account, state: NativeBalance) -> AccountWithBalance:
    return AccountWithBalance(
        id=account.id,
        name=account.name,
        type=account.type,
        currency=account.currency,
        color=account.color,
        is_archived=account.is_archived,
        balance=state.balance,
        has_monetary_history=state.has_monetary_history,
        complete=state.balance is not None,
        data_issues=[] if state.balance is not None else [FinancialDataIssue(code="transfer_amount_unresolved")],
    )


async def list_accounts(session: AsyncSession, include_archived: bool) -> list[AccountWithBalance]:
    stmt = select(Account).order_by(Account.name)
    if not include_archived:
        stmt = stmt.where(Account.is_archived.is_(False))
    accounts = (await session.execute(stmt)).scalars().all()
    balances = await account_balances(session)
    return [_to_read(account, balances.get(account.id, NativeBalance())) for account in accounts]


async def create_account(session: AsyncSession, payload: AccountCreate) -> AccountWithBalance:
    account = Account(**payload.model_dump())
    session.add(account)
    await session.commit()
    await session.refresh(account)
    # A brand-new account has no transactions yet — no need to query.
    return _to_read(account, NativeBalance())


async def update_account(session: AsyncSession, account_id: int, payload: AccountUpdate) -> AccountWithBalance:
    # Lock before reading the currency/history; refresh any identity-map copy.
    account = (await session.execute(
        select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    has_history = await account_has_history(session, account_id)
    if payload.currency is not None and payload.currency != normalize_currency(account.currency) and has_history:
        raise HTTPException(status_code=409, detail="currency_change_requires_empty_history")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await session.commit()
    await session.refresh(account)
    balances = await account_balances(session)
    state = balances.get(account.id, NativeBalance())
    return _to_read(account, state)


async def delete_account(session: AsyncSession, account_id: int) -> None:
    account = await session.scalar(
        select(Account).where(Account.id == account_id).with_for_update().execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if await account_has_history(session, account_id):
        raise HTTPException(409, "account_delete_requires_empty_history")
    await session.delete(account)
    await session.commit()
