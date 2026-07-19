from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .alert_service import evaluate_price_alerts
from .budget_service import evaluate_budget_notifications
from .crawler import InvoiceCrawler, LoginRequired
from .database import SessionLocal
from .importer import CsvImporter, validate_csv_month
from .budget_service import budget_summary
from .models import DataQualityIssue, SyncRun, SyncRunEvent
from .services import dashboard
from .mqtt_service import mqtt_publisher


TAIPEI = ZoneInfo("Asia/Taipei")
RETRY_DELAYS = tuple(int(value) for value in os.getenv("E_INVOICE_RETRY_DELAYS", "120,300,900").split(","))


class SyncCoordinator:
    def __init__(self, crawler: InvoiceCrawler, session_factory=SessionLocal):
        self.crawler = crawler
        self.session_factory = session_factory
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def months_for_today() -> list[str]:
        today = datetime.now(TAIPEI).date()
        current = today.strftime("%Y%m")
        previous_day = today.replace(day=1).fromordinal(today.replace(day=1).toordinal() - 1)
        return [current, previous_day.strftime("%Y%m")]

    def active_run(self) -> SyncRun | None:
        with self.session_factory() as session:
            return session.scalar(
                select(SyncRun).where(SyncRun.status.in_(("queued", "running"))).order_by(SyncRun.id.desc())
            )

    async def start(self, *, wait: bool = False) -> SyncRun:
        async with self._lock:
            active = self.active_run()
            if active and self._task and not self._task.done():
                task = self._task
                run_id = active.id
            else:
                if active:
                    with self.session_factory() as session:
                        stale = session.get(SyncRun, active.id)
                        stale.status = "failed"
                        stale.current_stage = "interrupted"
                        stale.message = "前次同步因應用程式重啟而中止"
                        stale.finished_at = datetime.now(UTC).replace(tzinfo=None)
                        for event in session.scalars(select(SyncRunEvent).where(
                            SyncRunEvent.sync_run_id == stale.id,
                            SyncRunEvent.status == "running",
                        )).all():
                            event.status = "failed"
                            event.error_code = "ProcessInterrupted"
                            event.finished_at = stale.finished_at
                        session.commit()
                with self.session_factory() as session:
                    run = SyncRun(
                        months=",".join(self.months_for_today()), status="queued", current_stage="queued",
                        message="同步已排入佇列", stats_json="{}",
                    )
                    session.add(run)
                    session.flush()
                    session.add(SyncRunEvent(
                        sync_run_id=run.id, stage="queued", status="running", attempt=0,
                        metadata_json="{}",
                    ))
                    session.commit(); session.refresh(run); run_id = run.id
                task = asyncio.create_task(self.execute(run_id))
                self._task = task
        if wait:
            await task
        with self.session_factory() as session:
            return session.get(SyncRun, run_id)

    async def _progress(self, run_id: int, stage: str, metadata: dict, attempt: int) -> None:
        with self.session_factory() as session:
            run = session.get(SyncRun, run_id)
            if not run:
                return
            now = datetime.now(UTC).replace(tzinfo=None)
            previous = session.scalar(
                select(SyncRunEvent).where(
                    SyncRunEvent.sync_run_id == run_id, SyncRunEvent.status == "running"
                ).order_by(SyncRunEvent.id.desc())
            )
            if previous and previous.stage != stage:
                previous.status = "completed"; previous.finished_at = now
            if not previous or previous.stage != stage:
                session.add(SyncRunEvent(
                    sync_run_id=run_id, stage=stage, attempt=attempt,
                    metadata_json=json.dumps(metadata, ensure_ascii=False), started_at=now,
                ))
            run.status = "running"; run.current_stage = stage; run.attempt_count = attempt
            run.message = f"同步階段：{stage}"
            session.commit()

    async def execute(self, run_id: int) -> None:
        months = self.months_for_today()
        downloads: list[bytes] | None = None
        error: Exception | None = None
        for attempt in range(1, len(RETRY_DELAYS) + 2):
            try:
                async def progress(stage: str, metadata: dict) -> None:
                    await self._progress(run_id, stage, metadata, attempt)

                parameters = inspect.signature(self.crawler.sync_months).parameters
                downloads = await (
                    self.crawler.sync_months(months, progress)
                    if len(parameters) >= 2 else self.crawler.sync_months(months)
                )
                error = None
                break
            except LoginRequired as exc:
                error = exc
                break
            except Exception as exc:
                error = exc
                if attempt <= len(RETRY_DELAYS):
                    await self._progress(run_id, "retry_wait", {"delay": RETRY_DELAYS[attempt - 1]}, attempt)
                    await asyncio.sleep(RETRY_DELAYS[attempt - 1])
        if error or downloads is None:
            await self._finish_failed(run_id, error or RuntimeError("download_failed"))
            return

        try:
            await self._progress(run_id, "validate_csv", {"files": len(downloads)}, 1)
            if len(downloads) != len(months):
                raise ValueError("下載檔案數量與查詢月份數量不一致")
            hashes = [hashlib.sha256(value).hexdigest() for value in downloads]
            if len(set(months)) > 1 and len(set(hashes)) != len(hashes):
                raise ValueError("不同月份下載到相同 CSV，已拒絕匯入")
            validated_rows = {
                month: validate_csv_month(content, month)
                for month, content in zip(months, downloads, strict=True)
            }
            with self.session_factory() as session:
                results = []
                for content in downloads:
                    results.append(CsvImporter().import_bytes(
                        session, content, commit=False, sync_run_id=run_id
                    ))
                # Keep reconciliation inside the same transaction. Opening a second
                # SQLite session here would contend with the uncommitted importer
                # writes and could leave the run in a misleading state.
                now = datetime.now(UTC).replace(tzinfo=None)
                running = session.scalar(select(SyncRunEvent).where(
                    SyncRunEvent.sync_run_id == run_id, SyncRunEvent.status == "running"
                ).order_by(SyncRunEvent.id.desc()))
                if running:
                    running.status = "completed"
                    running.finished_at = now
                session.add(SyncRunEvent(
                    sync_run_id=run_id,
                    stage="reconcile",
                    attempt=1,
                    metadata_json=json.dumps({"files": len(results)}, ensure_ascii=False),
                    started_at=now,
                ))
                run = session.get(SyncRun, run_id)
                run.current_stage = "reconcile"
                run.message = "同步階段：reconcile"
                month = datetime.now(TAIPEI).strftime("%Y-%m")
                budget_events = evaluate_budget_notifications(session, month)
                price_events = evaluate_price_alerts(session)
                stats = {
                    "files": len(downloads), "source_hashes": hashes,
                    "validated_rows_by_month": validated_rows,
                    "rows": sum(result.data_rows for result in results),
                    "rows_repaired": sum(result.rows_repaired for result in results),
                    "quality_issues": sum(result.quality_issues for result in results),
                    "skipped_rows": sum(result.skipped_rows for result in results),
                    "pending_review_rows": sum(result.pending_review_rows for result in results),
                    "invoices": sum(result.invoices_upserted for result in results),
                    "positive_lines": sum(result.lines_created - result.discounts for result in results),
                    "discount_lines": sum(result.discounts for result in results),
                    "void_invoices": sum(result.void_invoices for result in results),
                    "positive_amount": sum((result.positive_amount for result in results), Decimal("0")),
                    "discount_amount": sum((result.discount_amount for result in results), Decimal("0")),
                    "net_amount": sum((result.positive_amount + result.discount_amount for result in results), Decimal("0")),
                    "budget_notifications": budget_events, "price_notifications": price_events,
                }
                now = datetime.now(UTC).replace(tzinfo=None)
                run.status = "completed"; run.current_stage = "completed"; run.finished_at = now
                run.stats_json = json.dumps(stats, ensure_ascii=False, default=str); run.source_hash = stats["source_hashes"][-1]
                run.message = f"已下載並匯入 {len(downloads)} 個月份，共 {stats['rows']} 筆明細"
                running = session.scalars(select(SyncRunEvent).where(
                    SyncRunEvent.sync_run_id == run_id, SyncRunEvent.status == "running"
                )).all()
                for event in running:
                    event.status = "completed"; event.finished_at = now
                session.commit()
            self._cleanup_diagnostics()
            self.publish_mqtt_state()
        except Exception as exc:
            await self._finish_failed(run_id, exc)

    async def _finish_failed(self, run_id: int, exc: Exception) -> None:
        with self.session_factory() as session:
            run = session.get(SyncRun, run_id)
            now = datetime.now(UTC).replace(tzinfo=None)
            is_login = isinstance(exc, LoginRequired)
            run.status = "login_required" if is_login else "failed"
            run.current_stage = run.status; run.finished_at = now
            run.attempt_count = max(run.attempt_count, 1)
            run.message = str(exc) if is_login else f"同步失敗：{type(exc).__name__}"
            event = session.scalar(select(SyncRunEvent).where(
                SyncRunEvent.sync_run_id == run_id, SyncRunEvent.status == "running"
            ).order_by(SyncRunEvent.id.desc()))
            if event:
                event.status = "failed"; event.finished_at = now; event.error_code = type(exc).__name__
            else:
                session.add(SyncRunEvent(
                    sync_run_id=run_id, stage=run.current_stage, status="failed", attempt=run.attempt_count,
                    error_code=type(exc).__name__, metadata_json="{}", started_at=now, finished_at=now,
                ))
            session.commit()
        self.publish_mqtt_state()

    def publish_mqtt_state(self) -> None:
        if not mqtt_publisher.connected:
            mqtt_publisher.connect()
        with self.session_factory() as session:
            latest = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
            recent_automatic = session.scalars(
                select(SyncRun).where(SyncRun.months.contains(",")).order_by(SyncRun.id.desc()).limit(3)
            ).all()
            three_failures = len(recent_automatic) == 3 and all(
                run.status == "failed" for run in recent_automatic
            )
            month = datetime.now(TAIPEI).strftime("%Y-%m")
            summary = dashboard(session)
            budgets = budget_summary(session, month)
            quality_count = session.scalar(select(func.count(DataQualityIssue.id)).where(
                DataQualityIssue.status == "open"
            )) or 0
            mqtt_publisher.publish_state("last_sync", latest.finished_at.isoformat() if latest and latest.finished_at else "")
            mqtt_publisher.publish_state("month_total", summary["total"], status=latest.status if latest else "unknown")
            mqtt_publisher.publish_state("budget_remaining", budgets["total_remaining"])
            mqtt_publisher.publish_state("uncategorized_count", summary["uncategorized_count"])
            mqtt_publisher.publish_state("data_quality_issues", quality_count)
            mqtt_publisher.publish_state("unallocated_discounts", summary["unallocated_discount_count"])
            mqtt_publisher.publish_state("login_required", "ON" if latest and latest.status == "login_required" else "OFF")
            mqtt_publisher.publish_state("sync_problem", "ON" if three_failures else "OFF")
            mqtt_publisher.publish_pending_notifications(session)

    def _cleanup_diagnostics(self) -> None:
        cutoff = datetime.now().timestamp() - 7 * 86400
        for pattern in ("*.png", "crawler-debug.json", "login-debug.json"):
            for path in self.crawler.data_dir.glob(pattern):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
