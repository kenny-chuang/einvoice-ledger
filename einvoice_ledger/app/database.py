from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def data_dir() -> Path:
    path = Path(os.getenv("E_INVOICE_DATA_DIR", "./data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


DATABASE_URL = f"sqlite:///{data_dir() / 'einvoice.db'}"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(engine, "connect")
def configure_sqlite(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
    config.set_main_option("sqlalchemy.url", DATABASE_URL)
    return config


def backup_database(prefix: str = "einvoice-pre-v1") -> Path | None:
    source = data_dir() / "einvoice.db"
    if not source.exists() or source.stat().st_size == 0:
        return None
    backup_dir = data_dir() / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)
    os.chmod(target, 0o600)
    backups = sorted(backup_dir.glob(f"{prefix}-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for stale in backups[5:]:
        stale.unlink(missing_ok=True)
    return target


def init_database() -> None:
    config = _alembic_config()
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    inspector = inspect(engine)
    if not inspector.has_table("invoices"):
        Base.metadata.create_all(bind=engine)
        command.stamp(config, head)
        return
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        backup_database()
        command.upgrade(config, "head")


def get_session():
    session: Session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
