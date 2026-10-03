"""Monthly category budgets: CRUD for the limits themselves, plus
get_budget_status(), which compares each limit against actual spend for a
given month — the data behind the Budget page's progress bars and the
budget_exceeded proactive alert (services/insights_service.py)."""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN, localcontext

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.account import Account
from app.models.budget import Budget
from app.models.category import Category
from app.models.enums import CategoryKind, TransactionType
from app.models.transaction import Transaction, TransactionSplit
from app.schemas.budget import BudgetCreate, BudgetStatus, BudgetStatusResponse, BudgetUpdate
from app.schemas.money import FinancialDataStatus, MissingRate
from app.services.financial_data_service import merge_statuses
from app.services.fx_service import ResolvedRate, convert, resolve_rate
from app.services.settings_service import effective_default_currency, get_or_create_app_settings

_EAGER = (selectinload(Budget.category),)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


async def list_budgets(session: AsyncSession) -> list[Budget]:
    result = await session.execute(select(Budget).options(*_EAGER).join(Category).order_by(Category.sort_order))
    return list(result.scalars().all())


async def create_budget(session: AsyncSession, payload: BudgetCreate) -> Budget:
    category = await session.get(Category, payload.category_id)
    if category is None:
        raise HTTPException(status_code=400, detail="Category not found")
    if category.kind != CategoryKind.EXPENSE:
        raise HTTPException(status_code=400, detail="Budgets can only be set on expense categories")

    existing = await session.execute(select(Budget).where(Budget.category_id == payload.category_id))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail=f"'{category.name}' already has a budget")

    budget = Budget(category_id=payload.category_id, monthly_limit=payload.monthly_limit,
                    currency=payload.currency or await effective_default_currency(session))
    session.add(budget)
    await session.commit()
    refreshed = await session.execute(select(Budget).options(*_EAGER).where(Budget.id == budget.id))
    return refreshed.scalar_one()


async def update_budget(session: AsyncSession, budget_id: int, payload: BudgetUpdate) -> Budget:
    budget = await session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    if payload.currency is not None:
        budget.currency = payload.currency
    budget.monthly_limit = payload.monthly_limit
    await session.commit()
    refreshed = await session.execute(select(Budget).options(*_EAGER).where(Budget.id == budget_id))
    return refreshed.scalar_one()


async def delete_budget(session: AsyncSession, budget_id: int) -> None:
    budget = await session.get(Budget, budget_id)
    if budget is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    await session.delete(budget)
    await session.commit()


class _DatedValues:
    """One request cache shared by all budgets, including their target currencies."""

    def __init__(self, session: AsyncSession, max_age_days: int):
        self.session, self.max_age_days = session, max_age_days
        self.rates: dict[tuple[str, str, date, int], ResolvedRate | MissingRate] = {}

    async def value(
        self, amount: Decimal, source: str, target: str, day: date,
    ) -> tuple[Decimal | None, FinancialDataStatus]:
        if amount == 0:
            return Decimal("0"), FinancialDataStatus()
        key = (source, target, day, self.max_age_days)
        if key not in self.rates:
            self.rates[key] = await resolve_rate(self.session, *key)
        rate = self.rates[key]
        if isinstance(rate, MissingRate):
            return None, FinancialDataStatus(complete=False, missing_rates=[rate])
        return convert(amount, rate), FinancialDataStatus()


async def get_budget_status(session: AsyncSession, year: int, month: int) -> BudgetStatusResponse:
    with localcontext() as context:
        context.prec = 96
        context.rounding = ROUND_HALF_EVEN
        return await _dated_budget_status(session, year, month)


async def _dated_budget_status(session: AsyncSession, year: int, month: int) -> BudgetStatusResponse:
    start, end = _month_bounds(year, month)
    budgets = await list_budgets(session)
    if not budgets:
        return BudgetStatusResponse(year=year, month=month, items=[])
    settings = await get_or_create_app_settings(session)
    fx = _DatedValues(session, settings.fx_max_age_days)

    children_by_parent: dict[int, set[int]] = defaultdict(set)
    for child_id, parent_id in (await session.execute(
        select(Category.id, Category.parent_id).where(Category.parent_id.in_([b.category_id for b in budgets]))
    )).all():
        children_by_parent[parent_id].add(child_id)
    categories_by_budget = {b.id: {b.category_id} | children_by_parent[b.category_id] for b in budgets}
    counted_ids = set().union(*categories_by_budget.values())
    transactions = (await session.execute(
        select(Transaction, Account.currency).join(Account, Account.id == Transaction.account_id).where(
            Transaction.type == TransactionType.EXPENSE,
            Transaction.date >= start, Transaction.date <= end,
            or_(Transaction.category_id.in_(counted_ids),
                Transaction.splits.any(TransactionSplit.category_id.in_(counted_ids))),
        ).options(selectinload(Transaction.splits)).order_by(Transaction.date, Transaction.id)
    )).all()

    items = []
    for budget in budgets:
        category_ids = categories_by_budget[budget.id]
        spent = Decimal("0")
        inputs = []
        for transaction, native in transactions:
            selected = (
                any(part.category_id in category_ids for part in transaction.splits)
                if transaction.splits else transaction.category_id in category_ids
            )
            if not selected:
                continue
            value, status = await fx.value(transaction.amount, native, budget.currency, transaction.date)
            inputs.append(status)
            if value is None:
                continue  # Suppress the entire dependent metric below, not just this row.
            if transaction.splits:
                allocated = Decimal("0")
                for index, part in enumerate(transaction.splits):
                    if index == len(transaction.splits) - 1:
                        # Reconcile all stored splits before category selection,
                        # including deleted categories, as in Dashboard/Reports.
                        amount = value - allocated
                    else:
                        amount, _ = await fx.value(part.amount, native, budget.currency, transaction.date)
                        allocated += amount
                    if part.category_id in category_ids:
                        spent += amount
            else:
                spent += value
        status = merge_statuses(*inputs)
        statuses = {name: status for name in ("spent", "remaining", "percent", "is_over_budget")}
        statuses["monthly_limit"] = FinancialDataStatus()
        items.append(BudgetStatus(
            currency=budget.currency, **status.model_dump(), metric_status=statuses,
            budget_id=budget.id, category_id=budget.category_id, category_name=budget.category.name,
            category_color=budget.category.color, category_icon=budget.category.icon,
            monthly_limit=budget.monthly_limit,
            spent=spent if status.complete else None,
            remaining=budget.monthly_limit - spent if status.complete else None,
            percent=(float(spent / budget.monthly_limit * 100) if budget.monthly_limit else 0.0) if status.complete else None,
            is_over_budget=spent > budget.monthly_limit if status.complete else None,
        ))
    return BudgetStatusResponse(year=year, month=month, items=items, **merge_statuses(*items).model_dump())
