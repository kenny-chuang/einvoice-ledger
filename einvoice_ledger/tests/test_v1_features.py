import asyncio
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.alert_service import evaluate_price_alerts
from app.budget_service import budget_summary, evaluate_budget_notifications
from app.importer import CsvImporter
from app.crawler import LoginRequired
from app.mqtt_service import MqttPublisher
from app.models import (
    Base, CategoryBudget, DataQualityIssue, Invoice, InvoiceLine, NotificationEvent, PriceAlert, Product, SyncRun,
    SyncRunEvent,
)
from app.quality_service import resolve_quality_issue
from app.sync_service import SyncCoordinator
from test_importer import CSV, UNQUOTED_ADDRESS_COMMA_CSV


LOW_PRICE_CSV = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260601,GH12345678,35,開立已確認,否,123,商店甲,台北市,,1,35,35,測試飲料500ml
""".encode()


def make_factory():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_budget_uses_positive_items_allocated_discounts_and_dedupes_thresholds():
    factory = make_factory()
    with factory() as session:
        CsvImporter().import_bytes(session, CSV)
        session.add(CategoryBudget(category="水or飲料", monthly_limit=Decimal("100"), start_month="2026-05"))
        session.commit()

        summary = budget_summary(session, "2026-05")
        item = summary["items"][0]
        assert item["spent"] == Decimal("125")
        assert summary["unallocated_discount_total"] == Decimal("10")
        assert evaluate_budget_notifications(session, "2026-05") == 2
        session.commit()
        assert evaluate_budget_notifications(session, "2026-05") == 0


def test_price_alert_uses_raw_unit_price_and_dedupes_target_and_new_low():
    factory = make_factory()
    with factory() as session:
        CsvImporter().import_bytes(session, CSV)
        product = session.scalar(select(Product).where(Product.canonical_name == "測試飲料500ml"))
        session.add(PriceAlert(product_id=product.id, target_price=Decimal("45"), notify_new_low=True))
        session.commit()
        assert evaluate_price_alerts(session) == 1
        session.commit()
        assert evaluate_price_alerts(session) == 0

        CsvImporter().import_bytes(session, LOW_PRICE_CSV)
        assert evaluate_price_alerts(session) == 2
        session.commit()
        assert evaluate_price_alerts(session) == 0
        assert {event.value for event in session.scalars(select(NotificationEvent)).all()} == {
            Decimal("45"), Decimal("35")
        }
        CsvImporter().import_bytes(session, LOW_PRICE_CSV)
        assert evaluate_price_alerts(session) == 0
        assert session.scalar(select(func.count()).select_from(NotificationEvent)) == 3


def test_resolving_quality_issue_allows_valid_line_to_enter_comparison():
    factory = make_factory()
    with factory() as session:
        CsvImporter().import_bytes(session, UNQUOTED_ADDRESS_COMMA_CSV)
        issue = session.scalar(select(DataQualityIssue))
        line = session.get(InvoiceLine, issue.invoice_line_id)
        line.needs_review = True
        line.is_comparable = False
        session.commit()

        resolve_quality_issue(session, issue.id)
        assert line.needs_review is False
        assert line.is_comparable is True


def test_two_file_sync_rolls_back_all_imports_when_second_file_is_invalid(monkeypatch):
    monkeypatch.setattr("app.sync_service.RETRY_DELAYS", ())
    factory = make_factory()

    class FakeCrawler:
        data_dir = Path("/private/tmp")

        async def sync_months(self, months, progress):
            await progress("download", {"months": months})
            return [CSV, b"not-a-ministry-csv"]

    coordinator = SyncCoordinator(FakeCrawler(), session_factory=factory)
    run = asyncio.run(coordinator.start(wait=True))

    with factory() as session:
        assert run.status == "failed"
        assert session.scalar(select(func.count()).select_from(Invoice)) == 0
        assert session.scalar(select(func.count()).select_from(SyncRun)) == 1
        event = session.scalar(select(SyncRunEvent))
        assert event.stage == "queued"
        assert event.status == "completed"


def test_duplicate_sync_request_returns_the_existing_run_id(monkeypatch):
    monkeypatch.setattr("app.sync_service.RETRY_DELAYS", ())
    factory = make_factory()

    async def scenario():
        gate = asyncio.Event()

        class SlowCrawler:
            data_dir = Path("/private/tmp")

            async def sync_months(self, months, progress):
                await progress("download", {"months": months})
                await gate.wait()
                return [CSV.replace(b"202605", b"202607"), CSV.replace(b"202605", b"202606")]

        coordinator = SyncCoordinator(SlowCrawler(), session_factory=factory)
        first = await coordinator.start()
        second = await coordinator.start()
        assert first.id == second.id
        gate.set()
        await coordinator._task

    asyncio.run(scenario())


def test_sync_retries_transient_errors_but_never_retries_login_required(monkeypatch):
    monkeypatch.setattr("app.sync_service.RETRY_DELAYS", (0, 0, 0))
    factory = make_factory()

    class RetryCrawler:
        data_dir = Path("/private/tmp")
        calls = 0

        async def sync_months(self, months, progress):
            self.calls += 1
            if self.calls < 3:
                raise TimeoutError("temporary")
            return [CSV.replace(b"202605", b"202607"), CSV.replace(b"202605", b"202606")]

    retry_crawler = RetryCrawler()
    completed = asyncio.run(SyncCoordinator(retry_crawler, session_factory=factory).start(wait=True))
    assert completed.status == "completed"
    assert retry_crawler.calls == 3

    class LoginCrawler:
        data_dir = Path("/private/tmp")
        calls = 0

        async def sync_months(self, months, progress):
            self.calls += 1
            raise LoginRequired("expired")

    login_crawler = LoginCrawler()
    stopped = asyncio.run(SyncCoordinator(login_crawler, session_factory=factory).start(wait=True))
    assert stopped.status == "login_required"
    assert login_crawler.calls == 1


def test_mqtt_keeps_events_until_connection_recovers(monkeypatch):
    factory = make_factory()
    with factory() as session:
        session.add(NotificationEvent(
            dedupe_key="price:test", event_type="target_price", title="測試低價",
            message="已達目標價", value=Decimal("42"),
        ))
        session.commit()
        publisher = MqttPublisher()
        assert publisher.publish_pending_notifications(session) == 0
        assert session.scalar(select(NotificationEvent)).published_at is None

        published_topics = []
        publisher.connected = True
        publisher._client = object()
        monkeypatch.setattr(publisher, "_publish", lambda topic, payload, retain=True: published_topics.append(topic) or True)
        assert publisher.publish_pending_notifications(session) == 1
        assert "einvoice/events" in published_topics
        assert session.scalar(select(NotificationEvent)).published_at is not None
