from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session, aliased, joinedload

from .discounts import allocated_discount
from .models import DiscountAllocation, Invoice, InvoiceLine, InvoiceLineCorrection, Product, Store, SyncRun
from .product_manager import display_name, is_junk_product_name


def money(value: Decimal | None) -> str:
    return f"NT${(value or Decimal('0')):,.0f}"


def comparable_price(line: InvoiceLine) -> Decimal | None:
    """Return the manual comparison price when present, otherwise the CSV unit price."""
    value = line.correction.corrected_unit_price if line.correction and line.correction.corrected_unit_price is not None else line.unit_price
    if value is not None and value > 0:
        return value
    return None


def effective_product(line: InvoiceLine) -> Product | None:
    if line.correction and line.correction.corrected_product is not None:
        return line.correction.corrected_product
    return line.product


def effective_date(line: InvoiceLine) -> date:
    if line.correction and line.correction.corrected_date is not None:
        return line.correction.corrected_date
    return line.invoice.invoice_date


def effective_value(line: InvoiceLine, corrected_field: str, original_field: str):
    if line.correction:
        corrected = getattr(line.correction, corrected_field)
        if corrected is not None:
            return corrected
    return getattr(line, original_field)


def effective_store_name(line: InvoiceLine) -> str:
    if line.correction and line.correction.corrected_store_name:
        return line.correction.corrected_store_name
    return line.invoice.store.name


def effective_category(line: InvoiceLine) -> str:
    product = effective_product(line)
    if product:
        return product.category
    if line.correction and line.correction.corrected_category:
        return line.correction.corrected_category
    return "待分類"


def dashboard(session: Session) -> dict:
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    month_start = today.replace(day=1)
    lines = session.scalars(
        select(InvoiceLine)
        .options(
            joinedload(InvoiceLine.invoice).joinedload(Invoice.store),
            joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
            joinedload(InvoiceLine.discount_allocations),
            joinedload(InvoiceLine.received_discount_allocations),
        )
        .join(Invoice)
        .where(Invoice.is_void.is_(False))
    ).unique().all()
    current_lines = [line for line in lines if effective_date(line) >= month_start]
    total = sum((effective_value(line, "corrected_amount", "amount") for line in current_lines), Decimal("0"))
    uncategorized_lines = [
        line for line in current_lines if not line.is_discount and effective_category(line) == "待分類"
    ]
    uncategorized = sum(
        (
            effective_value(line, "corrected_amount", "amount") + allocated_discount(line)
            for line in uncategorized_lines
        ),
        Decimal("0"),
    )
    unallocated_discounts = [line for line in lines if line.is_discount and not line.discount_allocations]
    uncategorized_product_count = session.scalar(
        select(func.count(Product.id)).where(Product.category == "待分類")
    ) or 0
    last_run = session.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    return {
        "total": total,
        "uncategorized": uncategorized,
        "uncategorized_count": len(uncategorized_lines),
        "uncategorized_product_count": uncategorized_product_count,
        "unallocated_discount_count": len(unallocated_discounts),
        "unallocated_discount_total": abs(sum((line.amount for line in unallocated_discounts), Decimal("0"))),
        "last_run": last_run,
    }


def purchase_search(
    session: Session,
    query: str = "",
    page: int = 1,
    per_page: int = 50,
    month: str = "",
    category: str = "",
) -> dict:
    query = query.strip()
    month = month.strip()
    category = category.strip()
    per_page = per_page if per_page in {25, 50, 100} else 50
    corrected_product = aliased(Product)
    base = (
        select(InvoiceLine)
        .join(Invoice, InvoiceLine.invoice)
        .join(Store, Invoice.store)
        .outerjoin(Product, InvoiceLine.product)
        .outerjoin(InvoiceLineCorrection, InvoiceLine.correction)
        .outerjoin(corrected_product, InvoiceLineCorrection.corrected_product)
        .where(Invoice.is_void.is_(False), InvoiceLine.is_discount.is_(False))
    )
    if query:
        pattern = f"%{query}%"
        base = base.where(or_(
            InvoiceLine.raw_name.ilike(pattern),
            Product.canonical_name.ilike(pattern),
            Product.alias_name.ilike(pattern),
            corrected_product.canonical_name.ilike(pattern),
            corrected_product.alias_name.ilike(pattern),
            Store.name.ilike(pattern),
            Invoice.invoice_number.ilike(pattern),
            cast(Invoice.invoice_date, String).ilike(pattern),
            InvoiceLineCorrection.corrected_store_name.ilike(pattern),
            InvoiceLineCorrection.corrected_invoice_number.ilike(pattern),
            cast(InvoiceLineCorrection.corrected_date, String).ilike(pattern),
            InvoiceLineCorrection.corrected_category.ilike(pattern),
            InvoiceLineCorrection.note.ilike(pattern),
        ))
    if month:
        try:
            year, month_number = (int(part) for part in month.split("-", 1))
            month_start = date(year, month_number, 1)
            next_month = date(year + (month_number // 12), (month_number % 12) + 1, 1)
            effective_date_column = func.coalesce(InvoiceLineCorrection.corrected_date, Invoice.invoice_date)
            base = base.where(effective_date_column >= month_start, effective_date_column < next_month)
        except (TypeError, ValueError):
            month = ""
    if category:
        base = base.where(func.coalesce(corrected_product.category, Product.category) == category)

    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, page), total_pages)
    items = session.scalars(
        base.options(
            joinedload(InvoiceLine.invoice).joinedload(Invoice.store),
            joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
            joinedload(InvoiceLine.received_discount_allocations),
        )
        .order_by(func.coalesce(InvoiceLineCorrection.corrected_date, Invoice.invoice_date).desc(), InvoiceLine.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
    ).unique().all()
    records = []
    for line in items:
        quantity = effective_value(line, "corrected_quantity", "quantity")
        amount = effective_value(line, "corrected_amount", "amount")
        discount_amount = allocated_discount(line)
        net_amount = amount + discount_amount
        records.append({
            "line": line,
            "date": effective_date(line),
            "month": effective_date(line).strftime("%Y-%m"),
            "month_label": effective_date(line).strftime("%Y 年 %m 月"),
            "invoice_number": line.correction.corrected_invoice_number if line.correction and line.correction.corrected_invoice_number else line.invoice.invoice_number,
            "product": effective_product(line),
            "product_name": display_name(effective_product(line)) if effective_product(line) else line.raw_name,
            "store_name": effective_store_name(line),
            "category": effective_category(line),
            "quantity": quantity,
            "unit_price": comparable_price(line),
            "amount": amount,
            "discount_amount": discount_amount,
            "net_amount": net_amount,
            "net_unit_price": net_amount / quantity if quantity and quantity > 0 else None,
            "is_corrected": line.correction is not None,
        })
    return {
        "items": records,
        "query": query,
        "month": month,
        "category": category,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


def purchase_month_options(session: Session) -> list[str]:
    lines = session.scalars(
        select(InvoiceLine)
        .options(joinedload(InvoiceLine.invoice), joinedload(InvoiceLine.correction))
        .join(Invoice)
        .where(Invoice.is_void.is_(False), InvoiceLine.is_discount.is_(False))
    ).all()
    return sorted({effective_date(line).strftime("%Y-%m") for line in lines}, reverse=True)


def product_search(session: Session, query: str = "", category: str = "") -> list[Product]:
    products = session.scalars(select(Product).order_by(Product.id)).all()
    groups: dict[str, list[Product]] = {}
    for product in products:
        if not is_junk_product_name(product.canonical_name):
            groups.setdefault(display_name(product), []).append(product)

    needle = query.strip().casefold()
    category = category.strip()
    results: list[Product] = []
    for group_name, members in groups.items():
        if needle and not any(
            needle in member.canonical_name.casefold()
            or needle in (member.alias_name or "").casefold()
            for member in members
        ):
            continue
        representative = next(
            (member for member in members if member.canonical_name == group_name),
            min(members, key=lambda member: member.id),
        )
        if category and representative.category != category:
            continue
        results.append(representative)
    return sorted(results, key=lambda product: display_name(product).casefold())


def product_comparison_rows(session: Session, query: str = "", category: str = "") -> list[dict]:
    products = product_search(session, query, category)
    summaries: dict[str, list[tuple[date, int, Decimal]]] = {}
    lines = session.scalars(
        select(InvoiceLine)
        .options(
            joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
        )
        .join(Invoice)
        .where(InvoiceLine.is_comparable.is_(True), Invoice.is_void.is_(False))
    ).all()
    for line in lines:
        product = effective_product(line)
        price = comparable_price(line)
        if product and price is not None:
            summaries.setdefault(display_name(product), []).append((effective_date(line), line.id, price))
    rows = []
    for product in products:
        history = summaries.get(display_name(product), [])
        prices = [value[2] for value in history]
        latest_entry = max(history, key=lambda value: (value[0], value[1])) if history else None
        rows.append({
            "product": product,
            "purchase_count": len(prices),
            "minimum": min(prices) if prices else None,
            "maximum": max(prices) if prices else None,
            "latest": latest_entry[2] if latest_entry else None,
            "latest_date": latest_entry[0] if latest_entry else None,
        })
    return rows


def product_prices(session: Session, product_id: int) -> dict | None:
    product = session.get(Product, product_id)
    if product is None:
        return None
    effective_name = display_name(product)
    member_products = [
        candidate for candidate in session.scalars(select(Product).order_by(Product.id)).all()
        if display_name(candidate) == effective_name
    ]
    member_ids = [candidate.id for candidate in member_products]
    lines = session.scalars(
        select(InvoiceLine)
        .options(
            joinedload(InvoiceLine.invoice).joinedload(Invoice.store),
            joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
            joinedload(InvoiceLine.received_discount_allocations),
        )
        .join(Invoice)
        .outerjoin(InvoiceLineCorrection, InvoiceLine.correction)
        .where(
            or_(
                and_(InvoiceLineCorrection.corrected_product_id.is_(None), InvoiceLine.product_id.in_(member_ids)),
                InvoiceLineCorrection.corrected_product_id.in_(member_ids),
            ),
            InvoiceLine.is_comparable.is_(True),
            Invoice.is_void.is_(False),
        )
        .order_by(func.coalesce(InvoiceLineCorrection.corrected_date, Invoice.invoice_date).desc(), InvoiceLine.id.desc())
    ).unique().all()
    entries = []
    for line in lines:
        price = comparable_price(line)
        if price is None:
            continue
        quantity = effective_value(line, "corrected_quantity", "quantity")
        amount = effective_value(line, "corrected_amount", "amount")
        discount_amount = allocated_discount(line)
        entries.append({
            "date": effective_date(line), "store_name": effective_store_name(line), "line": line,
            "quantity": quantity,
            "amount": amount,
            "discount_amount": discount_amount,
            "net_amount": amount + discount_amount,
            "net_price": (amount + discount_amount) / quantity if quantity and quantity > 0 else None,
            "price": price, "has_discount": line.invoice.discount_total < 0,
        })
    prices = [entry["price"] for entry in entries]
    by_store: dict[str, list[Decimal]] = {}
    for entry in entries:
        by_store.setdefault(entry["store_name"], []).append(entry["price"])
    store_rows = [
        {"store_name": store_name, "min": min(values), "max": max(values), "avg": sum(values) / len(values), "count": len(values)}
        for store_name, values in by_store.items()
    ]
    store_rows.sort(key=lambda row: row["min"])
    return {
        "product": product,
        "display_name": effective_name,
        "member_products": member_products,
        "entries": entries[:20],
        "chart": list(reversed(entries))[-60:],
        "minimum": min(prices) if prices else None,
        "maximum": max(prices) if prices else None,
        "average": sum(prices) / len(prices) if prices else None,
        "latest": entries[0]["price"] if entries else None,
        "purchase_count": len(entries),
        "unit_label": "消費明細單價",
        "stores": store_rows,
    }


def status(session: Session) -> dict:
    last_run = session.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    return {
        "last_run": last_run,
        "login_required": bool(last_run and last_run.status == "login_required"),
        "now": datetime.now(UTC).replace(tzinfo=None),
    }
