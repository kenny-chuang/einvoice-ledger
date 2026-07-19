from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..alert_service import acknowledge_notification, evaluate_price_alerts
from ..database import get_session
from ..models import NotificationEvent, PriceAlert, Product


router = APIRouter(tags=["alerts"])


class PriceAlertInput(BaseModel):
    target_price: Decimal | None = None
    notify_new_low: bool = True
    enabled: bool = True


def serialize_alert(value: PriceAlert) -> dict:
    return {
        "product_id": value.product_id,
        "target_price": value.target_price,
        "notify_new_low": value.notify_new_low,
        "enabled": value.enabled,
        "updated_at": value.updated_at,
    }


@router.get("/api/products/{product_id}/price-alert")
def get_price_alert(product_id: int, session: Session = Depends(get_session)):
    if session.get(Product, product_id) is None:
        raise HTTPException(404, "找不到商品")
    value = session.scalar(select(PriceAlert).where(PriceAlert.product_id == product_id))
    return None if value is None else serialize_alert(value)


@router.put("/api/products/{product_id}/price-alert")
def put_price_alert(
    product_id: int, payload: PriceAlertInput, session: Session = Depends(get_session),
):
    if session.get(Product, product_id) is None:
        raise HTTPException(404, "找不到商品")
    if payload.target_price is not None and payload.target_price < 0:
        raise HTTPException(400, "目標價不可小於零")
    value = session.scalar(select(PriceAlert).where(PriceAlert.product_id == product_id))
    if value is None:
        value = PriceAlert(product_id=product_id)
        session.add(value)
    value.target_price = payload.target_price
    value.notify_new_low = payload.notify_new_low
    value.enabled = payload.enabled
    session.flush()
    evaluate_price_alerts(session)
    session.commit()
    session.refresh(value)
    return serialize_alert(value)


@router.delete("/api/products/{product_id}/price-alert")
def delete_price_alert(product_id: int, session: Session = Depends(get_session)):
    value = session.scalar(select(PriceAlert).where(PriceAlert.product_id == product_id))
    if value is None:
        raise HTTPException(404, "找不到價格提醒")
    session.delete(value)
    session.commit()
    return {"ok": True}


@router.get("/api/notifications")
def get_notifications(
    acknowledged: bool | None = None, product_id: int | None = None, limit: int = 100,
    session: Session = Depends(get_session),
):
    statement = select(NotificationEvent).order_by(
        NotificationEvent.created_at.desc()
    ).limit(min(max(limit, 1), 500))
    if acknowledged is True:
        statement = statement.where(NotificationEvent.acknowledged_at.is_not(None))
    elif acknowledged is False:
        statement = statement.where(NotificationEvent.acknowledged_at.is_(None))
    if product_id is not None:
        statement = statement.where(NotificationEvent.product_id == product_id)
    events = session.scalars(statement).all()
    return [{
        "id": event.id, "event_type": event.event_type, "title": event.title,
        "message": event.message, "value": event.value, "category": event.category,
        "product_id": event.product_id, "invoice_line_id": event.invoice_line_id,
        "created_at": event.created_at, "published_at": event.published_at,
        "acknowledged_at": event.acknowledged_at,
    } for event in events]


@router.post("/api/notifications/{notification_id}/acknowledge")
def acknowledge(notification_id: int, session: Session = Depends(get_session)):
    value = acknowledge_notification(session, notification_id)
    if value is None:
        raise HTTPException(404, "找不到通知")
    return {"ok": True, "id": value.id}
