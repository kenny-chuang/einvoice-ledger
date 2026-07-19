from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import DataQualityIssue
from ..quality_service import quality_issues, resolve_quality_issue


router = APIRouter(prefix="/api/data-quality", tags=["quality"])


@router.get("")
def list_data_quality(
    month: str = "", store: str = "", issue_type: str = "", status: str = "open",
    session: Session = Depends(get_session),
):
    rows = quality_issues(
        session, month=month, store=store, issue_type=issue_type, status=status
    )
    issue_types = session.scalars(
        select(DataQualityIssue.issue_type).distinct().order_by(DataQualityIssue.issue_type)
    ).all()
    return {"items": rows, "total": len(rows), "issue_types": issue_types}


@router.post("/{issue_id}/resolve")
def resolve_data_quality(issue_id: int, session: Session = Depends(get_session)):
    issue = resolve_quality_issue(session, issue_id)
    if issue is None:
        raise HTTPException(404, "找不到資料品質問題")
    return {"ok": True, "id": issue.id, "status": issue.status}
