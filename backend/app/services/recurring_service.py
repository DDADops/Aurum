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
from app.schemas.transaction import TransactionCreate, transfer_rule_violation
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
        .execution_options(populate_existing=True)
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


_MONETARY_FIELDS = frozenset({"amount", "account_id", "type", "transfer_account_id", "transfer_amount"})
_SNAPSHOT_FIELDS = frozenset({"amount_currency", "transfer_amount_currency"})


async def _lock_instruction(session: AsyncSession, recurring_id: int, requested_ids=()):
    discovered = await _get_or_404(session, recurring_id)
    references = (discovered.account_id, discovered.transfer_account_id)
    version = (discovered.last_posted_date, discovered.updated_at)
    accounts = await lock_accounts(session, (*references, *requested_ids))
    # Account DELETE can cascade/set-null templates after acquiring Accounts.
    # Never wait for an inverse template-first editor while holding Accounts.
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
    return recurring, accounts, version


def _snapshot_instruction(values: dict, supplied: dict, accounts: dict[int, Account]) -> None:
    """Validate a confirmed final monetary instruction using locked account rows."""
    if values["type"] != TransactionType.TRANSFER:
        if any(supplied.get(field) is not None for field in (
            "transfer_account_id", "transfer_amount", "transfer_amount_currency",
        )):
            raise HTTPException(400, "Destination fields are only valid for transfers")
        values.update(transfer_account_id=None, transfer_amount=None, transfer_amount_currency=None)
    else:
        # Old same-currency or differently-denominated amounts are not an
        # explicit new cross-currency instruction. Every monetary cross PATCH
        # must supply its authoritative destination amount again.
        values["transfer_amount"] = supplied.get("transfer_amount")
    normalize_transfer(values, accounts)
    try:
        TransactionCreate.model_validate({**values, "date": date_.today()})
    except ValidationError as exc:
        raise HTTPException(422, "recurring_invalid_monetary_instruction") from exc
    values["amount_currency"] = normalize_currency(accounts[values["account_id"]].currency)
    values["transfer_amount_currency"] = (
        normalize_currency(accounts[values["transfer_account_id"]].currency)
        if values["type"] == TransactionType.TRANSFER else None
    )
    # Retain old optional inputs only as assertions, never snapshot authority.
    for field in _SNAPSHOT_FIELDS:
        if field in supplied and supplied[field] != values[field]:
            raise HTTPException(422, "recurring_snapshot_must_match_account")


async def create_recurring(session: AsyncSession, payload: RecurringTransactionCreate) -> RecurringTransactionRead:
    values = payload.model_dump()
    accounts = await lock_accounts(session, (payload.account_id, payload.transfer_account_id))
    _snapshot_instruction(values, payload.model_dump(exclude_unset=True), accounts)
    await _ensure_category_matches_type(session, payload.category_id, payload.type)
    recurring = RecurringTransaction(**values)
    session.add(recurring)
    await session.commit()
    return _to_read(await _get_or_404(session, recurring.id))


async def update_recurring(
    session: AsyncSession, recurring_id: int, payload: RecurringTransactionUpdate,
) -> RecurringTransactionRead:
    updates = payload.model_dump(exclude_unset=True)
    recurring, accounts, _ = await _lock_instruction(
        session, recurring_id, (updates.get("account_id"), updates.get("transfer_account_id")),
    )
    monetary = bool(_MONETARY_FIELDS & payload.model_fields_set)
    if not monetary and _SNAPSHOT_FIELDS & payload.model_fields_set:
        raise HTTPException(422, "recurring_monetary_reentry_required")
    values = {field: getattr(recurring, field) for field in RecurringTransactionCreate.model_fields}
    values.update(updates)
    if monetary:
        _snapshot_instruction(values, updates, accounts)
    elif "category_id" in updates:
        violation = transfer_rule_violation(
            type=values["type"], account_id=values["account_id"],
            transfer_account_id=values["transfer_account_id"], category_id=values["category_id"],
        )
        if violation:
            raise HTTPException(400, violation)
    await _ensure_category_matches_type(session, values["category_id"], values["type"])
    for field, value in values.items():
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
    recurring, accounts, version = await _lock_instruction(session, recurring_id)
    if (recurring.last_posted_date, recurring.updated_at) != version:
        raise HTTPException(409, "recurring_instruction_changed_retry")
    source = accounts.get(recurring.account_id)
    destination = accounts.get(recurring.transfer_account_id)
    if source is None or recurring.amount_currency != normalize_currency(source.currency):
        raise HTTPException(409, "recurring_currency_mismatch")
    if recurring.type == TransactionType.TRANSFER:
        if destination is None:
            raise HTTPException(409, "recurring_currency_revalidation_required")
        if recurring.transfer_amount_currency != normalize_currency(destination.currency):
            raise HTTPException(409, "recurring_currency_mismatch")
        if recurring.transfer_amount is None:
            raise HTTPException(409, "recurring_currency_revalidation_required")
    elif recurring.transfer_amount_currency is not None:
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
    try:
        normalize_transfer(fields, accounts)
        await _ensure_category_matches_type(session, payload.category_id, payload.type)
    except HTTPException as exc:
        raise HTTPException(409, "recurring_currency_revalidation_required") from exc
    session.add(Transaction(**fields))
    recurring.last_posted_date = today
    await session.commit()
    return _to_read(await _get_or_404(session, recurring_id))
