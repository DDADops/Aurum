"""Conservative transitional guards, removed as readers adopt real FX conversion.

These queries establish whether the existing arithmetic is safe, never resolve
rates or reconstruct transfers. Missing observations use the approved 7-day
policy for metadata only; aggregate integration with the FX resolver is deferred.
"""
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.account import Account
from app.models.asset import Asset, AssetValuation
from app.models.crypto import CryptoHolding
from app.models.enums import AccountType, TransactionType
from app.models.transaction import Transaction, TransactionSplit
from app.schemas.money import FinancialDataIssue, FinancialDataStatus, MissingRate


def merge_statuses(*statuses: FinancialDataStatus) -> FinancialDataStatus:
    rates = {}
    issues = {}
    for status in statuses:
        for rate in status.missing_rates:
            rates[(rate.base_currency, rate.quote_currency, rate.requested_date, rate.reason)] = rate
        for issue in status.data_issues:
            issues[issue.code] = issue
    return FinancialDataStatus(
        complete=all(s.complete for s in statuses) and not rates and not issues,
        missing_rates=list(rates.values()), data_issues=list(issues.values()),
    )


def currency_status(native: str, target: str, requested_date: date) -> FinancialDataStatus:
    if native == target:
        return FinancialDataStatus()
    return FinancialDataStatus(complete=False, missing_rates=[MissingRate(
        base_currency=native, quote_currency=target, requested_date=requested_date,
        reason="absent", max_age_days=7,
    )])


async def transaction_status(
    session: AsyncSession, currency: str, *,
    start_date: date | None = None, end_date: date | None = None,
    transaction_type: TransactionType | None = None,
    category_ids: list[int] | None = None, categorized_only: bool = False,
) -> FinancialDataStatus:
    """Source amounts only: income, expense and transferred-out metrics."""
    stmt = select(Account.currency, Transaction.date).join(
        Transaction, Transaction.account_id == Account.id
    ).distinct()
    if transaction_type is not None:
        stmt = stmt.where(Transaction.type == transaction_type)
    else:
        stmt = stmt.where(Transaction.type != TransactionType.TRANSFER)
    if start_date is not None:
        stmt = stmt.where(Transaction.date >= start_date)
    if end_date is not None:
        stmt = stmt.where(Transaction.date <= end_date)
    if category_ids is not None:
        stmt = stmt.where(or_(
            Transaction.category_id.in_(category_ids),
            Transaction.splits.any(TransactionSplit.category_id.in_(category_ids)),
        ))
    elif categorized_only:
        stmt = stmt.where(or_(
            Transaction.category_id.is_not(None),
            Transaction.splits.any(TransactionSplit.category_id.is_not(None)),
        ))
    rows = (await session.execute(stmt)).all()
    return merge_statuses(*(currency_status(native, currency, day) for native, day in rows))


async def capital_status(
    session: AsyncSession, currency: str, *, account_types: set[AccountType] | None = None,
) -> FinancialDataStatus:
    """Whole-history guard, deliberately conservative until dated valuation.

Used by pre-FX consumers such as Insights. Net Worth now resolves dated FX
independently and does not call this whole-history gate. This is not a balance
or valuation engine.
"""
    today = date.today()
    source, destination = aliased(Account), aliased(Account)
    rows = (await session.execute(
        select(Transaction.type, source.currency, source.type, destination.currency, destination.type, Transaction.transfer_amount)
        .join(source, Transaction.account_id == source.id)
        .outerjoin(destination, Transaction.transfer_account_id == destination.id)
    )).all()
    statuses = []
    for kind, src_currency, src_type, dst_currency, dst_type, transfer_amount in rows:
        source_used = account_types is None or src_type in account_types
        destination_used = dst_type is not None and (account_types is None or dst_type in account_types)
        if source_used:
            statuses.append(currency_status(src_currency, currency, today))
        if kind == TransactionType.TRANSFER:
            if destination_used:
                statuses.append(currency_status(dst_currency, currency, today))
                if transfer_amount is None:
                    statuses.append(FinancialDataStatus(complete=False, data_issues=[
                        FinancialDataIssue(code="transfer_amount_unresolved")]))
            # Missing historical destination has no surviving balance to mark
            # unknown. Transaction-level diagnostics still report it.

    assets = (await session.execute(select(
        Asset.currency, Asset.monthly_cash_flow,
        select(AssetValuation.id).where(AssetValuation.asset_id == Asset.id).exists(),
        select(CryptoHolding.asset_id).where(CryptoHolding.asset_id == Asset.id).exists(),
    ))).all()
    for native, monthly_cash_flow, has_valuation, is_crypto in assets:
        if has_valuation or monthly_cash_flow is not None:
            statuses.append(currency_status(native, currency, today))
        if not has_valuation:
            statuses.append(FinancialDataStatus(complete=False, data_issues=[
                FinancialDataIssue(code="valuation_missing")]))
        if is_crypto:
            # Neither the settings history nor the old valuation quote was stored.
            statuses.append(FinancialDataStatus(complete=False, data_issues=[
                FinancialDataIssue(code="valuation_currency_unconfirmed")]))
    return merge_statuses(*statuses)
