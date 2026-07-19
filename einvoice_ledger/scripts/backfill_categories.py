from app.database import SessionLocal, init_database
from app.product_manager import backfill_default_categories


init_database()
with SessionLocal() as session:
    result = backfill_default_categories(session)
    print(f"changed={result['changed']}")
