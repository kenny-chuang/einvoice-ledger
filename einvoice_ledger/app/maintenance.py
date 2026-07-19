from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .importer import normalize_tax_id
from .models import Invoice


def _line_signature(invoice: Invoice) -> tuple[tuple, ...]:
    values = [
        (line.normalized_name, line.quantity, line.unit_price, line.amount, line.is_discount)
        for line in invoice.lines
    ]
    return tuple(sorted(values, key=repr))


def remove_identical_duplicate_invoices(session: Session) -> dict[str, int]:
    groups = session.execute(
        select(Invoice.invoice_date, Invoice.invoice_number, func.count(Invoice.id))
        .group_by(Invoice.invoice_date, Invoice.invoice_number)
        .having(func.count(Invoice.id) > 1)
    ).all()
    removed = 0
    skipped = 0
    for invoice_date, invoice_number, _ in groups:
        invoices = session.scalars(
            select(Invoice)
            .where(Invoice.invoice_date == invoice_date, Invoice.invoice_number == invoice_number)
            .order_by(Invoice.id)
        ).all()
        signatures = {_line_signature(invoice) for invoice in invoices}
        if len(signatures) != 1:
            skipped += 1
            continue
        survivor = max(
            invoices,
            key=lambda invoice: (
                invoice.seller_tax_id == normalize_tax_id(invoice.seller_tax_id),
                sum(1 for line in invoice.lines if line.correction or line.discount_allocations),
                -invoice.id,
            ),
        )
        survivor.seller_tax_id = normalize_tax_id(survivor.seller_tax_id)
        for duplicate in invoices:
            if duplicate.id != survivor.id:
                session.delete(duplicate)
                removed += 1
    session.commit()
    return {"removed": removed, "skipped": skipped}
