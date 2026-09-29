"""Savings goals: CRUD for the goal itself, plus a running current_amount —
the sum of all logged GoalContribution rows, computed on read rather than
stored, so it's never out of sync with the log."""
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import exists, func, select
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal, GoalContribution
from app.schemas.goal import GoalContributionCreate, GoalCreate, GoalRead, GoalUpdate
from app.services.settings_service import effective_default_currency

_SELECT_WITH_TOTAL = (
    select(
        Goal.id,
        Goal.name,
        Goal.currency,
        Goal.target_amount,
        Goal.target_date,
        func.coalesce(func.sum(GoalContribution.amount), 0).label("current_amount"),
    )
    .outerjoin(GoalContribution, GoalContribution.goal_id == Goal.id)
    .group_by(Goal.id, Goal.name, Goal.currency, Goal.target_amount, Goal.target_date, Goal.created_at)
    .order_by(Goal.created_at)
)


def _to_read(row: Row) -> GoalRead:
    current = row.current_amount
    target = row.target_amount
    percent = float(current / target * 100) if target else 0.0
    return GoalRead(
        id=row.id,
        name=row.name,
        currency=row.currency,
        target_amount=target,
        target_date=row.target_date,
        current_amount=current,
        remaining=target - current,
        percent=percent,
        is_reached=current >= target,
    )


async def _read_one(session: AsyncSession, goal_id: int) -> GoalRead:
    row = (await session.execute(_SELECT_WITH_TOTAL.where(Goal.id == goal_id))).one()
    return _to_read(row)


async def list_goals(session: AsyncSession) -> list[GoalRead]:
    rows = (await session.execute(_SELECT_WITH_TOTAL)).all()
    return [_to_read(row) for row in rows]


async def create_goal(session: AsyncSession, payload: GoalCreate) -> GoalRead:
    goal = Goal(name=payload.name, target_amount=payload.target_amount, target_date=payload.target_date,
                currency=payload.currency or await effective_default_currency(session))
    session.add(goal)
    await session.commit()
    return GoalRead(
        id=goal.id,
        name=goal.name,
        currency=goal.currency,
        target_amount=goal.target_amount,
        target_date=goal.target_date,
        current_amount=Decimal("0"),
        remaining=goal.target_amount,
        percent=0.0,
        is_reached=False,
    )


async def update_goal(session: AsyncSession, goal_id: int, payload: GoalUpdate) -> GoalRead:
    goal = await session.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    if payload.currency is not None and payload.currency != goal.currency:
        if await session.scalar(select(exists().where(GoalContribution.goal_id == goal_id))):
            raise HTTPException(409, "currency_change_requires_empty_history")
        if payload.target_amount is None:
            raise HTTPException(409, "currency_change_requires_target_replacement")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(goal, field, value)
    await session.commit()
    return await _read_one(session, goal_id)


async def delete_goal(session: AsyncSession, goal_id: int) -> None:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    await session.delete(goal)
    await session.commit()


async def add_contribution(session: AsyncSession, goal_id: int, payload: GoalContributionCreate) -> GoalRead:
    goal = await session.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if goal is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    session.add(GoalContribution(goal_id=goal_id, amount=payload.amount, date=payload.date, note=payload.note))
    await session.commit()
    return await _read_one(session, goal_id)
