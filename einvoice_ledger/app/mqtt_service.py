from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import NotificationEvent


class MqttPublisher:
    def __init__(self) -> None:
        options = {}
        try:
            with open(os.getenv("E_INVOICE_OPTIONS_FILE", "/data/options.json"), encoding="utf-8") as source:
                options = json.load(source)
        except (FileNotFoundError, OSError, ValueError):
            pass
        self.host = os.getenv("MQTT_HOST", options.get("mqtt_host", ""))
        self.port = int(os.getenv("MQTT_PORT", options.get("mqtt_port", 1883)))
        self.username = os.getenv("MQTT_USERNAME", options.get("mqtt_username", ""))
        self.password = os.getenv("MQTT_PASSWORD", options.get("mqtt_password", ""))
        self.connected = False
        self.error = "" if self.host else "尚未設定 MQTT broker"
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self.host)

    def connect(self) -> bool:
        if not self.configured:
            return False
        try:
            import paho.mqtt.client as mqtt

            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="einvoice-ledger")
            if self.username:
                client.username_pw_set(self.username, self.password)
            client.connect(self.host, self.port, keepalive=30)
            client.loop_start()
            self._client = client
            self.connected = True
            self.error = ""
            self.publish_discovery()
            return True
        except Exception as exc:
            self.connected = False
            self.error = type(exc).__name__
            return False

    def close(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        self.connected = False

    def _publish(self, topic: str, payload: dict | str, retain: bool = True) -> bool:
        if not self.connected or not self._client:
            return False
        try:
            body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
            result = self._client.publish(topic, body, qos=1, retain=retain)
            result.wait_for_publish(timeout=5)
            return result.rc == 0
        except Exception as exc:
            self.connected = False
            self.error = type(exc).__name__
            return False

    def publish_discovery(self) -> None:
        device = {"identifiers": ["einvoice_ledger"], "name": "發票記帳助手", "manufacturer": "Personal Home Assistant Apps"}
        sensors = {
            "last_sync": ("最近同步", None, None), "month_total": ("本月支出", "TWD", "monetary"),
            "budget_remaining": ("預算剩餘", "TWD", "monetary"), "uncategorized_count": ("待分類商品", "筆", None),
            "data_quality_issues": ("資料品質問題", "筆", None), "unallocated_discounts": ("未分攤折扣", "筆", None),
            "last_price_alert": ("最近低價提醒", None, None),
        }
        for key, (name, unit, device_class) in sensors.items():
            config = {
                "name": name, "unique_id": f"einvoice_{key}", "state_topic": f"einvoice/state/{key}",
                "value_template": "{{ value_json.value }}", "json_attributes_topic": f"einvoice/state/{key}",
                "device": device,
            }
            if unit:
                config["unit_of_measurement"] = unit
            if device_class:
                config["device_class"] = device_class
            self._publish(f"homeassistant/sensor/einvoice/{key}/config", config)
        for key, name in {"login_required": "需要重新登入", "sync_problem": "同步異常"}.items():
            self._publish(f"homeassistant/binary_sensor/einvoice/{key}/config", {
                "name": name, "unique_id": f"einvoice_{key}", "state_topic": f"einvoice/state/{key}",
                "value_template": "{{ value_json.value }}", "payload_on": "ON", "payload_off": "OFF", "device": device,
            })

    def publish_state(self, key: str, value, **attributes) -> bool:
        app_path = os.getenv("E_INVOICE_INGRESS_PATH", os.getenv("INGRESS_ENTRY", "/"))
        payload = {"value": value, "updated_at": datetime.now(UTC).isoformat(), "app_path": app_path, **attributes}
        return self._publish(f"einvoice/state/{key}", payload)

    def publish_pending_notifications(self, session: Session) -> int:
        if not self.connected:
            return 0
        events = session.scalars(
            select(NotificationEvent).where(NotificationEvent.published_at.is_(None)).order_by(NotificationEvent.id)
        ).all()
        count = 0
        for event in events:
            payload = {
                "event_id": event.id, "event_type": event.event_type,
                "title": event.title, "message": event.message,
                "value": event.value, "category": event.category,
                "updated_at": datetime.now(UTC).isoformat(),
                "app_path": os.getenv("E_INVOICE_INGRESS_PATH", os.getenv("INGRESS_ENTRY", "/")),
            }
            published = self._publish("einvoice/events", payload, retain=False)
            if published and event.event_type in {"target_price", "historical_low"}:
                self.publish_state("last_price_alert", event.title, event_type=event.event_type, message=event.message)
            if published:
                event.published_at = datetime.now(UTC).replace(tzinfo=None)
                count += 1
        session.commit()
        return count

    def status(self) -> dict:
        return {
            "configured": self.configured, "connected": self.connected, "error": self.error,
            "host": self.host if self.configured else "",
            "port": self.port,
        }


mqtt_publisher = MqttPublisher()
