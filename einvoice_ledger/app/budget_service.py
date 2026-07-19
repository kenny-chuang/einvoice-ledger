from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from .discounts import allocated_discount
from .models import CategoryBudget, Invoice, InvoiceLine, NotificationEvent
from .services import effective_category, effective_value


def month_bounds(month: str) -> tuple[date, date]:
    try:
        start = datetime.strptime(month, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise ValueError("月份必須是 YYYY-MM") from exc
    return start, start.replace(day=monthrange(start.year, start.month)[1])


def budget_summary(session: Session, month: str) -> dict:
    start, end = month_bounds(month)
    lines = session.scalars(
        select(InvoiceLine)
        .join(Invoice)
        .options(
            joinedload(InvoiceLine.invoice), joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction), joinedload(InvoiceLine.received_discount_allocations),
        )
        .where(
            Invoice.invoice_date.between(start, end), Invoice.is_void.is_(False),
            InvoiceLine.is_discount.is_(False), InvoiceLine.needs_review.is_(False),
        )
    ).unique().all()
    spent: dict[str, Decimal] = {}
    for line in lines:
        category = effective_category(line)
        amount = effective_value(line, "corrected_amount", "amount") + allocated_discount(line)
        spent[category] = spent.get(category, Decimal("0")) + amount

    budgets = session.scalars(
        select(CategoryBudget).where(
            CategoryBudget.active.is_(True),
            or_(CategoryBudget.start_month == "", CategoryBudget.start_month <= month),
        ).order_by(CategoryBudget.category)
    ).all()
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    elapsed = min(today.day, end.day) if today.year == start.year and today.month == start.month else end.day
    rows = []
    for budget in budgets:
        value = spent.get(budget.category, Decimal("0"))
        limit = budget.monthly_limit
        rows.append({
            "category": budget.category, "limit": limit, "spent": value,
            "remaining": limit - value, "usage_percent": value / limit * 100 if limit > 0 else Decimal("0"),
            "forecast": value / elapsed * end.day if elapsed > 0 else value,
        })
    unallocated = session.scalars(
        select(InvoiceLine).join(Invoice).where(
            Invoice.invoice_date.between(start, end), Invoice.is_void.is_(False),
            InvoiceLine.is_discount.is_(True), ~InvoiceLine.discount_allocations.any(),
        )
    ).all()
    return {
        "month": month, "items": rows,
        "total_limit": sum((row["limit"] for row in rows), Decimal("0")),
        "total_spent": sum((row["spent"] for row in rows), Decimal("0")),
        "total_remaining": sum((row["remaining"] for row in rows), Decimal("0")),
        "unallocated_discount_total": abs(sum((line.amount for line in unallocated), Decimal("0"))),
    }


def evaluate_budget_notifications(session: Session, month: str) -> int:
    created = 0
    for item in budget_summary(session, month)["items"]:
        for threshold in (80, 100):
            if item["usage_percent"] < threshold:
                continue
            key = f"budget:{month}:{item['category']}:{threshold}"
            if session.scalar(select(NotificationEvent).where(NotificationEvent.dedupe_key == key)):
                continue
            session.add(NotificationEvent(
                dedupe_key=key, event_type="budget_threshold", category=item["category"],
                title=f"{item['category']}預算已達 {threshold}%",
                message=f"{month} 已使用 NT${item['spent']}／NT${item['limit']}", value=item["usage_percent"],
            ))
            created += 1
    session.flush()
    return created
