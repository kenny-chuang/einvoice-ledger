import json

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_session
from ..models import SyncRun, SyncRunEvent
from ..sync_service import SyncCoordinator


def build_router(coordinator: SyncCoordinator) -> APIRouter:
    router = APIRouter(tags=["sync"])

    @router.post("/api/sync", status_code=202)
    async def start_sync(response: Response):
        run = await coordinator.start()
        response.status_code = 202
        return {"run_id": run.id, "status": run.status}

    @router.get("/api/sync-runs/{run_id}")
    def sync_run(run_id: int, session: Session = Depends(get_session)):
        run = session.get(SyncRun, run_id)
        if run is None:
            raise HTTPException(404, "找不到同步工作")
        events = session.scalars(select(SyncRunEvent).where(
            SyncRunEvent.sync_run_id == run_id
        ).order_by(SyncRunEvent.id)).all()
        return {
            "id": run.id, "months": run.months.split(",") if run.months else [],
            "status": run.status, "current_stage": run.current_stage,
            "attempt_count": run.attempt_count, "message": run.message,
            "started_at": run.started_at, "finished_at": run.finished_at,
            "stats": json.loads(run.stats_json or "{}"),
            "events": [{
                "stage": event.stage, "status": event.status, "attempt": event.attempt,
                "error_code": event.error_code,
                "metadata": json.loads(event.metadata_json or "{}"),
                "started_at": event.started_at, "finished_at": event.finished_at,
            } for event in events],
        }

    return router
