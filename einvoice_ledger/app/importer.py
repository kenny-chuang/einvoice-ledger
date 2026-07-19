from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CategoryRule, DataQualityIssue, DiscountAllocation, Invoice, InvoiceLine, InvoiceLineCorrection,
    Product, ProductAlias, Store,
)


SIZE_PATTERN = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|ml|l|公克|毫升|公升|入|包|罐)", re.IGNORECASE)
DEFAULT_CATEGORY_RULES = (
    ("酒", ("啤酒", "威士忌", "伏特加", "高粱")),
    ("交通", ("汽油", "無鉛")),
    ("餐點", ("飯", "麵", "三明治", "餐盒", "飯糰")),
    ("牛奶", ("乳",)),
    ("水or飲料", ("茶", "水", "可樂", "飲料")),
)
EXPECTED_HEADERS = (
    "載具自訂名稱", "發票日期", "發票號碼", "發票金額", "發票狀態", "折讓",
    "賣方統一編號", "賣方名稱", "賣方地址", "買方統編", "消費明細_數量",
    "消費明細_單價", "消費明細_金額", "消費明細_品名",
)


def validate_csv_month(contents: bytes, month: str) -> int:
    """Reject empty, structurally invalid, or wrong-month portal downloads."""
    if not re.fullmatch(r"\d{6}", month):
        raise ValueError("月份必須是 YYYYMM")
    reader = csv.reader(io.StringIO(contents.decode("utf-8-sig")))
    headers = tuple(value.strip() for value in next(reader, []))
    if headers != EXPECTED_HEADERS:
        raise ValueError("CSV 標題不符合財政部消費明細格式")
    count = 0
    for values in reader:
        if len(values) < 3 or not values[1].strip() or not values[2].strip():
            continue
        raw_date = values[1].strip()
        try:
            datetime.strptime(raw_date, "%Y%m%d")
        except ValueError as exc:
            raise ValueError("CSV 發票日期格式不正確") from exc
        if not raw_date.startswith(month):
            raise ValueError(f"下載內容月份 {raw_date[:6]} 與查詢月份 {month} 不一致")
        count += 1
    if count == 0:
        raise ValueError("CSV 沒有可匯入的發票明細")
    return count


def normalize_tax_id(value: str | None) -> str:
    value = (value or "").strip()
    return value.zfill(8) if value.isdigit() and len(value) <= 8 else value


def default_category_for_name(value: str) -> str:
    normalized = normalize_text(value)
    for category, keywords in DEFAULT_CATEGORY_RULES:
        if any(normalize_text(keyword) in normalized for keyword in keywords):
            return category
    return "待分類"


def clean_product_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"^\s*\([a-z]\)\s*\*?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^[\s\.。·•*＊_-]+", "", value)
    value = re.sub(r"(?<=\d)\s*cc(?=$|[^a-z0-9])", "ml", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().lower()
    return re.sub(r"\s+", " ", value)


def parse_decimal(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def parse_size(raw_name: str) -> tuple[Decimal | None, str | None]:
    match = SIZE_PATTERN.search(normalize_text(raw_name))
    if not match:
        return None, None
    value = Decimal(match.group("value"))
    unit = match.group("unit").lower()
    unit = {"公克": "g", "毫升": "ml", "公升": "l"}.get(unit, unit)
    if unit == "kg":
        return value * 1000, "g"
    if unit == "l":
        return value * 1000, "ml"
    return value, unit


@dataclass
class ImportResult:
    rows_read: int = 0
    data_rows: int = 0
    rows_repaired: int = 0
    quality_issues: int = 0
    skipped_rows: int = 0
    invoices_upserted: int = 0
    lines_created: int = 0
    discounts: int = 0
    void_invoices: int = 0
    pending_review_rows: int = 0
    positive_amount: Decimal = Decimal("0")
    discount_amount: Decimal = Decimal("0")
    source_hash: str = ""


def cleanup_zero_amount_data(session: Session) -> dict[str, int]:
    zero_lines = session.scalars(select(InvoiceLine).where(InvoiceLine.amount == 0)).all()
    for line in zero_lines:
        session.delete(line)
    session.flush()
    corrected_product_ids = set(session.scalars(
        select(InvoiceLineCorrection.corrected_product_id)
        .where(InvoiceLineCorrection.corrected_product_id.is_not(None))
    ).all())
    orphan_products = session.scalars(
        select(Product).where(~Product.lines.any())
    ).all()
    deleted_products = 0
    for product in orphan_products:
        if product.id not in corrected_product_ids:
            session.delete(product)
            deleted_products += 1
    session.flush()
    return {"deleted_lines": len(zero_lines), "deleted_products": deleted_products}


class CsvImporter:
    @staticmethod
    def _repair_row(headers: list[str], values: list[str]) -> tuple[list[str], str, str | None]:
        """Repair unquoted commas in seller addresses emitted by the portal.

        The portal schema has nine fields through seller address, followed by
        buyer tax id, quantity, unit price, amount and product name. When an
        address contains an unquoted comma, csv.reader returns extra columns.
        Lock the five-field suffix from the right and merge only the surplus
        middle fields back into seller address.
        """
        if len(values) == len(headers):
            return values, "high", None
        if len(headers) != 14:
            normalized = (values + [""] * len(headers))[:len(headers)]
            return normalized, "low", "unknown_schema_width"
        if tuple(headers) == EXPECTED_HEADERS and len(values) > len(headers):
            repaired = values[:8] + [",".join(values[8:-5])] + values[-5:]
            if len(repaired) == len(headers) and parse_decimal(repaired[-2]) is not None:
                return repaired, "high", "unquoted_seller_address_comma"
            return repaired[:len(headers)], "low", "ambiguous_column_shift"
        normalized = (values + [""] * len(headers))[:len(headers)]
        return normalized, "low", "column_count_mismatch"

    def import_bytes(
        self, session: Session, contents: bytes, *, commit: bool = True, sync_run_id: int | None = None
    ) -> ImportResult:
        result = ImportResult(source_hash=hashlib.sha256(contents).hexdigest())
        text = contents.decode("utf-8-sig")
        reader = csv.reader(io.StringIO(text))
        headers = [value.strip() for value in next(reader, [])]
        if tuple(headers) != EXPECTED_HEADERS:
            raise ValueError("CSV 標題不符合財政部消費明細格式")
        groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
        for values in reader:
            result.rows_read += 1
            original_values = list(values)
            values, confidence, repair_rule = self._repair_row(headers, values)
            if repair_rule:
                result.rows_repaired += 1
            row = dict(zip(headers, values))
            row["__quality_confidence"] = confidence
            row["__repair_rule"] = repair_rule or ""
            row["__raw_data"] = json.dumps(original_values, ensure_ascii=False)
            row["__sync_run_id"] = str(sync_run_id or "")
            if not row.get("發票日期") or not row.get("發票號碼"):
                result.skipped_rows += 1
                continue
            try:
                datetime.strptime(row["發票日期"], "%Y%m%d")
            except ValueError as exc:
                raise ValueError("CSV 發票日期格式不正確") from exc
            if not row.get("消費明細_金額", "").strip():
                result.skipped_rows += 1
                continue
            if parse_decimal(row.get("消費明細_金額")) is None:
                raise ValueError("CSV 消費明細金額格式不正確")
            invalid_numeric = [
                name for name in ("消費明細_數量", "消費明細_單價")
                if row.get(name, "").strip() and parse_decimal(row.get(name)) is None
            ]
            if invalid_numeric or not row.get("消費明細_品名", "").strip():
                row["__quality_confidence"] = "low"
                row["__repair_rule"] = (
                    "invalid_numeric_field" if invalid_numeric else "missing_product_name"
                )
                result.rows_repaired += 1
            result.data_rows += 1
            key = (row.get("載具自訂名稱", ""), row["發票日期"], row["發票號碼"])
            groups[key].append(row)

        if not groups:
            raise ValueError("CSV 沒有可匯入的發票明細")

        for identity, rows in groups.items():
            self._upsert_invoice(session, identity, rows, result)
        cleanup_zero_amount_data(session)
        if commit:
            session.commit()
        else:
            session.flush()
        return result

    def _get_store(self, session: Session, row: dict[str, str]) -> Store:
        tax_id = normalize_tax_id(row.get("賣方統一編號", ""))
        name = row.get("賣方名稱", "")
        address = row.get("賣方地址", "")
        store = session.scalar(select(Store).where(Store.seller_tax_id == tax_id, Store.name == name, Store.address == address))
        if store is None:
            store = Store(seller_tax_id=tax_id, name=name, address=address)
            session.add(store)
            session.flush()
        return store

    def _category_for(self, session: Session, raw_name: str, row: dict[str, str]) -> str:
        normalized_name = normalize_text(raw_name)
        seller_tax_id = row.get("賣方統一編號", "")
        merchant_name = normalize_text(row.get("賣方名稱", ""))
        rules = session.scalars(select(CategoryRule).order_by(CategoryRule.priority, CategoryRule.id)).all()
        for rule in rules:
            pattern = normalize_text(rule.pattern)
            if rule.rule_type == "seller_tax_id" and seller_tax_id == rule.pattern:
                return rule.category
            if rule.rule_type == "item_keyword" and pattern in normalized_name:
                return rule.category
            if rule.rule_type == "merchant_keyword" and pattern in merchant_name:
                return rule.category
            if rule.rule_type == "product_name_exact" and pattern == normalized_name:
                return rule.category
        return default_category_for_name(raw_name)

    def _resolve_product(self, session: Session, raw_name: str, row: dict[str, str]) -> Product:
        normalized = normalize_text(raw_name)
        alias = session.scalar(select(ProductAlias).where(ProductAlias.normalized_name == normalized))
        if alias:
            return alias.product
        for pending in session.new:
            if isinstance(pending, ProductAlias) and pending.normalized_name == normalized:
                return pending.product
        product = session.scalar(select(Product).where(Product.normalized_name == normalized))
        if product is None:
            size_value, size_unit = parse_size(raw_name)
            product = Product(
                canonical_name=raw_name, normalized_name=normalized, size_value=size_value, size_unit=size_unit,
                category=self._category_for(session, raw_name, row),
            )
            session.add(product)
            session.flush()
        session.add(ProductAlias(product_id=product.id, raw_name=raw_name, normalized_name=normalized))
        return product

    def _upsert_invoice(self, session: Session, identity: tuple[str, ...], rows: list[dict[str, str]], result: ImportResult) -> None:
        carrier, raw_date, number = identity
        first = rows[0]
        tax_id = normalize_tax_id(first.get("賣方統一編號", ""))
        status = next((row.get("發票狀態", "") for row in rows if "作廢" in row.get("發票狀態", "")), first.get("發票狀態", ""))
        invoice_date = datetime.strptime(raw_date, "%Y%m%d").date()
        invoice_amount = sum((parse_decimal(row.get("消費明細_金額")) or Decimal("0") for row in rows), Decimal("0"))
        store = self._get_store(session, first)
        invoice = session.scalar(
            select(Invoice).where(
                Invoice.carrier_name == carrier, Invoice.invoice_date == invoice_date,
                Invoice.invoice_number == number,
            )
        )
        saved_corrections: dict[tuple[str, Decimal | None], dict] = {}
        saved_allocations: list[dict] = []
        if invoice is None:
            invoice = Invoice(
                carrier_name=carrier, invoice_date=invoice_date, invoice_number=number,
                invoice_amount=invoice_amount, status=status, seller_tax_id=tax_id, store_id=store.id,
                is_void="作廢" in status,
            )
            session.add(invoice)
            session.flush()
        else:
            for old_line in invoice.lines:
                if old_line.correction:
                    correction = old_line.correction
                    saved_corrections[(old_line.normalized_name, old_line.unit_price)] = {
                        "corrected_date": correction.corrected_date,
                        "corrected_invoice_number": correction.corrected_invoice_number,
                        "corrected_product_id": correction.corrected_product_id,
                        "corrected_store_name": correction.corrected_store_name,
                        "corrected_category": correction.corrected_category,
                        "corrected_quantity": correction.corrected_quantity,
                        "corrected_unit_price": correction.corrected_unit_price,
                        "corrected_amount": correction.corrected_amount,
                        "note": correction.note,
                    }
                if old_line.is_discount:
                    for allocation in old_line.discount_allocations:
                        saved_allocations.append({
                            "discount_key": (old_line.normalized_name, old_line.unit_price),
                            "target_key": (allocation.target_line.normalized_name, allocation.target_line.unit_price),
                            "amount": allocation.amount,
                            "method": allocation.method,
                        })
            invoice.store_id = store.id
            invoice.seller_tax_id = tax_id
            invoice.invoice_amount = invoice_amount
            invoice.status = status
            invoice.is_void = "作廢" in status
            invoice.lines.clear()
            session.flush()

        aggregated: dict[tuple[str, Decimal | None], dict[str, Decimal | str | None]] = {}
        for row in rows:
            raw_name = (row.get("消費明細_品名") or "").strip()
            unit_price = parse_decimal(row.get("消費明細_單價"))
            amount = parse_decimal(row.get("消費明細_金額")) or Decimal("0")
            if amount == 0:
                continue
            quantity = parse_decimal(row.get("消費明細_數量"))
            key = (normalize_text(raw_name), unit_price)
            current = aggregated.setdefault(key, {
                "raw_name": raw_name, "quantity": Decimal("0"), "amount": Decimal("0"),
                "unit_price": unit_price, "unknown_quantity": False,
                "quality_confidence": row.get("__quality_confidence", "high"),
                "repair_rule": row.get("__repair_rule", ""), "raw_data": row.get("__raw_data", "[]"),
                "sync_run_id": row.get("__sync_run_id", ""),
            })
            if row.get("__quality_confidence") == "low":
                current["quality_confidence"] = "low"
                current["repair_rule"] = row.get("__repair_rule", "")
                current["raw_data"] = row.get("__raw_data", "[]")
            current["amount"] = Decimal(current["amount"]) + amount
            if quantity is None:
                current["unknown_quantity"] = True
            else:
                current["quantity"] = Decimal(current["quantity"]) + quantity

        discount_total = Decimal("0")
        rebuilt_lines: dict[tuple[str, Decimal | None], InvoiceLine] = {}
        for source_key, current in aggregated.items():
            raw_name = str(current["raw_name"])
            amount = Decimal(current["amount"])
            is_discount = amount < 0
            product = None if is_discount or not raw_name else self._resolve_product(session, raw_name, rows[0])
            quantity = None if current["unknown_quantity"] else Decimal(current["quantity"])
            confidence = str(current.get("quality_confidence") or "high")
            equation_mismatch = bool(
                not is_discount and quantity is not None and current["unit_price"] is not None
                and quantity * Decimal(current["unit_price"]) != amount
            )
            needs_review = confidence == "low" or equation_mismatch
            if needs_review:
                result.pending_review_rows += 1
            comparable = bool(
                product and not invoice.is_void and amount > 0
                and current["unit_price"] is not None and Decimal(current["unit_price"]) > 0
                and not needs_review
            )
            line = InvoiceLine(
                invoice_id=invoice.id, product_id=product.id if product else None, raw_name=raw_name,
                normalized_name=normalize_text(raw_name), quantity=quantity, unit_price=current["unit_price"],
                amount=amount, is_discount=is_discount, is_comparable=comparable,
                quality_confidence=confidence, needs_review=needs_review,
            )
            session.add(line)
            session.flush()
            repair_rule = str(current.get("repair_rule") or "")
            issue = None
            if repair_rule or equation_mismatch:
                issue = DataQualityIssue(
                    sync_run_id=int(current["sync_run_id"]) if str(current.get("sync_run_id") or "").isdigit() else None,
                    invoice_id=invoice.id, invoice_line_id=line.id,
                    issue_type="csv_repair" if repair_rule else "amount_equation_mismatch",
                    severity="review" if needs_review else "warning", confidence=confidence,
                    repair_rule=repair_rule or None, raw_data_json=str(current.get("raw_data") or "[]"),
                    message=(
                        f"已套用 CSV 修復規則：{repair_rule}" if repair_rule
                        else "數量 × 單價與品項金額不一致"
                    ),
                    status="open" if needs_review or equation_mismatch else "resolved",
                    resolved_at=None if needs_review or equation_mismatch else datetime.now(UTC).replace(tzinfo=None),
                )
                session.add(issue)
                result.quality_issues += 1
            rebuilt_lines[source_key] = line
            correction_values = saved_corrections.get((line.normalized_name, line.unit_price))
            if correction_values:
                line.correction = InvoiceLineCorrection(**correction_values)
                line.needs_review = False
                line.quality_confidence = "high"
                line.is_comparable = bool(
                    product and not invoice.is_void and amount > 0
                    and line.unit_price is not None and line.unit_price > 0
                )
                if issue:
                    issue.status = "resolved"
                    issue.resolved_at = datetime.now(UTC).replace(tzinfo=None)
            result.lines_created += 1
            if is_discount:
                discount_total += amount
                result.discounts += 1
                result.discount_amount += amount
            else:
                result.positive_amount += amount
        if saved_allocations:
            session.flush()
            for saved in saved_allocations:
                discount_line = rebuilt_lines.get(saved["discount_key"])
                target_line = rebuilt_lines.get(saved["target_key"])
                if discount_line and target_line:
                    session.add(DiscountAllocation(
                        discount_line_id=discount_line.id,
                        target_line_id=target_line.id,
                        amount=saved["amount"],
                        method=saved["method"],
                    ))
        invoice.discount_total = discount_total
        result.invoices_upserted += 1
        if invoice.is_void:
            result.void_invoices += 1
