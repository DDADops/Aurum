"""Aggregation logic behind the Overview dashboard."""
import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TransactionType
from app.models.transaction import Transaction
from app.schemas.dashboard import CategoryBreakdownChildItem, CategoryBreakdownItem, DashboardSummary
from app.services.category_rollup import rollup_spending_by_top_level_category
from app.services.settings_service import get_or_create_app_settings
from app.services.financial_data_service import transaction_status, merge_statuses

# Categorical slots are capped at 8 (dataviz skill: a 9th series folds into "Other",
# never a generated hue) — this is also the exact size of the default category set.
MAX_CHART_SLICES = 8
OTHER_SLICE_COLOR = "#898781"  # muted ink, reserved for the non-categorical rollup


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


async def get_dashboard_summary(session: AsyncSession, year: int, month: int) -> DashboardSummary:
    start, end = _month_bounds(year, month)
    currency = (await get_or_create_app_settings(session)).currency
    statuses = {}
    for metric, kind in (("real_income", TransactionType.INCOME), ("spent", TransactionType.EXPENSE),
                         ("transferred_out", TransactionType.TRANSFER)):
        statuses[metric] = await transaction_status(
            session, currency, start_date=start, end_date=end, transaction_type=kind)
    statuses["net"] = merge_statuses(statuses["real_income"], statuses["spent"])
    statuses["spending_by_category"] = statuses["spent"]
    status = merge_statuses(*statuses.values())

    totals_stmt = (
        select(Transaction.type, func.coalesce(func.sum(Transaction.amount), 0))
        .where(Transaction.date >= start, Transaction.date <= end)
        .group_by(Transaction.type)
    )
    totals_result = await session.execute(totals_stmt)
    totals: dict[TransactionType, Decimal] = {row[0]: row[1] for row in totals_result.all()}

    real_income = totals.get(TransactionType.INCOME, Decimal("0"))
    spent = totals.get(TransactionType.EXPENSE, Decimal("0"))
    transferred_out = totals.get(TransactionType.TRANSFER, Decimal("0"))

    # A subcategory's spending rolls up into its parent's slice, and a split
    # transaction's category_id=NULL means its category lives on its split
    # lines instead — rollup_spending_by_top_level_category handles both
    # the same way a plain transaction's category already was.
    rows = await rollup_spending_by_top_level_category(
        session, transaction_type=TransactionType.EXPENSE, start_date=start, end_date=end
    )

    top_rows, rest_rows = rows[:MAX_CHART_SLICES], rows[MAX_CHART_SLICES:]

    def _percent(amount: Decimal) -> float:
        return float(amount / spent * 100) if spent else 0.0

    spending_by_category = [
        CategoryBreakdownItem(
            category_id=row.category_id, name=row.name, color=row.color, icon=row.icon,
            amount=row.amount, percent=_percent(row.amount),
            children=[
                CategoryBreakdownChildItem(
                    category_id=child.category_id, name=child.name, color=child.color, icon=child.icon,
                    amount=child.amount,
                )
                for child in row.children
            ],
        )
        for row in top_rows
    ]

    if rest_rows:
        other_amount = sum((row.amount for row in rest_rows), Decimal("0"))
        spending_by_category.append(
            CategoryBreakdownItem(
                category_id=None, name="Other", color=OTHER_SLICE_COLOR, icon="more-horizontal",
                amount=other_amount, percent=_percent(other_amount),
            )
        )

    return DashboardSummary(
        currency=currency, metric_status=statuses, **status.model_dump(),
        year=year,
        month=month,
        real_income=real_income if statuses["real_income"].complete else None,
        spent=spent if statuses["spent"].complete else None,
        net=real_income - spent if statuses["net"].complete else None,
        transferred_out=transferred_out if statuses["transferred_out"].complete else None,
        spending_by_category=spending_by_category if statuses["spent"].complete else [],
    )
