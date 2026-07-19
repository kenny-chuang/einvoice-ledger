import asyncio
from decimal import Decimal
from datetime import date

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.importer import CsvImporter
from app.discounts import discount_rows
from app.main import (
    add_rule, api_allocate_discount, api_category_usage, api_dashboard, api_product_prices, api_purchase_detail,
    api_purchases, api_reset_discount, api_reset_purchase, api_update_purchase, import_csv_contents,
    delete_rule, list_rules, record_login_success, redirect_with_ingress, remove_category,
    serialize_discount_groups,
)
from app import main as main_module
from app.models import Invoice, InvoiceLine, Product, SyncRun
from sqlalchemy import func, select

from test_importer import CSV, DISCOUNT_RULE_CSV, make_session


def test_purchase_api_serializes_filters_and_editable_records():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)

    result = api_purchases(
        query="測試飲料",
        month="2026-05",
        category="水or飲料",
        page=1,
        per_page=25,
        session=session,
    )

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["per_page"] == 25
    assert result["items"][0]["product_name"] == "測試飲料500ml"
    assert result["items"][0]["unit_price"] in {40, 45}
    assert result["items"][0]["id"] > 0


def test_edit_redirect_keeps_home_assistant_ingress_prefix():
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/purchases/1/edit",
        "headers": [(b"x-ingress-path", b"/api/hassio_ingress/example")],
    })

    response = redirect_with_ingress(request, "/app/#/purchases?month=2026-07")

    assert response.headers["location"] == "/api/hassio_ingress/example/app/#/purchases?month=2026-07"


def test_product_detail_api_contains_price_store_and_management_data():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)
    product = session.scalar(select(Product).where(Product.canonical_name == "測試飲料500ml"))

    result = api_product_prices(product.id, session)

    assert result["product"] == "測試飲料500ml"
    assert result["product_info"]["display_name"] == "測試飲料500ml"
    assert result["product_info"]["canonical_name"] == "測試飲料500ml"
    assert result["purchase_count"] == 2
    assert result["minimum"] == 40
    assert result["maximum"] == 45
    assert len(result["entries"]) == 2
    assert len(result["chart"]) == 2
    assert {row["store"] for row in result["stores"]} == {"商店甲", "商店乙"}
    assert result["member_products"][0]["canonical_name"] == "測試飲料500ml"


def test_dashboard_api_and_manual_import_are_idempotent(monkeypatch):
    session = make_session()
    first = import_csv_contents(session, CSV)
    second = import_csv_contents(session, CSV)

    async def fake_session_is_valid():
        return True

    monkeypatch.setattr(main_module.crawler, "session_is_valid", fake_session_is_valid)

    summary = asyncio.run(api_dashboard(session))

    assert first.invoices_upserted == 3
    assert second.invoices_upserted == 3
    assert session.scalar(select(func.count()).select_from(Invoice)) == 3
    assert session.scalar(select(func.count()).select_from(SyncRun)) == 2
    assert summary["unallocated_discount_count"] == 1
    assert summary["uncategorized_product_count"] == 0
    assert summary["last_run"]["status"] == "completed"


def test_vue_discount_api_groups_invoices_and_supports_split_and_reset():
    session = make_session()
    CsvImporter().import_bytes(session, DISCOUNT_RULE_CSV)
    initial = serialize_discount_groups(session)

    assert initial["summary"]["unallocated_discount_count"] == 4
    assert initial["summary"]["unallocated_invoice_count"] == 3
    assert sum(len(month["invoices"]) for month in initial["unallocated"]) == 3

    generic = next(row for row in discount_rows(session) if row["discount"].raw_name == "任3件7折")
    target_ids = [candidate["line"].id for candidate in generic["candidates"]]
    result = api_allocate_discount(generic["discount"].id, target_ids, "manual", session)

    assert [item["amount"] for item in result["allocations"]] == [
        Decimal("-8.67"), Decimal("-8.67"), Decimal("-8.66")
    ]
    allocated = serialize_discount_groups(session)
    assert allocated["summary"]["allocated_invoice_count"] == 1
    assert allocated["summary"]["allocated_discount_count"] == 1

    api_reset_discount(generic["discount"].id, session)
    reset = serialize_discount_groups(session)
    assert reset["summary"]["allocated_discount_count"] == 0
    assert reset["summary"]["unallocated_invoice_count"] == 3


def test_vue_purchase_edit_api_updates_every_field_and_can_reset():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)
    line = session.scalar(
        select(InvoiceLine).join(Invoice).where(
            Invoice.invoice_number == "CD12345678",
            InvoiceLine.is_discount.is_(False),
        )
    )

    original = api_purchase_detail(line.id, session)
    updated = api_update_purchase(
        line.id,
        date(2026, 7, 20),
        "ZZ87654321",
        "人工修正商品",
        "人工修正商店",
        "水or飲料",
        "自訂飲品",
        "2",
        "20.5",
        "41",
        "已人工核對",
        session,
    )["purchase"]

    assert original["is_corrected"] is False
    assert updated["is_corrected"] is True
    assert updated["values"] == {
        "date": date(2026, 7, 20),
        "invoice_number": "ZZ87654321",
        "product_name": "人工修正商品",
        "store_name": "人工修正商店",
        "category": "自訂飲品",
        "quantity": Decimal("2"),
        "unit_price": Decimal("20.5"),
        "amount": Decimal("41"),
        "note": "已人工核對",
    }

    reset = api_reset_purchase(line.id, session)["purchase"]
    assert reset["is_corrected"] is False
    assert reset["values"]["invoice_number"] == "CD12345678"
    assert reset["values"]["product_name"] == "測試飲料500ml"
    assert reset["values"]["unit_price"] == Decimal("45")
    assert session.scalar(select(Product).where(Product.canonical_name == "人工修正商品")) is None


def test_vue_settings_reports_category_usage_and_confirms_product_move():
    session = make_session()
    product = Product(canonical_name="舊分類商品", normalized_name="舊分類商品", category="餐費")
    session.add(product)
    session.commit()

    usage = next(item for item in api_category_usage(session) if item["name"] == "餐費")
    assert usage == {"name": "餐費", "product_count": 1, "deletable": True}

    with pytest.raises(HTTPException) as conflict:
        remove_category("餐費", "餐點", False, session)
    assert conflict.value.status_code == 409

    result = remove_category("餐費", "餐點", True, session)
    assert result == {"ok": True, "moved_products": 1, "replacement": "餐點"}
    assert product.category == "餐點"


def test_vue_settings_can_add_apply_list_and_delete_keyword_rule():
    session = make_session()
    product = Product(canonical_name="百分百柳橙果汁", normalized_name="百分百柳橙果汁", category="待分類")
    session.add(product)
    session.commit()

    created = add_rule("item_keyword", "果汁", "水or飲料", 50, True, session)

    assert created["updated_products"] == 1
    assert product.category == "水or飲料"
    listed = list_rules("item_keyword", session)
    assert listed["items"] == [{
        "id": created["rule"]["id"],
        "rule_type": "item_keyword",
        "pattern": "果汁",
        "category": "水or飲料",
        "priority": 50,
    }]
    assert any(rule["category"] == "酒" and "啤酒" in rule["keywords"] for rule in listed["built_in"])

    with pytest.raises(HTTPException) as duplicate:
        add_rule("item_keyword", " 果汁 ", "水or飲料", 100, True, session)
    assert duplicate.value.status_code == 409

    assert delete_rule(created["rule"]["id"], session) == {"ok": True}
    assert list_rules("item_keyword", session)["items"] == []
    assert product.category == "水or飲料"


def test_login_success_creates_non_secret_status_record():
    session = make_session()
    record_login_success(session)

    run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    assert run.status == "completed"
    assert run.months == "login"
    assert run.message == "登入工作階段已更新"
    assert run.source_hash is None


def test_scheduled_sync_downloads_and_imports_csv(monkeypatch):
    session = make_session()

    async def fake_sync_months(months):
        assert len(months) == 2
        return [CSV.replace(b"202605", b"202607"), CSV.replace(b"202605", b"202606")]

    monkeypatch.setattr(main_module, "SessionLocal", lambda: session)
    monkeypatch.setattr(main_module.crawler, "sync_months", fake_sync_months)
    asyncio.run(main_module.scheduled_sync())

    run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()))
    assert run.status == "completed"
    assert "已下載並匯入 2 個月份" in run.message
    assert session.scalar(select(func.count()).select_from(Invoice)) == 6
