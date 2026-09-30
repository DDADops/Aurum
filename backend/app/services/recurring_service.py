"""Recurring transaction templates: CRUD, plus "post now" — a one-click
action that creates a real Transaction from the template. Deliberately not a
background job (see also insights_service.py's docstring on scope): the
schedule only advances when the user actually clicks Post, so a missed
week never silently back-fills a pile of transactions.
"""
import calendar
from datetime import date as date_
from datetime import timedelta

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import selectinload

from app.core.currency import normalize_currency
from app.models.account import Account
from app.models.category import Category
from app.models.enums import CategoryKind, RecurringFrequency, TransactionType
from app.models.recurring import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring import RecurringTransactionCreate, RecurringTransactionRead, RecurringTransactionUpdate
from app.schemas.transaction import TransactionCreate
from app.services.transaction_service import lock_accounts, normalize_transfer

_EAGER = (
    selectinload(RecurringTransaction.account),
    selectinload(RecurringTransaction.category),
    selectinload(RecurringTransaction.transfer_account),
)

_TYPE_TO_CATEGORY_KIND = {
    TransactionType.INCOME: CategoryKind.INCOME,
    TransactionType.EXPENSE: CategoryKind.EXPENSE,
}


async def _ensure_category_matches_type(
    session: AsyncSession, category_id: int | None, transaction_type: TransactionType
) -> None:
    if category_id is None:
        return
    expected_kind = _TYPE_TO_CATEGORY_KIND.get(transaction_type)
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=400, detail="Category not found")
    if expected_kind is not None and category.kind != expected_kind:
        raise HTTPException(
            status_code=400,
            detail=f"Category '{category.name}' is a {category.kind.value} category and cannot be used for a {transaction_type.value} recurring transaction",
        )


def _advance(day: date_, frequency: RecurringFrequency) -> date_:
    if frequency == RecurringFrequency.WEEKLY:
        return day + timedelta(days=7)
    if frequency == RecurringFrequency.MONTHLY:
        year = day.year + (day.month // 12)
        month = day.month % 12 + 1
        clamped_day = min(day.day, calendar.monthrange(year, month)[1])
        return date_(year, month, clamped_day)
    # YEARLY — Feb 29 anchors fall back to Feb 28 in non-leap years.
    try:
        return day.replace(year=day.year + 1)
    except ValueError:
        return day.replace(year=day.year + 1, day=28)


def _next_due_date(recurring: RecurringTransaction) -> date_:
    if recurring.last_posted_date is None:
        return recurring.anchor_date
    return _advance(recurring.last_posted_date, recurring.frequency)


def _to_read(recurring: RecurringTransaction) -> RecurringTransactionRead:
    next_due = _next_due_date(recurring)
    today = date_.today()
    return RecurringTransactionRead(
        id=recurring.id,
        account_id=recurring.account_id,
        account_name=recurring.account.name,
        category_id=recurring.category_id,
        category_name=recurring.category.name if recurring.category else None,
        category_color=recurring.category.color if recurring.category else None,
        category_icon=recurring.category.icon if recurring.category else None,
        transfer_account_id=recurring.transfer_account_id,
        transfer_account_name=recurring.transfer_account.name if recurring.transfer_account else None,
        type=recurring.type,
        amount=recurring.amount,
        amount_currency=recurring.amount_currency,
        transfer_amount=recurring.transfer_amount,
        transfer_amount_currency=recurring.transfer_amount_currency,
        description=recurring.description,
        merchant=recurring.merchant,
        notes=recurring.notes,
        frequency=recurring.frequency,
        anchor_date=recurring.anchor_date,
        last_posted_date=recurring.last_posted_date,
        is_active=recurring.is_active,
        next_due_date=next_due,
        is_due=recurring.is_active and next_due <= today,
        days_until_due=(next_due - today).days,
    )


async def _get_or_404(session: AsyncSession, recurring_id: int) -> RecurringTransaction:
    result = await session.execute(
        select(RecurringTransaction).options(*_EAGER).where(RecurringTransaction.id == recurring_id)
    )
    recurring = result.scalar_one_or_none()
    if recurring is None:
        raise HTTPException(status_code=404, detail="Recurring transaction not found")
    return recurring


async def list_recurring(session: AsyncSession) -> list[RecurringTransactionRead]:
    result = await session.execute(
        select(RecurringTransaction).options(*_EAGER).order_by(RecurringTransaction.id)
    )
    return [_to_read(row) for row in result.scalars().all()]


async def create_recurring(session: AsyncSession, payload: RecurringTransactionCreate) -> RecurringTransactionRead:
    await _ensure_category_matches_type(session, payload.category_id, payload.type)
    source = await session.get(Account, payload.account_id)
    if source is None:
        raise HTTPException(400, "Account not found")
    values = payload.model_dump()
    values["amount_currency"] = payload.amount_currency or normalize_currency(source.currency)
    if payload.type != TransactionType.TRANSFER:
        if payload.transfer_amount is not None or payload.transfer_amount_currency is not None:
            raise HTTPException(422, "Destination amounts are only valid for transfers")
    else:
        destination = await session.get(Account, payload.transfer_account_id) if payload.transfer_account_id else None
        if destination is not None:
            values["transfer_amount_currency"] = payload.transfer_amount_currency or normalize_currency(destination.currency)
            if payload.transfer_amount is None and values["amount_currency"] == values["transfer_amount_currency"]:
                values["transfer_amount"] = payload.amount
        if values["transfer_amount"] is not None and values["transfer_amount_currency"] is None:
            raise HTTPException(422, "Destination amount requires its instruction currency")
    recurring = RecurringTransaction(**values)
    session.add(recurring)
    await session.commit()
    return _to_read(await _get_or_404(session, recurring.id))


async def update_recurring(
    session: AsyncSession, recurring_id: int, payload: RecurringTransactionUpdate
) -> RecurringTransactionRead:
    recurring = await _get_or_404(session, recurring_id)
    updates = payload.model_dump(exclude_unset=True)
    effective_type = updates.get("type", recurring.type)
    effective_category_id = updates.get("category_id", recurring.category_id)
    await _ensure_category_matches_type(session, effective_category_id, effective_type)
    destination_amount = updates.get("transfer_amount", recurring.transfer_amount)
    destination_currency = updates.get("transfer_amount_currency", recurring.transfer_amount_currency)
    if effective_type != TransactionType.TRANSFER:
        if updates.get("transfer_amount") is not None or updates.get("transfer_amount_currency") is not None:
            raise HTTPException(422, "Destination amounts are only valid for transfers")
        updates.update(transfer_amount=None, transfer_amount_currency=None)
    elif destination_amount is not None and destination_currency is None:
        raise HTTPException(422, "Destination amount requires its instruction currency")
    # Existing snapshots remain authored instructions, even after account edits.
    for field, value in updates.items():
        setattr(recurring, field, value)
    await session.commit()
    return _to_read(await _get_or_404(session, recurring_id))


async def delete_recurring(session: AsyncSession, recurring_id: int) -> None:
    recurring = await session.get(RecurringTransaction, recurring_id)
    if recurring is None:
        raise HTTPException(status_code=404, detail="Recurring transaction not found")
    await session.delete(recurring)
    await session.commit()


async def post_recurring(session: AsyncSession, recurring_id: int) -> RecurringTransactionRead:
    """Creates a real Transaction from the template, dated today, and moves
    last_posted_date forward — the only thing that advances the schedule."""
    recurring = await _get_or_404(session, recurring_id)
    references = (recurring.account_id, recurring.transfer_account_id)
    accounts = await lock_accounts(session, references)
    # Older template-edit paths can hold the template before touching a new
    # account FK. Do not wait on that inverse lock order while holding Accounts.
    try:
        recurring = await session.scalar(
            select(RecurringTransaction).where(RecurringTransaction.id == recurring_id).options(*_EAGER)
            .with_for_update(nowait=True).execution_options(populate_existing=True)
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise HTTPException(409, "recurring_instruction_changed_retry") from exc
        raise
    if recurring is None:
        raise HTTPException(404, "Recurring transaction not found")
    if (recurring.account_id, recurring.transfer_account_id) != references:
        raise HTTPException(409, "recurring_accounts_changed_retry")
    # Retain the transitional refusal; full recurring revalidation is later.
    # The remaining supported posting path now joins the Account lock protocol.
    if recurring.amount_currency != normalize_currency(recurring.account.currency):
        raise HTTPException(409, "recurring_currency_revalidation_required")
    if recurring.type == TransactionType.TRANSFER and (
        recurring.transfer_account is None
        or recurring.transfer_amount_currency != normalize_currency(recurring.transfer_account.currency)
        or recurring.amount_currency != recurring.transfer_amount_currency
        or recurring.transfer_amount != recurring.amount
    ):
        raise HTTPException(409, "recurring_currency_revalidation_required")
    today = date_.today()

    try:
        payload = TransactionCreate(
            account_id=recurring.account_id, category_id=recurring.category_id,
            transfer_account_id=recurring.transfer_account_id, transfer_amount=recurring.transfer_amount,
            type=recurring.type, amount=recurring.amount, description=recurring.description,
            merchant=recurring.merchant, notes=recurring.notes, date=today,
        )
    except ValidationError as exc:
        raise HTTPException(409, "recurring_currency_revalidation_required") from exc
    fields = payload.model_dump(exclude={"tag_ids", "splits"})
    normalize_transfer(fields, accounts)
    await _ensure_category_matches_type(session, payload.category_id, payload.type)
    session.add(Transaction(**fields))
    recurring.last_posted_date = today
    await session.commit()
    return _to_read(await _get_or_404(session, recurring_id))
