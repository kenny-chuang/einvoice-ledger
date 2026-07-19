from app.database import SessionLocal, init_database
from app.maintenance import remove_identical_duplicate_invoices


init_database()
with SessionLocal() as session:
    result = remove_identical_duplicate_invoices(session)
    print(f"removed={result['removed']} skipped={result['skipped']}")
