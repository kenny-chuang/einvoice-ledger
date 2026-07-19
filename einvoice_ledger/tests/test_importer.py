from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.discounts import allocate_discount, discount_rows, group_discount_rows
from app.importer import CsvImporter, clean_product_name, default_category_for_name, validate_csv_month
from app.models import Base, CategoryBudget, Invoice, InvoiceLine, InvoiceLineCorrection, Product
from app.product_manager import (
    BudgetMergeRequired, assign_product_to_target, backfill_default_categories, category_options, delete_category,
    normalize_existing_products, set_group_category, set_product_alias,
)
from app.services import (
    dashboard, effective_category, product_comparison_rows, product_prices, product_search, purchase_search,
)


CSV = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260501,AB12345678,40,開立已確認,否,123,商店甲,台北市,,1,40,40,測試飲料500ml
手機條碼,20260501,AB12345678,40,開立已確認,否,123,商店甲,台北市,,1,40,40,測試飲料500ml
手機條碼,20260501,AB12345678,-10,開立已確認,否,123,商店甲,台北市,,1,-10,-10,促銷折扣
手機條碼,20260502,CD12345678,45,開立已確認,否,456,商店乙,新北市,,1,45,45,測試飲料500ml
手機條碼,20260503,EF12345678,30,作廢已確認,否,789,商店丙,台中市,,1,30,30,測試飲料500ml
備註,,, ,,,,,,,,,,
""".encode()

VOID_UPDATE = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260502,CD12345678,45,作廢已確認,否,456,商店乙,新北市,,1,45,45,測試飲料500ml
""".encode()

MERGE_CSV = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260601,AA11111111,39,開立已確認,否,123,商店甲,台北市,,1,39,39,.麒麟霸啤酒500ml
手機條碼,20260602,BB22222222,42,開立已確認,否,456,商店乙,新北市,,1,42,42,(A)*麒麟霸啤酒500cc罐
手機條碼,20260603,CC33333333,0,開立已確認,否,789,商店丙,台中市,,,1,0,0
""".encode()

DISCOUNT_RULE_CSV = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260712,CY60801125,208,開立已確認,否,93784029,便利商店,新北市,,4,52,208,(A)*麒麟霸啤酒500cc罐
手機條碼,20260712,CY60801125,59,開立已確認,否,93784029,便利商店,新北市,,1,59,59,御料小館秘製滷雞翅
手機條碼,20260712,CY60801125,59,開立已確認,否,93784029,便利商店,新北市,,1,59,59,御料小館炸裂椒麻腿
手機條碼,20260712,CY60801125,-52,開立已確認,否,93784029,便利商店,新北市,,1,-52,-52,啤酒2件79折4件75折
手機條碼,20260712,CY60801125,-10,開立已確認,否,93784029,便利商店,新北市,,1,0,-10,活動聯促
手機條碼,20260713,CK03558742,78,開立已確認,否,28422184,全家便利商店,台北市,,2,39,78,FIN補給飲料975ml
手機條碼,20260713,CK03558742,-19,開立已確認,否,28422184,全家便利商店,台北市,,1,0,-19,飲料促
手機條碼,20260714,GG11111111,40,開立已確認,否,11111111,一般商店,台北市,,1,40,40,商品甲
手機條碼,20260714,GG11111111,60,開立已確認,否,11111111,一般商店,台北市,,1,60,60,商品乙
手機條碼,20260714,GG11111111,50,開立已確認,否,11111111,一般商店,台北市,,1,50,50,商品丙
手機條碼,20260714,GG11111111,-26,開立已確認,否,11111111,一般商店,台北市,,1,0,-26,任3件7折
""".encode()

UNQUOTED_ADDRESS_COMMA_CSV = """載具自訂名稱,發票日期,發票號碼,發票金額,發票狀態,折讓,賣方統一編號,賣方名稱,賣方地址,買方統編,消費明細_數量,消費明細_單價,消費明細_金額,消費明細_品名
手機條碼,20260718,CS50344761,46,開立已確認,否,28984353,全聯實業股份有限公司永和中正分公司,新北市永和區中正路;247,249號1樓,,1,46,46,義美酸梅湯
""".encode()


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_import_is_idempotent_and_aggregates_identical_lines():
    session = make_session()
    importer = CsvImporter()
    first = importer.import_bytes(session, CSV)
    second = importer.import_bytes(session, CSV)

    assert first.invoices_upserted == 3
    assert second.invoices_upserted == 3
    assert session.scalar(select(func.count()).select_from(Invoice)) == 3
    assert session.scalar(select(func.count()).select_from(InvoiceLine)) == 4

    invoice = session.scalar(select(Invoice).where(Invoice.invoice_number == "AB12345678"))
    assert invoice.invoice_amount == Decimal("70")
    assert invoice.discount_total == Decimal("-10")
    drink = session.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id, InvoiceLine.raw_name == "測試飲料500ml"))
    assert drink.quantity == Decimal("2")
    assert drink.amount == Decimal("80")


def test_tax_id_leading_zero_variants_do_not_duplicate_invoices():
    session = make_session()
    importer = CsvImporter()
    importer.import_bytes(session, CSV)
    variant = CSV.replace(b",123,", b",00000123,").replace(b",456,", b",00000456,").replace(b",789,", b",00000789,")
    importer.import_bytes(session, variant)

    assert session.scalar(select(func.count()).select_from(Invoice)) == 3


def test_unquoted_comma_in_seller_address_is_repaired_without_shifting_line_fields():
    session = make_session()
    result = CsvImporter().import_bytes(session, UNQUOTED_ADDRESS_COMMA_CSV)

    invoice = session.scalar(select(Invoice).where(Invoice.invoice_number == "CS50344761"))
    line = session.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))

    assert result.rows_repaired == 1
    assert result.quality_issues == 1
    assert invoice.store.address == "新北市永和區中正路;247,249號1樓"
    assert line.raw_name == "義美酸梅湯"
    assert line.quantity == Decimal("1")
    assert line.unit_price == Decimal("46")
    assert line.amount == Decimal("46")


def test_downloaded_csv_must_match_the_requested_month():
    assert validate_csv_month(CSV, "202605") == 5
    with pytest.raises(ValueError, match="月份"):
        validate_csv_month(CSV, "202606")


def test_prices_use_source_unit_price_and_exclude_void_and_discounts():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)
    product = session.scalar(select(Product).where(Product.canonical_name == "測試飲料500ml"))
    summary = product_prices(session, product.id)

    assert summary["minimum"] == Decimal("40")
    assert summary["maximum"] == Decimal("45")
    assert summary["unit_label"] == "消費明細單價"
    assert len(summary["entries"]) == 2
    assert summary["purchase_count"] == 2

    comparison = product_comparison_rows(session, "測試飲料")
    assert comparison[0]["purchase_count"] == 2
    assert comparison[0]["minimum"] == Decimal("40")
    assert comparison[0]["maximum"] == Decimal("45")


def test_later_void_update_replaces_the_existing_invoice():
    session = make_session()
    importer = CsvImporter()
    importer.import_bytes(session, CSV)
    importer.import_bytes(session, VOID_UPDATE)

    assert session.scalar(select(func.count()).select_from(Invoice)) == 3
    updated = session.scalar(select(Invoice).where(Invoice.invoice_number == "CD12345678"))
    assert updated.is_void is True


def test_product_name_cleanup_and_manual_alias_grouping_preserves_history():
    assert clean_product_name(".台灣啤酒500ml") == "台灣啤酒500ml"
    assert clean_product_name("(A)*麒麟霸啤酒500cc罐") == "麒麟霸啤酒500ml罐"

    session = make_session()
    CsvImporter().import_bytes(session, MERGE_CSV)
    original = session.scalar(select(Product).where(Product.canonical_name == ".麒麟霸啤酒500ml"))
    assert original.alias_name is None
    assert original.display_name == ".麒麟霸啤酒500ml"
    normalize_existing_products(session)

    junk = session.scalar(select(Product).where(Product.canonical_name == "0"))
    assert junk is None
    target = session.scalar(select(Product).where(Product.canonical_name == ".麒麟霸啤酒500ml"))
    source = session.scalar(select(Product).where(Product.canonical_name == "(A)*麒麟霸啤酒500cc罐"))
    assert target.alias_name == "麒麟霸啤酒500ml"
    assert source.alias_name == "麒麟霸啤酒500ml罐"

    set_product_alias(session, target, "麒麟霸啤酒500ml")
    assigned = assign_product_to_target(session, target, source.canonical_name)
    session.commit()

    assert assigned.id == source.id
    assert source.alias_name == "麒麟霸啤酒500ml"
    assert session.scalar(select(func.count()).select_from(Product)) == 2
    assert session.scalar(select(func.count()).select_from(InvoiceLine).where(InvoiceLine.product_id == target.id)) == 1
    assert session.scalar(select(func.count()).select_from(InvoiceLine).where(InvoiceLine.product_id == source.id)) == 1

    source_line = session.scalar(select(InvoiceLine).where(InvoiceLine.product_id == source.id))
    source_line.correction = InvoiceLineCorrection(corrected_category="錯誤分類")
    set_group_category(session, target, "酒")
    session.commit()
    assert target.category == "酒"
    assert source.category == "酒"
    assert source_line.correction.corrected_category == "酒"
    assert "酒" in category_options(session)
    source_line.correction.corrected_category = "不應覆蓋商品分類"
    assert effective_category(source_line) == "酒"

    summary = product_prices(session, target.id)
    assert summary["minimum"] == Decimal("39")
    assert summary["maximum"] == Decimal("42")
    assert len(summary["entries"]) == 2


def test_product_list_is_not_limited_to_first_100_items():
    session = make_session()
    for index in range(105):
        session.add(Product(canonical_name=f"商品{index:03d}", normalized_name=f"商品{index:03d}"))
    session.commit()

    assert len(product_search(session)) == 105


def test_product_comparison_rows_support_query_and_category_filters():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)

    matching = product_comparison_rows(session, query="測試飲料", category="水or飲料")
    wrong_category = product_comparison_rows(session, query="測試飲料", category="餐點")

    assert len(matching) == 1
    assert matching[0]["purchase_count"] == 2
    assert matching[0]["minimum"] == Decimal("40")
    assert matching[0]["maximum"] == Decimal("45")
    assert wrong_category == []


def test_category_grouping_normalizes_full_width_and_case():
    session = make_session()
    first = Product(canonical_name="來源商品一", normalized_name="來源商品一", alias_name="ＡＢＣ飲料")
    second = Product(canonical_name="來源商品二", normalized_name="來源商品二", alias_name="abc飲料")
    session.add_all([first, second])
    session.commit()

    set_group_category(session, first, "飲料")
    session.commit()

    assert first.category == "飲料"
    assert second.category == "飲料"


def test_deleting_used_category_requires_confirmation_and_moves_products():
    session = make_session()
    product = Product(canonical_name="便當", normalized_name="便當", category="餐費")
    session.add(product)
    session.commit()

    with pytest.raises(RuntimeError):
        delete_category(session, "餐費", "餐點", confirmed=False)
    moved = delete_category(session, "餐費", "餐點", confirmed=True)
    session.commit()

    assert moved == 1
    assert product.category == "餐點"


def test_deleting_category_requires_explicit_budget_merge_policy():
    session = make_session()
    session.add_all([
        CategoryBudget(category="餐費", monthly_limit=Decimal("1000")),
        CategoryBudget(category="餐點", monthly_limit=Decimal("2000")),
    ])
    session.commit()

    with pytest.raises(BudgetMergeRequired):
        delete_category(session, "餐費", "餐點", confirmed=True)
    delete_category(session, "餐費", "餐點", confirmed=True, budget_policy="sum")
    session.commit()

    target = session.scalar(select(CategoryBudget).where(CategoryBudget.category == "餐點"))
    assert target.monthly_limit == Decimal("3000")
    assert session.scalar(select(func.count()).select_from(CategoryBudget)) == 1


def test_default_keyword_categories_and_existing_backfill():
    assert default_category_for_name("金牌台灣啤酒500ml") == "酒"
    assert default_category_for_name("蘇格蘭威士忌") == "酒"
    assert default_category_for_name("伏特加調酒") == "酒"
    assert default_category_for_name("金門高粱酒") == "酒"
    assert default_category_for_name("雞肉餐盒") == "餐點"
    assert default_category_for_name("鮮乳飲料") == "牛奶"
    assert default_category_for_name("95無鉛汽油") == "交通"
    assert default_category_for_name("無糖可樂") == "水or飲料"
    assert default_category_for_name("一般商品") == "待分類"

    session = make_session()
    meal = Product(canonical_name="雞肉飯糰", normalized_name="雞肉飯糰")
    milk = Product(canonical_name="鮮乳茶", normalized_name="鮮乳茶")
    alcohol = Product(canonical_name="麒麟霸啤酒500ml", normalized_name="麒麟霸啤酒500ml")
    manual = Product(canonical_name="瓶裝水", normalized_name="瓶裝水", category="自訂分類")
    session.add_all([meal, milk, alcohol, manual])
    session.commit()
    result = backfill_default_categories(session)

    assert result["changed"] == 3
    assert meal.category == "餐點"
    assert milk.category == "牛奶"
    assert alcohol.category == "酒"
    assert manual.category == "自訂分類"
    assert {"酒", "餐點", "牛奶", "交通", "水or飲料"}.issubset(set(category_options(session)))


def test_purchase_search_finds_records_by_product_store_and_invoice():
    session = make_session()
    CsvImporter().import_bytes(session, CSV)

    product_result = purchase_search(session, "測試飲料", page=1, per_page=25)
    store_result = purchase_search(session, "商店乙")
    invoice_result = purchase_search(session, "AB12345678")
    month_result = purchase_search(session, month="2026-05")
    category_result = purchase_search(session, category="水or飲料")

    assert product_result["total"] == 2
    assert len(product_result["items"]) == 2
    assert store_result["total"] == 1
    assert invoice_result["total"] == 1
    assert month_result["total"] == 2
    assert category_result["total"] == 2
    assert product_search(session, category="水or飲料")[0].canonical_name == "測試飲料500ml"
    assert dashboard(session)["unallocated_discount_count"] == 1


def test_single_purchase_correction_drives_search_and_prices_and_survives_reimport():
    session = make_session()
    importer = CsvImporter()
    importer.import_bytes(session, CSV)
    invoice = session.scalar(select(Invoice).where(Invoice.invoice_number == "AB12345678"))
    line = session.scalar(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id, InvoiceLine.is_discount.is_(False)))
    corrected_product = Product(canonical_name="正確商品", normalized_name="正確商品", category="飲料")
    session.add(corrected_product)
    session.flush()
    line.correction = InvoiceLineCorrection(
        corrected_date=date(2026, 7, 2),
        corrected_invoice_number="ZZ99999999",
        corrected_product_id=corrected_product.id,
        corrected_store_name="正確商店",
        corrected_category="飲料",
        corrected_quantity=Decimal("1"),
        corrected_unit_price=Decimal("20"),
        corrected_amount=Decimal("20"),
        note="人工核對",
    )
    session.commit()

    result = purchase_search(session, "正確商店")
    assert result["total"] == 1
    assert result["items"][0]["product_name"] == "正確商品"
    assert result["items"][0]["unit_price"] == Decimal("20")
    assert product_prices(session, corrected_product.id)["minimum"] == Decimal("20")

    importer.import_bytes(session, CSV)
    refreshed = session.scalar(
        select(InvoiceLine).join(Invoice).where(Invoice.invoice_number == "AB12345678", InvoiceLine.is_discount.is_(False))
    )
    assert refreshed.raw_name == "測試飲料500ml"
    assert refreshed.unit_price == Decimal("40")
    assert refreshed.correction.corrected_product_id == corrected_product.id
    assert refreshed.correction.corrected_unit_price == Decimal("20")


def test_discount_rules_require_confirmation_and_allocation_survives_reimport():
    session = make_session()
    importer = CsvImporter()
    importer.import_bytes(session, DISCOUNT_RULE_CSV)
    rows = {row["discount"].raw_name: row for row in discount_rows(session)}

    assert rows["啤酒2件79折4件75折"]["suggestion"].raw_name == "(A)*麒麟霸啤酒500cc罐"
    assert rows["啤酒2件79折4件75折"]["suggestion_method"] == "beer_keyword"
    assert rows["飲料促"]["suggestion"].raw_name == "FIN補給飲料975ml"
    assert rows["飲料促"]["suggestion_method"] == "single_item"
    assert rows["任3件7折"]["suggestion"] is None

    beer_discount = rows["啤酒2件79折4件75折"]["discount"]
    beer_target = rows["啤酒2件79折4件75折"]["suggestion"]
    allocate_discount(session, beer_discount.id, [beer_target.id], "beer_keyword")
    session.commit()

    mixed_groups = group_discount_rows(discount_rows(session))
    beer_invoice_groups = [
        invoice_group
        for month_group in mixed_groups["unallocated"]
        for invoice_group in month_group["invoices"]
        if invoice_group["invoice"].invoice_number == "CY60801125"
    ]
    assert len(beer_invoice_groups) == 1
    assert beer_invoice_groups[0]["discount_count"] == 2

    rows = {row["discount"].raw_name: row for row in discount_rows(session)}
    activity = rows["活動聯促"]
    activity_target = next(candidate["line"] for candidate in activity["candidates"] if candidate["line"].id != beer_target.id)
    allocate_discount(session, activity["discount"].id, [activity_target.id], "manual")
    generic = rows["任3件7折"]
    split_allocations = allocate_discount(
        session,
        generic["discount"].id,
        [candidate["line"].id for candidate in generic["candidates"]],
        "manual",
    )
    session.commit()

    sorted_rows = discount_rows(session)
    first_allocated_index = next(index for index, row in enumerate(sorted_rows) if row["allocation"] is not None)
    assert all(row["allocation"] is None for row in sorted_rows[:first_allocated_index])
    assert all(row["allocation"] is not None for row in sorted_rows[first_allocated_index:])

    assert [allocation.amount for allocation in split_allocations] == [
        Decimal("-8.67"), Decimal("-8.67"), Decimal("-8.66")
    ]
    assert sum((allocation.amount for allocation in split_allocations), Decimal("0")) == Decimal("-26")

    grouped = group_discount_rows(discount_rows(session))
    assert grouped["allocated"][0]["month"] == "2026-07"
    assert grouped["allocated"][0]["invoice_count"] == 2
    assert grouped["allocated"][0]["discount_count"] == 3
    assert grouped["unallocated"][0]["invoice_count"] == 1

    record = purchase_search(session, "麒麟霸啤酒")["items"][0]
    assert record["discount_amount"] == Decimal("-52")
    assert record["net_amount"] == Decimal("156")
    assert record["net_unit_price"] == Decimal("39")

    importer.import_bytes(session, DISCOUNT_RULE_CSV)
    refreshed = {row["discount"].raw_name: row for row in discount_rows(session)}["啤酒2件79折4件75折"]
    assert refreshed["allocation"] is not None
    assert refreshed["allocated_targets"][0]["line"].raw_name == "(A)*麒麟霸啤酒500cc罐"
