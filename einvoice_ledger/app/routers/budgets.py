from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..budget_service import budget_summary, evaluate_budget_notifications
from ..database import get_session
from ..models import CategoryBudget


router = APIRouter(prefix="/api/budgets", tags=["budgets"])


class BudgetInput(BaseModel):
    monthly_limit: Decimal = Field(gt=0)
    active: bool = True
    start_month: str = ""


def serialize_budget(value: CategoryBudget) -> dict:
    return {
        "category": value.category,
        "monthly_limit": value.monthly_limit,
        "active": value.active,
        "start_month": value.start_month,
        "updated_at": value.updated_at,
    }


@router.get("/summary")
def get_budget_summary(month: str, session: Session = Depends(get_session)):
    try:
        return budget_summary(session, month)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_budgets(session: Session = Depends(get_session)):
    values = session.scalars(select(CategoryBudget).order_by(CategoryBudget.category)).all()
    return [serialize_budget(value) for value in values]


@router.get("/{category}")
def get_budget(category: str, session: Session = Depends(get_session)):
    value = session.scalar(select(CategoryBudget).where(CategoryBudget.category == category))
    if value is None:
        raise HTTPException(404, "找不到分類預算")
    return serialize_budget(value)


@router.put("/{category}")
def put_budget(category: str, payload: BudgetInput, session: Session = Depends(get_session)):
    category = category.strip()
    if not category:
        raise HTTPException(400, "分類不可空白")
    value = session.scalar(select(CategoryBudget).where(CategoryBudget.category == category))
    if value is None:
        value = CategoryBudget(category=category)
        session.add(value)
    value.monthly_limit = payload.monthly_limit
    value.active = payload.active
    value.start_month = payload.start_month
    session.flush()
    if payload.start_month:
        try:
            evaluate_budget_notifications(session, payload.start_month)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(400, str(exc)) from exc
    session.commit()
    session.refresh(value)
    return serialize_budget(value)


@router.delete("/{category}")
def delete_budget(category: str, session: Session = Depends(get_session)):
    value = session.scalar(select(CategoryBudget).where(CategoryBudget.category == category))
    if value is None:
        raise HTTPException(404, "找不到分類預算")
    session.delete(value)
    session.commit()
    return {"ok": True}
