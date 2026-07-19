from pathlib import Path

from alembic.runtime.migration import MigrationContext
from fastapi import APIRouter

from ..database import data_dir, engine
from ..mqtt_service import mqtt_publisher


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("")
def system_status():
    with engine.connect() as connection:
        database_version = MigrationContext.configure(connection).get_current_revision()
    backups = sorted((data_dir() / "backups").glob("*.db"), reverse=True) if (data_dir() / "backups").exists() else []
    return {
        "version": "1.0.0",
        "database_version": database_version,
        "mqtt": mqtt_publisher.status(),
        "diagnostic_retention_days": 7,
        "backups": [{"name": path.name, "size": path.stat().st_size} for path in backups[:5]],
    }
