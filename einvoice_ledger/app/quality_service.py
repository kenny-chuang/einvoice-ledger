from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DataQualityIssue, Invoice, InvoiceLine, Store
from .budget_service import month_bounds


def quality_issues(
    session: Session, *, month: str = "", store: str = "", issue_type: str = "", status: str = "open"
) -> list[dict]:
    statement = select(DataQualityIssue, Invoice, Store).outerjoin(
        Invoice, DataQualityIssue.invoice_id == Invoice.id
    ).outerjoin(Store, Invoice.store_id == Store.id).order_by(DataQualityIssue.created_at.desc())
    if status:
        statement = statement.where(DataQualityIssue.status == status)
    if issue_type:
        statement = statement.where(DataQualityIssue.issue_type == issue_type)
    if store:
        statement = statement.where(Store.name.contains(store))
    if month:
        start, end = month_bounds(month)
        statement = statement.where(Invoice.invoice_date.between(start, end))
    return [{
        "id": issue.id, "issue_type": issue.issue_type, "severity": issue.severity,
        "confidence": issue.confidence, "repair_rule": issue.repair_rule, "message": issue.message,
        "status": issue.status, "invoice_id": issue.invoice_id, "invoice_line_id": issue.invoice_line_id,
        "invoice_date": invoice.invoice_date if invoice else None,
        "invoice_number": invoice.invoice_number if invoice else None, "store": store_row.name if store_row else "",
        "created_at": issue.created_at, "resolved_at": issue.resolved_at,
    } for issue, invoice, store_row in session.execute(statement).all()]


def resolve_quality_issue(session: Session, issue_id: int) -> DataQualityIssue | None:
    issue = session.get(DataQualityIssue, issue_id)
    if issue:
        issue.status = "resolved"
        issue.resolved_at = datetime.now(UTC).replace(tzinfo=None)
        line = session.get(InvoiceLine, issue.invoice_line_id) if issue.invoice_line_id else None
        if line:
            line.needs_review = False
            line.is_comparable = bool(
                not line.is_discount and line.unit_price is not None and line.unit_price > 0 and line.amount > 0
            )
        session.commit()
    return issue
