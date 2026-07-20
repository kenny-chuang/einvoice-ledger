import os
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter

from ..database import data_dir, engine
from ..mqtt_service import mqtt_publisher


router = APIRouter(prefix="/api/system", tags=["system"])
health_router = APIRouter(tags=["system"])


@health_router.get("/api/health")
def health_status():
    """Cheap liveness check that never launches Chromium or contacts the portal."""
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT 1")
    return {"status": "ok", "version": os.getenv("E_INVOICE_VERSION", "1.0.1")}


@router.get("")
def system_status():
    with engine.connect() as connection:
        database_version = MigrationContext.configure(connection).get_current_revision()
    backups = sorted((data_dir() / "backups").glob("*.db"), reverse=True) if (data_dir() / "backups").exists() else []
    return {
        "version": os.getenv("E_INVOICE_VERSION", "1.0.1"),
        "database_version": database_version,
        "mqtt": mqtt_publisher.status(),
        "diagnostic_retention_days": 7,
        "backups": [{"name": path.name, "size": path.stat().st_size} for path in backups[:5]],
    }
