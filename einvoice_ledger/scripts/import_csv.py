#!/usr/bin/env python3
"""Import one or more downloaded e-invoice CSV files into /data/einvoice.db."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, init_database
from app.importer import CsvImporter


def main(paths: list[str]) -> None:
    init_database()
    importer = CsvImporter()
    with SessionLocal() as session:
        for raw_path in paths:
            path = Path(raw_path)
            result = importer.import_bytes(session, path.read_bytes())
            print(f"{path.name}: {result.invoices_upserted} invoices, {result.lines_created} lines")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: import_csv.py FILE.csv [FILE.csv ...]")
    main(sys.argv[1:])
