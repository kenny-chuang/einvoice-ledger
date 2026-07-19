from app.database import SessionLocal, init_database
from app.importer import cleanup_zero_amount_data


init_database()
with SessionLocal() as session:
    result = cleanup_zero_amount_data(session)
    session.commit()
    print(f"deleted_lines={result['deleted_lines']} deleted_products={result['deleted_products']}")
