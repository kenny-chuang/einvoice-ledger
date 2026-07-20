from __future__ import annotations

import base64
import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from .crawler import InvoiceCrawler, LoginRequired
from .database import SessionLocal, data_dir, get_session, init_database
from .discounts import allocate_discount, discount_rows, group_discount_rows, reset_discount
from .importer import DEFAULT_CATEGORY_RULES, CsvImporter, normalize_text, parse_size
from .models import CategoryRule, Invoice, InvoiceLine, InvoiceLineCorrection, Product, ProductAlias, Store, SyncRun
from .models import DataQualityIssue, NotificationEvent, PriceAlert
from .alert_service import evaluate_price_alerts
from .budget_service import budget_summary, evaluate_budget_notifications
from .mqtt_service import mqtt_publisher
from .sync_service import SyncCoordinator
from .routers.alerts import router as alerts_router
from .routers.budgets import router as budgets_router
from .routers.quality import router as quality_router
from .routers.sync import build_router as build_sync_router
from .routers.system import health_router, router as system_router
from .routers.auth import build_router as build_auth_router
from .routers.categories import build_router as build_categories_router
from .routers.dashboard import build_router as build_dashboard_router
from .routers.discounts import build_router as build_discounts_router
from .routers.imports import build_router as build_imports_router
from .routers.products import build_router as build_products_router
from .routers.purchases import build_router as build_purchases_router
from .product_manager import (
    BudgetMergeRequired, assign_product_to_target, category_options, category_usage, delete_category, find_product_by_name,
    set_group_category, set_product_alias,
)
from .services import (
    dashboard, effective_category, effective_date, effective_product, effective_store_name,
    effective_value, product_comparison_rows, product_prices, product_search, purchase_month_options,
    purchase_search, status,
)


APP_DIR = Path(__file__).parent
FRONTEND_DIST = APP_DIR.parent / "frontend" / "dist"
def format_money(value) -> str:
    number = f"{(value or 0):,.3f}".rstrip("0").rstrip(".")
    return f"NT${number}"


def format_number(value) -> str:
    if value is None:
        return "-"
    number = f"{value:f}"
    return number.rstrip("0").rstrip(".") if "." in number else number


def short_name(value: str | None) -> str:
    value = value or ""
    return value if len(value) <= 10 else f"{value[:10]}..."


crawler = InvoiceCrawler(data_dir())
sync_coordinator = SyncCoordinator(crawler)
scheduler = AsyncIOScheduler(timezone="Asia/Taipei")


async def scheduled_sync() -> None:
    # Keep the factory assignable for isolated tests and staging runs.
    sync_coordinator.session_factory = SessionLocal
    await sync_coordinator.start(wait=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    scheduler.add_job(scheduled_sync, "cron", hour=4, minute=15, id="daily_sync", replace_existing=True)
    scheduler.start()
    mqtt_publisher.connect()
    yield
    scheduler.shutdown(wait=False)
    mqtt_publisher.close()
    await crawler.close()


app = FastAPI(title="發票記帳助手", lifespan=lifespan)
app.include_router(quality_router)
app.include_router(budgets_router)
app.include_router(alerts_router)
app.include_router(system_router)
app.include_router(health_router)
app.include_router(build_sync_router(sync_coordinator))
app.mount(
    "/app",
    StaticFiles(directory=str(FRONTEND_DIST), html=True, check_dir=False),
    name="vue-app",
)


def serialize_discount_groups(session: Session) -> dict:
    rows = discount_rows(session)
    groups = group_discount_rows(rows)

    def serialize_row(row: dict) -> dict:
        discount = row["discount"]
        return {
            "id": discount.id,
            "name": discount.raw_name,
            "amount": discount.amount,
            "allocated": row["allocation"] is not None,
            "reason": row["reason"],
            "suggestion": None if row["suggestion"] is None else {
                "line_id": row["suggestion"].id,
                "name": row["suggestion_name"],
                "method": row["suggestion_method"],
            },
            "candidates": [
                {
                    "line_id": candidate["line"].id,
                    "name": candidate["name"],
                    "amount": candidate["line"].amount,
                }
                for candidate in row["candidates"]
            ],
            "allocations": [
                {
                    "line_id": target["line"].id,
                    "name": target["name"],
                    "original_amount": target["line"].amount,
                    "amount": target["amount"],
                    "net_amount": target["line"].amount + target["amount"],
                }
                for target in row["allocated_targets"]
            ],
        }

    def serialize_months(month_groups: list[dict]) -> list[dict]:
        return [
            {
                "month": month_group["month"],
                "label": month_group["label"],
                "invoice_count": month_group["invoice_count"],
                "discount_count": month_group["discount_count"],
                "total": month_group["total"],
                "invoices": [
                    {
                        "id": invoice_group["invoice"].id,
                        "date": invoice_group["invoice"].invoice_date,
                        "invoice_number": invoice_group["invoice"].invoice_number,
                        "store": invoice_group["invoice"].store.name,
                        "discount_count": invoice_group["discount_count"],
                        "total": invoice_group["total"],
                        "all_allocated": invoice_group["all_allocated"],
                        "discounts": [serialize_row(row) for row in invoice_group["discount_rows"]],
                    }
                    for invoice_group in month_group["invoices"]
                ],
            }
            for month_group in month_groups
        ]

    return {
        "summary": {
            "unallocated_discount_count": sum(1 for row in rows if row["allocation"] is None),
            "allocated_discount_count": sum(1 for row in rows if row["allocation"] is not None),
            "unallocated_invoice_count": sum(group["invoice_count"] for group in groups["unallocated"]),
            "allocated_invoice_count": sum(group["invoice_count"] for group in groups["allocated"]),
        },
        "unallocated": serialize_months(groups["unallocated"]),
        "allocated": serialize_months(groups["allocated"]),
    }


def api_discounts(session: Session = Depends(get_session)):
    return serialize_discount_groups(session)


def api_allocate_discount(
    discount_line_id: int,
    target_line_ids: list[int] = Form(...),
    method: str = Form("manual"),
    session: Session = Depends(get_session),
):
    try:
        allocations = allocate_discount(session, discount_line_id, target_line_ids, method)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return {
        "ok": True,
        "allocations": [
            {"target_line_id": allocation.target_line_id, "amount": allocation.amount}
            for allocation in allocations
        ],
    }


def api_reset_discount(discount_line_id: int, session: Session = Depends(get_session)):
    try:
        reset_discount(session, discount_line_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return {"ok": True}


def get_purchase_line(session: Session, line_id: int) -> InvoiceLine:
    line = session.scalar(
        select(InvoiceLine)
        .options(
            joinedload(InvoiceLine.invoice).joinedload(Invoice.store),
            joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
        )
        .where(InvoiceLine.id == line_id)
    )
    if line is None:
        raise HTTPException(404, "找不到消費明細")
    return line


def serialize_purchase_detail(line: InvoiceLine) -> dict:
    product = effective_product(line)
    correction = line.correction
    return {
        "id": line.id,
        "is_corrected": correction is not None,
        "original": {
            "date": line.invoice.invoice_date,
            "invoice_number": line.invoice.invoice_number,
            "product_name": line.raw_name,
            "store_name": line.invoice.store.name,
            "category": line.product.category if line.product else "待分類",
            "quantity": line.quantity,
            "unit_price": line.unit_price,
            "amount": line.amount,
        },
        "values": {
            "date": effective_date(line),
            "invoice_number": (
                correction.corrected_invoice_number
                if correction and correction.corrected_invoice_number
                else line.invoice.invoice_number
            ),
            "product_name": product.display_name if product else line.raw_name,
            "store_name": effective_store_name(line),
            "category": effective_category(line),
            "quantity": effective_value(line, "corrected_quantity", "quantity"),
            "unit_price": effective_value(line, "corrected_unit_price", "unit_price"),
            "amount": effective_value(line, "corrected_amount", "amount"),
            "note": correction.note if correction else "",
        },
    }


def api_purchase_detail(line_id: int, session: Session = Depends(get_session)):
    line = get_purchase_line(session, line_id)
    if line.is_discount:
        raise HTTPException(400, "折扣明細請使用折扣分攤功能")
    return serialize_purchase_detail(line)


def parse_required_decimal(value: str, label: str) -> Decimal:
    try:
        return Decimal(value.strip())
    except (InvalidOperation, AttributeError):
        raise HTTPException(400, f"{label}格式不正確")


def parse_optional_decimal(value: str, label: str) -> Decimal | None:
    if not value.strip():
        return None
    return parse_required_decimal(value, label)


def apply_purchase_correction(
    session: Session,
    line: InvoiceLine,
    corrected_date: date,
    corrected_invoice_number: str,
    corrected_product_name: str,
    corrected_store_name: str,
    corrected_category: str,
    new_category: str,
    corrected_quantity: str,
    corrected_unit_price: str,
    corrected_amount: str,
    note: str,
) -> InvoiceLineCorrection:
    product_name = corrected_product_name.strip()
    if not product_name:
        raise HTTPException(400, "商品名稱不可空白")
    invoice_number = corrected_invoice_number.strip()
    if not invoice_number:
        raise HTTPException(400, "發票號碼不可空白")
    store_name = corrected_store_name.strip()
    if not store_name:
        raise HTTPException(400, "商店名稱不可空白")

    product = find_product_by_name(session, product_name)
    if product is None:
        normalized = normalize_text(product_name)
        size_value, size_unit = parse_size(product_name)
        product = Product(
            canonical_name=product_name,
            normalized_name=normalized,
            size_value=size_value,
            size_unit=size_unit,
            category=corrected_category.strip() or "待分類",
        )
        session.add(product)
        session.flush()

    selected_category = new_category.strip() or corrected_category.strip() or "待分類"
    correction = line.correction or InvoiceLineCorrection(invoice_line_id=line.id)
    correction.corrected_date = corrected_date
    correction.corrected_invoice_number = invoice_number
    correction.corrected_product_id = product.id
    correction.corrected_store_name = store_name
    correction.corrected_category = selected_category
    correction.corrected_quantity = parse_optional_decimal(corrected_quantity, "數量")
    correction.corrected_unit_price = parse_optional_decimal(corrected_unit_price, "單價")
    correction.corrected_amount = parse_required_decimal(corrected_amount, "金額")
    correction.note = note.strip()
    line.needs_review = False
    line.quality_confidence = "high"
    if not line.is_discount and line.unit_price is not None and line.unit_price > 0 and line.amount > 0:
        line.is_comparable = True
    for issue in session.scalars(select(DataQualityIssue).where(
        DataQualityIssue.invoice_line_id == line.id, DataQualityIssue.status == "open"
    )).all():
        issue.status = "resolved"
        issue.resolved_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(correction)
    set_group_category(session, product, selected_category)
    session.commit()
    return correction


def redirect_with_ingress(request: Request, destination: str) -> RedirectResponse:
    """Preserve the Home Assistant Ingress prefix for browser redirects."""
    safe_destination = destination if destination.startswith("/") and not destination.startswith("//") else "/"
    ingress_path = request.headers.get("x-ingress-path", "").rstrip("/")
    if ingress_path and not safe_destination.startswith(f"{ingress_path}/"):
        safe_destination = f"{ingress_path}{safe_destination}"
    return RedirectResponse(safe_destination, status_code=303)


def api_update_purchase(
    line_id: int,
    corrected_date: date = Form(...),
    corrected_invoice_number: str = Form(...),
    corrected_product_name: str = Form(...),
    corrected_store_name: str = Form(...),
    corrected_category: str = Form(...),
    new_category: str = Form(""),
    corrected_quantity: str = Form(""),
    corrected_unit_price: str = Form(""),
    corrected_amount: str = Form(...),
    note: str = Form(""),
    session: Session = Depends(get_session),
):
    line = get_purchase_line(session, line_id)
    if line.is_discount:
        raise HTTPException(400, "折扣明細請使用折扣分攤功能")
    apply_purchase_correction(
        session, line, corrected_date, corrected_invoice_number, corrected_product_name,
        corrected_store_name, corrected_category, new_category, corrected_quantity,
        corrected_unit_price, corrected_amount, note,
    )
    return {"ok": True, "purchase": serialize_purchase_detail(get_purchase_line(session, line_id))}


def api_reset_purchase(line_id: int, session: Session = Depends(get_session)):
    line = get_purchase_line(session, line_id)
    reset_purchase_correction(session, line)
    return {"ok": True, "purchase": serialize_purchase_detail(get_purchase_line(session, line_id))}


def reset_purchase_correction(session: Session, line: InvoiceLine) -> None:
    if not line.correction:
        return
    corrected_product_id = line.correction.corrected_product_id
    session.delete(line.correction)
    session.flush()
    if corrected_product_id and corrected_product_id != line.product_id:
        has_source_lines = session.scalar(
            select(InvoiceLine.id).where(InvoiceLine.product_id == corrected_product_id).limit(1)
        )
        has_other_corrections = session.scalar(
            select(InvoiceLineCorrection.id)
            .where(InvoiceLineCorrection.corrected_product_id == corrected_product_id)
            .limit(1)
        )
        product = session.get(Product, corrected_product_id)
        if product and not has_source_lines and not has_other_corrections:
            session.delete(product)
    session.commit()


def import_csv_contents(session: Session, contents: bytes):
    result = CsvImporter().import_bytes(session, contents, commit=False)
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(SyncRun(
        months="manual",
        source_hash=result.source_hash,
        status="completed",
        current_stage="completed",
        message=f"匯入 {result.invoices_upserted} 張發票 / {result.lines_created} 筆品項",
        started_at=completed_at,
        finished_at=completed_at,
    ))
    evaluate_budget_notifications(session, datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m"))
    evaluate_price_alerts(session)
    session.commit()
    return result


async def api_import_csv(file: UploadFile = File(...), session: Session = Depends(get_session)):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "請上傳 CSV 檔案")
    try:
        result = import_csv_contents(session, await file.read())
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(400, "CSV 格式不正確，請使用財政部下載的 UTF-8 CSV") from exc
    return {
        "ok": True,
        "rows_read": result.rows_read,
        "data_rows": result.data_rows,
        "rows_repaired": result.rows_repaired,
        "quality_issues": result.quality_issues,
        "skipped_rows": result.skipped_rows,
        "pending_review_rows": result.pending_review_rows,
        "positive_amount": result.positive_amount,
        "discount_amount": result.discount_amount,
        "invoices_upserted": result.invoices_upserted,
        "lines_created": result.lines_created,
        "discounts": result.discounts,
        "void_invoices": result.void_invoices,
    }


async def api_status(session: Session = Depends(get_session)):
    data = status(session)
    run = data["last_run"]
    has_session = crawler.has_session()
    session_valid = await crawler.session_is_valid()
    return {
        "login_required": data["login_required"] or not session_valid,
        "has_session": has_session,
        "session_valid": session_valid,
        "last_run": None if not run else {
            "status": run.status,
            "message": run.message,
            "finished_at": run.finished_at,
        },
    }


async def api_dashboard(session: Session = Depends(get_session)):
    summary = dashboard(session)
    state = status(session)
    run = summary["last_run"]
    session_valid = await crawler.session_is_valid()
    month = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m")
    budgets = budget_summary(session, month)
    quality_count = session.scalar(select(func.count(DataQualityIssue.id)).where(
        DataQualityIssue.status == "open"
    )) or 0
    latest_notification = session.scalar(select(NotificationEvent).order_by(
        NotificationEvent.created_at.desc()
    ))
    return {
        "month_total": summary["total"],
        "uncategorized_total": summary["uncategorized"],
        "uncategorized_count": summary["uncategorized_count"],
        "uncategorized_product_count": summary["uncategorized_product_count"],
        "unallocated_discount_count": summary["unallocated_discount_count"],
        "unallocated_discount_total": summary["unallocated_discount_total"],
        "login_required": state["login_required"] or not session_valid,
        "budget": budgets,
        "data_quality_issue_count": quality_count,
        "latest_notification": None if latest_notification is None else {
            "id": latest_notification.id,
            "title": latest_notification.title,
            "message": latest_notification.message,
            "event_type": latest_notification.event_type,
            "created_at": latest_notification.created_at,
        },
        "last_run": None if run is None else {
            "id": run.id,
            "status": run.status,
            "current_stage": run.current_stage,
            "attempt_count": run.attempt_count,
            "message": run.message,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
    }


def api_products(query: str = "", category: str = "", session: Session = Depends(get_session)):
    return [
        {"id": product.id, "name": product.display_name, "raw_name": product.canonical_name, "category": product.category}
        for product in product_search(session, query, category)
    ]


def api_product_comparisons(
    query: str = "", category: str = "", alert_status: str = "", view: str = "recent",
    sort: str = "recent", page: int = 1, per_page: int = 25,
    session: Session = Depends(get_session),
):
    if view not in {"recent", "frequent", "price_up", "at_low", "alerts"}:
        raise HTTPException(400, "不支援的快速檢視")
    if sort not in {"recent", "count", "price_gap", "lowest", "name"}:
        raise HTTPException(400, "不支援的排序方式")
    page = max(page, 1)
    per_page = min(max(per_page, 10), 100)
    rows = []
    for row in product_comparison_rows(session, query, category):
        alert = session.scalar(select(PriceAlert).where(PriceAlert.product_id == row["product"].id))
        latest = row["latest"]
        item = {
            "id": row["product"].id,
            "name": row["product"].display_name,
            "category": row["product"].category,
            "purchase_count": row["purchase_count"],
            "minimum": row["minimum"],
            "maximum": row["maximum"],
            "latest": latest,
            "latest_date": row["latest_date"],
            "distance_from_low": latest - row["minimum"] if latest is not None and row["minimum"] is not None else None,
            "distance_from_target": latest - alert.target_price if latest is not None and alert and alert.target_price is not None else None,
            "alert_enabled": bool(alert and alert.enabled),
            "target_price": alert.target_price if alert else None,
        }
        if alert_status == "enabled" and not item["alert_enabled"]:
            continue
        if alert_status == "target_met" and not (
            alert and alert.enabled and alert.target_price is not None and latest is not None and latest <= alert.target_price
        ):
            continue
        if view == "price_up" and not (item["distance_from_low"] is not None and item["distance_from_low"] > 0):
            continue
        if view == "at_low" and item["distance_from_low"] != 0:
            continue
        if view == "alerts" and not item["alert_enabled"]:
            continue
        rows.append(item)
    effective_sort = "count" if view == "frequent" else "price_gap" if view == "price_up" else sort
    if effective_sort == "count":
        rows.sort(key=lambda item: (item["purchase_count"], item["latest_date"] or date.min), reverse=True)
    elif effective_sort == "price_gap":
        rows.sort(key=lambda item: (item["distance_from_low"] is not None, item["distance_from_low"] or 0), reverse=True)
    elif effective_sort == "lowest":
        rows.sort(key=lambda item: (item["latest"] is None, item["latest"] or 0))
    elif effective_sort == "name":
        rows.sort(key=lambda item: item["name"].casefold())
    else:
        rows.sort(key=lambda item: (item["latest_date"] is not None, item["latest_date"] or date.min), reverse=True)
    total = len(rows)
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    return {
        "items": rows[start:start + per_page], "total": total, "page": page,
        "per_page": per_page, "total_pages": total_pages, "sort": effective_sort, "view": view,
    }


def api_categories(session: Session = Depends(get_session)):
    return category_options(session)


def api_category_usage(session: Session = Depends(get_session)):
    return category_usage(session)


def api_purchase_months(session: Session = Depends(get_session)):
    return purchase_month_options(session)


def api_purchases(
    query: str = "",
    month: str = "",
    category: str = "",
    page: int = 1,
    per_page: int = 50,
    session: Session = Depends(get_session),
):
    result = purchase_search(session, query, page, per_page, month, category)
    return {
        "items": [
            {
                "id": record["line"].id,
                "date": record["date"],
                "month": record["month"],
                "month_label": record["month_label"],
                "invoice_number": record["invoice_number"],
                "product_id": record["product"].id if record["product"] else None,
                "product_name": record["product_name"],
                "raw_name": record["line"].raw_name,
                "quantity": record["quantity"],
                "unit_price": record["unit_price"],
                "discount_amount": record["discount_amount"],
                "net_unit_price": record["net_unit_price"],
                "net_amount": record["net_amount"],
                "is_corrected": record["is_corrected"],
            }
            for record in result["items"]
        ],
        "query": result["query"],
        "month": result["month"],
        "category": result["category"],
        "page": result["page"],
        "per_page": result["per_page"],
        "total": result["total"],
        "total_pages": result["total_pages"],
    }


def remove_category(
    category: str = Form(...),
    replacement: str = Form("待分類"),
    confirmed: bool = Form(False),
    session: Session = Depends(get_session),
    budget_policy: str = Form(""),
):
    try:
        moved = delete_category(session, category, replacement, confirmed, budget_policy)
    except BudgetMergeRequired as exc:
        raise HTTPException(409, {
            "message": str(exc), "budget_merge_required": True,
            "policies": ["keep_target", "sum"],
        }) from exc
    except RuntimeError as exc:
        raise HTTPException(409, {
            "message": f"分類「{category}」仍有 {exc} 個商品，是否移到「{replacement}」後刪除？",
            "product_count": int(str(exc)),
        }) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.commit()
    return {"ok": True, "moved_products": moved, "replacement": replacement}


def api_product_prices(product_id: int, session: Session = Depends(get_session)):
    prices = product_prices(session, product_id)
    if prices is None:
        raise HTTPException(404, "找不到商品")
    return {
        "product": prices["display_name"],
        "product_info": {
            "id": prices["product"].id,
            "display_name": prices["display_name"],
            "canonical_name": prices["product"].canonical_name,
            "alias_name": prices["product"].alias_name,
            "category": prices["product"].category,
            "size_value": prices["product"].size_value,
            "size_unit": prices["product"].size_unit,
        },
        "minimum": prices["minimum"],
        "maximum": prices["maximum"],
        "average": prices["average"],
        "latest": prices["latest"],
        "purchase_count": prices["purchase_count"],
        "unit_label": prices["unit_label"],
        "member_products": [
            {
                "id": product.id,
                "canonical_name": product.canonical_name,
                "display_name": product.display_name,
            }
            for product in prices["member_products"]
        ],
        "entries": [
            {
                "line_id": item["line"].id,
                "date": item["date"],
                "store": item["store_name"],
                "quantity": item["quantity"],
                "price": item["price"],
                "discount_amount": item["discount_amount"],
                "net_price": item["net_price"],
                "net_amount": item["net_amount"],
                "has_discount": item["has_discount"],
                "is_corrected": item["line"].correction is not None,
            }
            for item in prices["entries"]
        ],
        "chart": [
            {
                "date": item["date"],
                "store": item["store_name"],
                "price": item["price"],
            }
            for item in prices["chart"]
        ],
        "stores": [
            {
                "store": row["store_name"],
                "minimum": row["min"],
                "maximum": row["max"],
                "average": row["avg"],
                "count": row["count"],
            }
            for row in prices["stores"]
        ],
    }


def add_alias(product_id: int, alias: str = Form(...), session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "找不到商品")
    if not alias.strip():
        raise HTTPException(400, "請輸入商品名稱")
    try:
        source = assign_product_to_target(session, product, alias.strip())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    set_group_category(session, product, product.category)
    session.commit()
    return {"ok": True, "action": "assigned", "source_product": source.canonical_name}


def update_alias_name(product_id: int, alias_name: str = Form(""), session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "找不到商品")
    set_product_alias(session, product, alias_name)
    set_group_category(session, product, product.category)
    session.commit()
    return {"ok": True, "display_name": product.display_name}


def set_product_category(product_id: int, category: str = Form(...), session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "找不到商品")
    set_group_category(session, product, category)
    session.commit()
    return {"ok": True, "category": product.category}


def _serialize_category_rule(rule: CategoryRule) -> dict:
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "pattern": rule.pattern,
        "category": rule.category,
        "priority": rule.priority,
    }


def list_rules(rule_type: str = "item_keyword", session: Session = Depends(get_session)):
    if rule_type not in {"seller_tax_id", "item_keyword", "merchant_keyword", "product_name_exact"}:
        raise HTTPException(400, "不支援的規則類型")
    rules = session.scalars(
        select(CategoryRule)
        .where(CategoryRule.rule_type == rule_type)
        .order_by(CategoryRule.priority, CategoryRule.id)
    ).all()
    return {
        "items": [_serialize_category_rule(rule) for rule in rules],
        "built_in": [
            {"category": category, "keywords": list(keywords)}
            for category, keywords in DEFAULT_CATEGORY_RULES
        ] if rule_type == "item_keyword" else [],
    }


def _apply_keyword_rule_to_uncategorized(session: Session, pattern: str, category: str) -> int:
    normalized_pattern = normalize_text(pattern)
    matched_ids: set[int] = set()
    products = session.scalars(
        select(Product).where(Product.category == "待分類").order_by(Product.id)
    ).all()
    for product in products:
        if normalized_pattern in normalize_text(product.display_name):
            for member in set_group_category(session, product, category):
                matched_ids.add(member.id)
    return len(matched_ids)


def add_rule(
    rule_type: str = Form(...), pattern: str = Form(...), category: str = Form(...),
    priority: int = Form(100), apply_existing: bool = Form(True),
    session: Session = Depends(get_session),
):
    if rule_type not in {"seller_tax_id", "item_keyword", "merchant_keyword", "product_name_exact"}:
        raise HTTPException(400, "不支援的規則類型")
    pattern = pattern.strip()
    category = category.strip()
    if not pattern:
        raise HTTPException(400, "請輸入比對內容")
    if not category:
        raise HTTPException(400, "請選擇或輸入分類")
    if not 0 <= priority <= 1000:
        raise HTTPException(400, "優先順序必須介於 0 到 1000")
    duplicate = next((
        rule for rule in session.scalars(
            select(CategoryRule).where(CategoryRule.rule_type == rule_type)
        ).all()
        if normalize_text(rule.pattern) == normalize_text(pattern)
    ), None)
    if duplicate:
        raise HTTPException(409, f"規則「{pattern}」已存在")
    rule = CategoryRule(priority=priority, rule_type=rule_type, pattern=pattern, category=category)
    session.add(rule)
    session.flush()
    updated_products = (
        _apply_keyword_rule_to_uncategorized(session, pattern, category)
        if rule_type == "item_keyword" and apply_existing else 0
    )
    session.commit()
    return {"ok": True, "rule": _serialize_category_rule(rule), "updated_products": updated_products}


def delete_rule(rule_id: int, session: Session = Depends(get_session)):
    rule = session.get(CategoryRule, rule_id)
    if rule is None:
        raise HTTPException(404, "找不到分類規則")
    if rule.rule_type == "product_name_exact":
        raise HTTPException(400, "商品人工分類規則請從商品頁修改")
    session.delete(rule)
    session.commit()
    return {"ok": True}


async def api_login_preview(response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        preview = await crawler.login_preview()
    except Exception as exc:
        raise HTTPException(502, f"無法載入財政部登入畫面：{type(exc).__name__}") from exc
    return {
        "image": f"data:image/png;base64,{base64.b64encode(preview.screenshot).decode()}",
        "challenge_token": preview.challenge_token,
        "captcha_guess": preview.captcha_guess,
        "security_verification": preview.security_verification,
    }


async def api_login_interact(
    challenge_token: str = Form(...),
    x: float = Form(...),
    y: float = Form(...),
):
    try:
        preview = await crawler.interact(challenge_token, x, y)
    except LoginRequired as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "image": f"data:image/png;base64,{base64.b64encode(preview.screenshot).decode()}",
        "challenge_token": preview.challenge_token,
        "captcha_guess": preview.captcha_guess,
        "security_verification": preview.security_verification,
    }


def record_login_success(session: Session) -> None:
    completed_at = datetime.now(UTC).replace(tzinfo=None)
    session.add(SyncRun(
        months="login",
        status="completed",
        current_stage="completed",
        message="登入工作階段已更新",
        started_at=completed_at,
        finished_at=completed_at,
    ))
    session.commit()


async def api_login(
    challenge_token: str = Form(...),
    carrier_identifier: str = Form(...),
    password: str = Form(...),
    captcha: str = Form(...),
    session: Session = Depends(get_session),
):
    if not carrier_identifier.strip() or not password or not captcha.strip():
        raise HTTPException(400, "帳號、密碼與圖形驗證碼不可空白")
    if not re.fullmatch(r"09\d{8}", carrier_identifier.strip()):
        raise HTTPException(400, "請輸入申請手機條碼時登記的 10 碼手機號碼，不是 / 開頭的手機條碼")
    try:
        await crawler.login(challenge_token, carrier_identifier.strip(), password, captcha.strip())
    except LoginRequired as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"登入過程無法完成：{type(exc).__name__}") from exc
    record_login_success(session)
    return {"ok": True, "message": "登入工作階段已更新"}


app.include_router(build_dashboard_router(
    status_handler=api_status, dashboard_handler=api_dashboard,
))
app.include_router(build_discounts_router(
    list_handler=api_discounts, allocate_handler=api_allocate_discount, reset_handler=api_reset_discount,
))
app.include_router(build_purchases_router(
    list_handler=api_purchases, detail_handler=api_purchase_detail,
    update_handler=api_update_purchase, reset_handler=api_reset_purchase,
    months_handler=api_purchase_months,
))
app.include_router(build_products_router(
    list_handler=api_products, comparisons_handler=api_product_comparisons,
    prices_handler=api_product_prices, aliases_handler=add_alias,
    alias_name_handler=update_alias_name, category_handler=set_product_category,
    rules_list_handler=list_rules, rules_create_handler=add_rule, rules_delete_handler=delete_rule,
))
app.include_router(build_categories_router(
    list_handler=api_categories, usage_handler=api_category_usage, delete_handler=remove_category,
))
app.include_router(build_imports_router(csv_handler=api_import_csv))
app.include_router(build_auth_router(
    preview_handler=api_login_preview, interact_handler=api_login_interact, login_handler=api_login,
))


# Keep this catch-all mount last so API routes above win. Vue hash routing keeps
# Home Assistant Ingress reloads independent from the external path prefix.
app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True, check_dir=False), name="vue-root")
