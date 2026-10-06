"""Versioned, validated JSON snapshots. No provider calls or monetary repair."""
import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import Enum, Integer, String, delete, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import APP_VERSION
from app.core.money import sum_money
from app.models.account import Account
from app.models.asset import Asset, AssetValuation
from app.models.budget import Budget
from app.models.category import Category
from app.models.crypto import CryptoHolding, CryptoPortfolio, CryptoSyncState, CryptoTransaction
from app.models.enums import TransactionType
from app.models.fx import FxRate
from app.models.goal import Goal, GoalContribution
from app.models.recurring import RecurringTransaction
from app.models.settings import AppSettings
from app.models.tag import Tag, transaction_tags
from app.models.transaction import Transaction, TransactionSplit
from app.schemas.backup import (
    BACKUP_ROWS, AppSettingsBackup, BackupPayload, CryptoSyncStateBackup,
    RecurringTransactionBackup, TransactionBackup,
)
from app.schemas.transaction import split_rule_violation

logger = logging.getLogger(__name__)
BACKUP_FORMAT_VERSION = 2

# Explicit parent-before-child ordering. Association rows are inserted last.
MODELS = (
    Account, Category, Tag, Asset, CryptoPortfolio, Goal, Transaction,
    TransactionSplit, AssetValuation, CryptoHolding, CryptoTransaction,
    Budget, GoalContribution, RecurringTransaction, FxRate,
)
LOCK_TABLES = sorted([model.__tablename__ for model in MODELS] +
                     ["transaction_tags", "app_settings", "crypto_sync_state"])


async def _lock_dataset(session: AsyncSession, *, export: bool = False) -> None:
    if session.get_bind().dialect.name == "postgresql":
        mode = "SHARE" if export else "ACCESS EXCLUSIVE"
        await session.execute(text(f"LOCK TABLE {', '.join(LOCK_TABLES)} IN {mode} MODE"))


async def build_backup(session: AsyncSession) -> BackupPayload:
    # A consistent whole-dataset read, even while financial writers run.
    await _lock_dataset(session, export=True)
    values = {}
    for model in MODELS:
        key = model.__tablename__
        stmt = select(model).order_by(*model.__table__.primary_key.columns)
        if model is Transaction:
            stmt = stmt.options(selectinload(Transaction.tags))
        rows = (await session.scalars(stmt)).all()
        values[key] = [BACKUP_ROWS[key].model_validate(row) for row in rows]
        if model is Transaction:
            for original, backup in zip(rows, values[key]):
                backup.tag_ids = sorted(tag.id for tag in original.tags)
    settings = await session.get(AppSettings, 1)
    sync = await session.get(CryptoSyncState, 1)
    return BackupPayload(
        aurum_backup_version=BACKUP_FORMAT_VERSION, exported_at=datetime.now(timezone.utc), app_version=APP_VERSION,
        app_settings=AppSettingsBackup.model_validate(settings) if settings else AppSettingsBackup(currency="USD"),
        crypto_sync_state=CryptoSyncStateBackup.model_validate(sync) if sync else None,
        **values,
    )


def _validate_references(payload: BackupPayload) -> None:
    account_ids = {row.id for row in payload.accounts}
    category_ids = {row.id for row in payload.categories}
    asset_ids = {row.id for row in payload.assets}
    tag_ids = {row.id for row in payload.tags}

    for c in payload.categories:
        if c.parent_id is not None and c.parent_id not in category_ids:
            raise HTTPException(400, f"Category {c.id} references unknown parent_id {c.parent_id}")

    for t in payload.transactions:
        if t.account_id not in account_ids:
            raise HTTPException(400, f"Transaction {t.id} references unknown account_id {t.account_id}")
        if t.transfer_account_id is not None and t.transfer_account_id not in account_ids:
            raise HTTPException(
                400, f"Transaction {t.id} references unknown transfer_account_id {t.transfer_account_id}"
            )
        if t.category_id is not None and t.category_id not in category_ids:
            raise HTTPException(400, f"Transaction {t.id} references unknown category_id {t.category_id}")
        for tag_id in t.tag_ids:
            if tag_id not in tag_ids:
                raise HTTPException(400, f"Transaction {t.id} references unknown tag_id {tag_id}")

    transaction_ids = {row.id for row in payload.transactions}
    for s in payload.transaction_splits:
        if s.transaction_id not in transaction_ids:
            raise HTTPException(400, f"Transaction split {s.id} references unknown transaction_id {s.transaction_id}")
        if s.category_id is not None and s.category_id not in category_ids:
            raise HTTPException(400, f"Transaction split {s.id} references unknown category_id {s.category_id}")

    for v in payload.asset_valuations:
        if v.asset_id not in asset_ids:
            raise HTTPException(400, f"Asset valuation {v.id} references unknown asset_id {v.asset_id}")

    crypto_portfolio_ids = {p.id for p in payload.crypto_portfolios}
    crypto_holding_asset_ids = {h.asset_id for h in payload.crypto_holdings}
    for h in payload.crypto_holdings:
        if h.asset_id not in asset_ids:
            raise HTTPException(400, f"Crypto holding {h.asset_id} references unknown asset_id {h.asset_id}")
        # Historical NULL portfolios are resolved in pure v1 normalization.
        # v2 NULL is rejected by the column nullability check below.
        if h.portfolio_id is not None and h.portfolio_id not in crypto_portfolio_ids:
            raise HTTPException(
                400, f"Crypto holding {h.asset_id} references unknown portfolio_id {h.portfolio_id}"
            )

    for tx in payload.crypto_transactions:
        if tx.asset_id not in crypto_holding_asset_ids:
            raise HTTPException(400, f"Crypto transaction {tx.id} references unknown asset_id {tx.asset_id}")

    for b in payload.budgets:
        if b.category_id not in category_ids:
            raise HTTPException(400, f"Budget {b.id} references unknown category_id {b.category_id}")

    goal_ids = {row.id for row in payload.goals}
    for c in payload.goal_contributions:
        if c.goal_id not in goal_ids:
            raise HTTPException(400, f"Goal contribution {c.id} references unknown goal_id {c.goal_id}")

    for r in payload.recurring_transactions:
        if r.account_id not in account_ids:
            raise HTTPException(400, f"Recurring transaction {r.id} references unknown account_id {r.account_id}")
        if r.transfer_account_id is not None and r.transfer_account_id not in account_ids:
            raise HTTPException(
                400,
                f"Recurring transaction {r.id} references unknown transfer_account_id {r.transfer_account_id}",
            )
        if r.category_id is not None and r.category_id not in category_ids:
            raise HTTPException(400, f"Recurring transaction {r.id} references unknown category_id {r.category_id}")


def _invalid(message: str) -> None:
    raise HTTPException(400, message)


def _unique(rows, keys: tuple[str, ...], label: str) -> None:
    seen = set()
    for row in rows:
        key = tuple(getattr(row, field) for field in keys)
        if key in seen:
            _invalid(f"Duplicate {label}")
        seen.add(key)


def _validate_backup(payload: BackupPayload) -> None:
    """Pure checks: all representable constraints precede locks/deletion."""
    if payload.aurum_backup_version != 2:
        _invalid("Backup must be normalized to version 2")
    _validate_references(payload)
    for model in MODELS:
        rows = getattr(payload, model.__tablename__)
        _unique(rows, tuple(c.name for c in model.__table__.primary_key), model.__tablename__ + " primary key")
        for row in rows:
            for column in model.__table__.columns:
                if column.name in ("created_at", "updated_at"):
                    continue  # Existing backup contract excludes volatile ORM timestamps.
                value = getattr(row, column.name)
                if value is None and not column.nullable:
                    _invalid(f"{model.__tablename__}.{column.name} cannot be NULL")
                if isinstance(column.type, Integer) and value is not None and not -2147483648 <= value <= 2147483647:
                    _invalid(f"{model.__tablename__}.{column.name} exceeds integer range")
                if column.primary_key and (type(value) is not int or not 0 < value < 2147483647):
                    _invalid("IDs must be positive PostgreSQL integers with sequence headroom")
                if isinstance(column.type, String) and not isinstance(column.type, Enum) and value is not None:
                    if column.name == "color" and re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
                        _invalid("Invalid color")
                    if ((column.name == "name" and model is not CryptoHolding)
                            or column.name == "description") and not value:
                        _invalid(f"{model.__tablename__}.{column.name} cannot be empty")
                    if column.type.length and len(value) > column.type.length:
                        _invalid(f"{model.__tablename__}.{column.name} exceeds stored length")
    _unique(payload.tags, ("name",), "tag name")
    _unique(payload.budgets, ("category_id",), "budget category")
    _unique(payload.asset_valuations, ("asset_id", "as_of_date"), "valuation day")
    _unique(payload.fx_rates, ("base_currency", "quote_currency", "as_of_date"), "FX pair/day")
    categories = {row.id: row for row in payload.categories}
    for row in payload.categories:
        if row.parent_id is not None:
            parent = categories[row.parent_id]
            if parent.parent_id is not None or parent.id == row.id or parent.kind != row.kind:
                _invalid("Invalid category hierarchy")
    accounts = {row.id: row for row in payload.accounts}
    for row in [*payload.transactions, *payload.recurring_transactions]:
        if row.amount <= 0 or (row.transfer_amount is not None and row.transfer_amount <= 0):
            _invalid("Transaction amounts must be positive")
        if row.type == TransactionType.TRANSFER:
            if row.category_id is not None or row.account_id == row.transfer_account_id:
                _invalid("Invalid transfer category/destination")
            # NULL destination/amount is a legitimate readable legacy state.
            # Recurring snapshots may intentionally differ from current Accounts.
            if isinstance(row, TransactionBackup) and row.transfer_account_id is not None:
                if (accounts[row.account_id].currency == accounts[row.transfer_account_id].currency
                        and row.transfer_amount is not None and row.transfer_amount != row.amount):
                    _invalid("Same-currency transfer amounts must agree")
        elif row.transfer_account_id is not None or row.transfer_amount is not None:
            _invalid("Non-transfer cannot have destination fields")
        if isinstance(row, RecurringTransactionBackup):
            if row.type != TransactionType.TRANSFER and row.transfer_amount_currency is not None:
                _invalid("Non-transfer cannot have destination currency")
            if (row.transfer_amount is not None and row.amount_currency == row.transfer_amount_currency
                    and row.amount != row.transfer_amount):
                _invalid("Same-currency recurring amounts must agree")
    splits = {}
    for row in payload.transaction_splits:
        if row.amount <= 0:
            _invalid("Split amount must be positive")
        splits.setdefault(row.transaction_id, []).append(row)
    for row in payload.transactions:
        parts = splits.get(row.id, [])
        violation = split_rule_violation(type=row.type, amount=row.amount, category_id=row.category_id,
                                         split_count=len(parts), split_total=sum_money(p.amount for p in parts))
        if violation:
            _invalid(violation)
        if len(row.tag_ids) != len(set(row.tag_ids)):
            _invalid("Duplicate transaction tag")
    for row in payload.budgets:
        if row.monthly_limit <= 0 or categories[row.category_id].kind.value != "expense":
            _invalid("Budget needs positive limit and expense category")
    if any(row.target_amount <= 0 for row in payload.goals):
        _invalid("Goal target must be positive")
    if any(row.amount == 0 for row in payload.goal_contributions):
        _invalid("Goal contribution cannot be zero")
    for row in payload.asset_valuations:
        if row.value < 0 or (row.currency_confirmed and row.currency is None):
            _invalid("Invalid valuation value/provenance")
    for row in payload.crypto_transactions:
        if row.quantity <= 0 or row.price_per_unit <= 0:
            _invalid("Crypto quantity and price must be positive")
    for row in payload.crypto_holdings:
        provenance = (row.last_price_currency, row.last_price_as_of_date)
        if (row.last_price is not None and row.last_price < 0
                or (any(v is not None for v in provenance) and
                    (row.last_price is None or any(v is None for v in provenance)))):
            _invalid("Invalid crypto price cache tuple")


async def _reset_sequence(session: AsyncSession, model) -> None:
    if session.get_bind().dialect.name != "postgresql" or "id" not in model.__table__.primary_key:
        return
    table = model.__tablename__
    sequence = await session.scalar(text("SELECT pg_get_serial_sequence(:table, 'id')"), {"table": table})
    if sequence:
        next_id = await session.scalar(text(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}"))
        # Unlike setval, RESTART is transactional and rolls back on restore failure.
        quote = session.get_bind().dialect.identifier_preparer.quote
        identifier = ".".join(quote(part) for part in sequence.split("."))
        await session.execute(text(f"ALTER SEQUENCE {identifier} RESTART WITH {int(next_id)}"))


async def restore_backup(session: AsyncSession, payload: BackupPayload) -> None:
    _validate_backup(payload)
    try:
        await _lock_dataset(session)
        await session.execute(delete(transaction_tags))
        for model in reversed(MODELS):
            await session.execute(delete(model))
        await session.execute(delete(CryptoSyncState))
        await session.execute(delete(AppSettings))
        for model in MODELS:
            rows = getattr(payload, model.__tablename__)
            if model is Category:
                rows = sorted(rows, key=lambda row: row.parent_id is not None)
            # Individual Core inserts preserve ordering, explicit NULLs and IDs.
            # They do not invoke reconciliation, FX or writer-side snapshots.
            for row in rows:
                values = row.model_dump(exclude={"tag_ids"})
                await session.execute(insert(model.__table__).values(**values))
        for row in payload.transactions:
            for tag_id in row.tag_ids:
                await session.execute(insert(transaction_tags).values(transaction_id=row.id, tag_id=tag_id))
        await session.execute(insert(AppSettings.__table__).values(**payload.app_settings.model_dump()))
        if payload.crypto_sync_state is not None:
            await session.execute(insert(CryptoSyncState.__table__).values(**payload.crypto_sync_state.model_dump()))
        for model in (*MODELS, AppSettings, CryptoSyncState):
            await _reset_sequence(session, model)
        await session.commit()
        session.expire_all()
    except Exception as exc:
        await session.rollback()
        logger.exception("Backup restore failed")
        raise HTTPException(400, "Restore failed, no changes were made") from exc
