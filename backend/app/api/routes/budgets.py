from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session
from app.models.budget import Budget
from app.schemas.budget import BudgetCreate, BudgetRead, BudgetStatusResponse, BudgetUpdate
from app.services.budget_service import create_budget, delete_budget, get_budget_status, list_budgets, update_budget

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _to_read(budget: Budget) -> BudgetRead:
    return BudgetRead(
        id=budget.id,
        category_id=budget.category_id,
        category_name=budget.category.name,
        category_color=budget.category.color,
        category_icon=budget.category.icon,
        monthly_limit=budget.monthly_limit,
        currency=budget.currency,
    )


@router.get("", response_model=list[BudgetRead])
async def read_budgets(session: AsyncSession = Depends(get_session)) -> list[BudgetRead]:
    return [_to_read(budget) for budget in await list_budgets(session)]


@router.get("/status", response_model=BudgetStatusResponse)
async def read_budget_status(
    year: int = Query(default_factory=lambda: date.today().year, ge=2000, le=2100),
    month: int = Query(default_factory=lambda: date.today().month, ge=1, le=12),
    session: AsyncSession = Depends(get_session),
) -> BudgetStatusResponse:
    return await get_budget_status(session, year, month)


@router.post("", response_model=BudgetRead, status_code=201)
async def create_budget_route(payload: BudgetCreate, session: AsyncSession = Depends(get_session)) -> BudgetRead:
    return _to_read(await create_budget(session, payload))


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget_route(
    budget_id: int, payload: BudgetUpdate, session: AsyncSession = Depends(get_session)
) -> BudgetRead:
    return _to_read(await update_budget(session, budget_id, payload))


@router.delete("/{budget_id}", status_code=204)
async def delete_budget_route(budget_id: int, session: AsyncSession = Depends(get_session)) -> None:
    await delete_budget(session, budget_id)
