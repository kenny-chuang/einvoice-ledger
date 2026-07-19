from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from .models import Invoice, InvoiceLine, InvoiceLineCorrection, NotificationEvent, PriceAlert, Product
from .services import effective_date


def evaluate_price_alerts(session: Session) -> int:
    created = 0
    alerts = session.scalars(
        select(PriceAlert).options(joinedload(PriceAlert.product)).where(PriceAlert.enabled.is_(True))
    ).all()
    for alert in alerts:
        member_ids = [
            product.id for product in session.scalars(select(Product)).all()
            if product.display_name == alert.product.display_name
        ]
        lines = session.scalars(
            select(InvoiceLine).join(Invoice)
            .outerjoin(InvoiceLineCorrection, InvoiceLine.correction)
            .options(joinedload(InvoiceLine.invoice), joinedload(InvoiceLine.correction))
            .where(
                or_(
                    and_(InvoiceLineCorrection.corrected_product_id.is_(None), InvoiceLine.product_id.in_(member_ids)),
                    InvoiceLineCorrection.corrected_product_id.in_(member_ids),
                ),
                InvoiceLine.is_comparable.is_(True),
                InvoiceLine.needs_review.is_(False), InvoiceLine.unit_price.is_not(None),
                Invoice.is_void.is_(False),
            )
        ).all()
        lines.sort(key=lambda line: (effective_date(line), line.id))
        if not lines:
            continue
        latest = lines[-1]
        price = Decimal(latest.unit_price)
        identity = f"{latest.invoice.invoice_date}:{latest.invoice.invoice_number}:{alert.product_id}:{price}"
        triggers: list[tuple[str, str]] = []
        if alert.target_price is not None and price <= alert.target_price:
            triggers.append(("target_price", f"已低於目標價 NT${alert.target_price}"))
        prior = [Decimal(line.unit_price) for line in lines[:-1] if line.unit_price is not None]
        if alert.notify_new_low and prior and price < min(prior):
            triggers.append(("historical_low", f"刷新歷史最低價，過去最低 NT${min(prior)}"))
        for event_type, reason in triggers:
            key = f"price:{event_type}:{identity}"
            if session.scalar(select(NotificationEvent).where(NotificationEvent.dedupe_key == key)):
                continue
            session.add(NotificationEvent(
                dedupe_key=key, event_type=event_type, product_id=alert.product_id,
                invoice_line_id=latest.id, title=f"{alert.product.display_name} NT${price}",
                message=f"{latest.invoice.invoice_date}：{reason}", value=price,
            ))
            created += 1
    session.flush()
    return created


def acknowledge_notification(session: Session, notification_id: int) -> NotificationEvent | None:
    event = session.get(NotificationEvent, notification_id)
    if event:
        event.acknowledged_at = datetime.now(UTC).replace(tzinfo=None)
        session.commit()
    return event
