#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, init_database
from app.product_manager import normalize_existing_products


def main() -> None:
    init_database()
    with SessionLocal() as session:
        result = normalize_existing_products(session)
        print(f"aliased={result['aliased']} hidden={result['hidden']}")


if __name__ == "__main__":
    main()
