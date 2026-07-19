from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import DiscountAllocation, Invoice, InvoiceLine, InvoiceLineCorrection, Store


def line_product_name(line: InvoiceLine) -> str:
    product = line.correction.corrected_product if line.correction and line.correction.corrected_product else line.product
    return product.display_name if product else line.raw_name


def allocated_discount(line: InvoiceLine) -> Decimal:
    return sum((allocation.amount for allocation in line.received_discount_allocations), Decimal("0"))


def discount_suggestion(discount_line: InvoiceLine, candidates: list[InvoiceLine]) -> tuple[InvoiceLine | None, str | None, str]:
    if len(candidates) == 1:
        return candidates[0], "single_item", "發票只有一個正數品項"
    if "啤酒" in discount_line.raw_name:
        beer_lines = [line for line in candidates if "啤酒" in line_product_name(line)]
        if len(beer_lines) == 1:
            return beer_lines[0], "beer_keyword", "折扣名稱包含啤酒，且發票只有一個啤酒品項"
    return None, None, "多種商品且折扣名稱不明確"


def discount_rows(session: Session) -> list[dict]:
    discounts = session.scalars(
        select(InvoiceLine)
        .options(
            joinedload(InvoiceLine.invoice).joinedload(Invoice.store),
            joinedload(InvoiceLine.invoice).joinedload(Invoice.lines).joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.invoice).joinedload(Invoice.lines).joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
            joinedload(InvoiceLine.discount_allocations).joinedload(DiscountAllocation.target_line).joinedload(InvoiceLine.product),
            joinedload(InvoiceLine.discount_allocations).joinedload(DiscountAllocation.target_line).joinedload(InvoiceLine.correction).joinedload(InvoiceLineCorrection.corrected_product),
        )
        .join(Invoice)
        .where(InvoiceLine.is_discount.is_(True), Invoice.is_void.is_(False))
        .order_by(Invoice.invoice_date.desc(), InvoiceLine.id.desc())
    ).unique().all()
    rows = []
    for discount in discounts:
        candidates = [
            line for line in discount.invoice.lines
            if not line.is_discount and line.amount > 0
        ]
        suggestion, method, reason = discount_suggestion(discount, candidates)
        allocations = sorted(discount.discount_allocations, key=lambda allocation: allocation.id)
        rows.append({
            "discount": discount,
            "candidates": [{"line": line, "name": line_product_name(line)} for line in candidates],
            "suggestion": suggestion,
            "suggestion_name": line_product_name(suggestion) if suggestion else None,
            "suggestion_method": method,
            "reason": reason,
            "allocations": allocations,
            "allocation": allocations[0] if allocations else None,
            "allocated_targets": [
                {"line": allocation.target_line, "name": line_product_name(allocation.target_line), "amount": allocation.amount}
                for allocation in allocations
            ],
        })
    rows.sort(key=lambda row: row["allocation"] is not None)
    return rows


def group_discount_rows(rows: list[dict]) -> dict[str, list[dict]]:
    invoice_groups: dict[int, dict] = {}
    for row in rows:
        invoice = row["discount"].invoice
        group = invoice_groups.setdefault(invoice.id, {"invoice": invoice, "discount_rows": []})
        group["discount_rows"].append(row)
    for group in invoice_groups.values():
        group["all_allocated"] = all(row["allocation"] for row in group["discount_rows"])
        group["discount_count"] = len(group["discount_rows"])
        group["total"] = abs(sum((row["discount"].amount for row in group["discount_rows"]), Decimal("0")))

    grouped: dict[str, dict[str, list[dict]]] = {"unallocated": {}, "allocated": {}}
    for invoice_group in invoice_groups.values():
        status = "allocated" if invoice_group["all_allocated"] else "unallocated"
        month = invoice_group["invoice"].invoice_date.strftime("%Y-%m")
        grouped[status].setdefault(month, []).append(invoice_group)
    result: dict[str, list[dict]] = {"unallocated": [], "allocated": []}
    for status, month_groups in grouped.items():
        for month in sorted(month_groups, reverse=True):
            month_invoices = sorted(
                month_groups[month], key=lambda group: (group["invoice"].invoice_date, group["invoice"].id), reverse=True
            )
            result[status].append({
                "month": month,
                "label": f"{month[:4]} 年 {month[5:]} 月",
                "invoices": month_invoices,
                "invoice_count": len(month_invoices),
                "discount_count": sum(group["discount_count"] for group in month_invoices),
                "total": sum((group["total"] for group in month_invoices), Decimal("0")),
            })
    return result


def allocate_discount(session: Session, discount_line_id: int, target_line_ids: list[int], method: str = "manual") -> list[DiscountAllocation]:
    discount = session.get(InvoiceLine, discount_line_id)
    if discount is None or not discount.is_discount or discount.amount >= 0:
        raise ValueError("找不到有效的折扣明細")
    unique_ids = list(dict.fromkeys(target_line_ids))
    if not unique_ids:
        raise ValueError("請至少選擇一個商品")
    targets = [session.get(InvoiceLine, target_id) for target_id in unique_ids]
    if any(target is None or target.is_discount or target.amount <= 0 for target in targets):
        raise ValueError("請選擇有效的正數商品")
    if any(target.invoice_id != discount.invoice_id for target in targets):
        raise ValueError("折扣只能分配給同一張發票的商品")
    for existing in list(discount.discount_allocations):
        session.delete(existing)

    count = len(targets)
    regular_share = (discount.amount / count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amounts = [regular_share] * (count - 1)
    amounts.append(discount.amount - sum(amounts, Decimal("0")))
    allocation_method = "equal_split" if count > 1 else method if method in {"single_item", "beer_keyword", "manual"} else "manual"
    allocations = [
        DiscountAllocation(
            discount_line_id=discount.id,
            target_line_id=target.id,
            amount=amount,
            method=allocation_method,
        )
        for target, amount in zip(targets, amounts)
    ]
    session.add_all(allocations)
    session.flush()
    return allocations


def reset_discount(session: Session, discount_line_id: int) -> None:
    discount = session.get(InvoiceLine, discount_line_id)
    if discount is None or not discount.is_discount:
        raise ValueError("找不到折扣明細")
    for allocation in list(discount.discount_allocations):
        session.delete(allocation)
