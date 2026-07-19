from __future__ import annotations

import re

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .importer import DEFAULT_CATEGORY_RULES, clean_product_name, default_category_for_name, normalize_text
from .models import CategoryBudget, CategoryRule, InvoiceLine, InvoiceLineCorrection, PriceAlert, Product, ProductAlias


class BudgetMergeRequired(ValueError):
    pass


def is_junk_product_name(value: str) -> bool:
    cleaned = clean_product_name(value)
    return not cleaned or bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned))


def display_name(product: Product) -> str:
    return product.alias_name or product.canonical_name


def find_product_by_name(session: Session, name: str) -> Product | None:
    normalized = normalize_text(name)
    alias = session.scalar(select(ProductAlias).where(ProductAlias.normalized_name == normalized))
    if alias:
        return alias.product
    product = session.scalar(select(Product).where(Product.normalized_name == normalized))
    if product:
        return product
    return session.scalar(select(Product).where(Product.alias_name == name.strip()))


def set_product_alias(session: Session, product: Product, alias_name: str | None) -> None:
    old_display_name = display_name(product)
    product.alias_name = (alias_name or "").strip() or None
    new_display_name = display_name(product)
    if old_display_name != new_display_name:
        session.execute(
            update(Product)
            .where(Product.id != product.id, Product.alias_name == old_display_name)
            .values(alias_name=new_display_name, category=product.category)
        )


def assign_product_to_target(session: Session, target: Product, source_name: str) -> Product:
    source = find_product_by_name(session, source_name)
    if source is None:
        raise ValueError("找不到要歸類的既有商品名稱")
    if source.id == target.id:
        raise ValueError("不能將商品歸類到自己")
    source.alias_name = display_name(target)
    source.category = target.category
    source_alert = session.scalar(select(PriceAlert).where(PriceAlert.product_id == source.id))
    target_alert = session.scalar(select(PriceAlert).where(PriceAlert.product_id == target.id))
    if source_alert and target_alert:
        target_alert.notify_new_low = target_alert.notify_new_low or source_alert.notify_new_low
        target_alert.enabled = target_alert.enabled or source_alert.enabled
        if target_alert.target_price is None:
            target_alert.target_price = source_alert.target_price
        session.delete(source_alert)
    elif source_alert:
        source_alert.product_id = target.id
    return source


def category_options(session: Session) -> list[str]:
    categories = {
        value.strip()
        for value in (
            list(session.scalars(select(Product.category).distinct()).all())
            + list(session.scalars(select(CategoryRule.category).distinct()).all())
            + list(session.scalars(select(InvoiceLineCorrection.corrected_category).distinct()).all())
            + list(session.scalars(select(CategoryBudget.category).distinct()).all())
        )
        if value and value.strip()
    }
    categories.update(category for category, _ in DEFAULT_CATEGORY_RULES)
    return sorted(categories, key=lambda value: (value != "待分類", value.casefold()))


def category_usage(session: Session) -> list[dict]:
    protected = {"待分類", *(category for category, _ in DEFAULT_CATEGORY_RULES)}
    return [
        {
            "name": category,
            "product_count": session.scalar(
                select(func.count(Product.id)).where(Product.category == category)
            ) or 0,
            "deletable": category not in protected,
        }
        for category in category_options(session)
    ]


def delete_category(
    session: Session, category: str, replacement: str, confirmed: bool = False,
    budget_policy: str = "",
) -> int:
    category = category.strip()
    replacement = replacement.strip() or "待分類"
    protected = {"待分類", *(value for value, _ in DEFAULT_CATEGORY_RULES)}
    if not category or category in protected:
        raise ValueError("此分類是系統預設分類，不能刪除")
    if category == replacement:
        raise ValueError("替代分類不可與刪除分類相同")
    product_count = session.scalar(select(func.count(Product.id)).where(Product.category == category)) or 0
    if product_count and not confirmed:
        raise RuntimeError(str(product_count))
    source_budget = session.scalar(select(CategoryBudget).where(CategoryBudget.category == category))
    target_budget = session.scalar(select(CategoryBudget).where(CategoryBudget.category == replacement))
    if source_budget and target_budget:
        if budget_policy not in {"keep_target", "sum"}:
            raise BudgetMergeRequired("替代分類已有預算，請選擇保留替代分類或加總兩筆預算")
        if budget_policy == "sum":
            target_budget.monthly_limit += source_budget.monthly_limit
        session.delete(source_budget)
    elif source_budget:
        source_budget.category = replacement
    session.execute(update(Product).where(Product.category == category).values(category=replacement))
    session.execute(
        update(InvoiceLineCorrection)
        .where(InvoiceLineCorrection.corrected_category == category)
        .values(corrected_category=replacement)
    )
    session.execute(update(CategoryRule).where(CategoryRule.category == category).values(category=replacement))
    return product_count


def set_group_category(session: Session, product: Product, category: str) -> list[Product]:
    category = category.strip() or "待分類"
    group_name = normalize_text(display_name(product))
    members = [
        candidate for candidate in session.scalars(select(Product).order_by(Product.id)).all()
        if normalize_text(display_name(candidate)) == group_name
    ]
    member_ids = {member.id for member in members}
    for member in members:
        member.category = category
        rule = session.scalar(
            select(CategoryRule).where(
                CategoryRule.rule_type == "product_name_exact",
                CategoryRule.pattern == member.normalized_name,
            )
        )
        if rule:
            rule.category = category
            rule.priority = 0
        else:
            session.add(CategoryRule(
                priority=0,
                rule_type="product_name_exact",
                pattern=member.normalized_name,
                category=category,
            ))
    for correction in session.scalars(select(InvoiceLineCorrection)).all():
        effective_product_id = correction.corrected_product_id or correction.line.product_id
        if effective_product_id in member_ids:
            correction.corrected_category = category
    return members


def backfill_default_categories(session: Session) -> dict[str, int]:
    changed_products: set[int] = set()
    processed_groups: set[str] = set()
    products = session.scalars(select(Product).order_by(Product.id)).all()
    for product in products:
        group_key = normalize_text(display_name(product))
        if group_key in processed_groups:
            continue
        processed_groups.add(group_key)
        members = [candidate for candidate in products if normalize_text(display_name(candidate)) == group_key]
        existing = next((member.category for member in members if member.category != "待分類"), None)
        category = existing or default_category_for_name(display_name(product))
        if category == "待分類":
            continue
        before = {member.id: member.category for member in members}
        for member in set_group_category(session, product, category):
            if before.get(member.id) != category:
                changed_products.add(member.id)
    session.commit()
    return {"changed": len(changed_products)}


def normalize_existing_products(session: Session) -> dict[str, int]:
    aliased = 0
    hidden = 0
    for product in session.scalars(select(Product).order_by(Product.id)):
        if is_junk_product_name(product.canonical_name):
            session.execute(
                update(InvoiceLine).where(InvoiceLine.product_id == product.id).values(is_comparable=False)
            )
            hidden += 1
            continue
        cleaned = clean_product_name(product.canonical_name)
        if cleaned != product.canonical_name and not product.alias_name:
            product.alias_name = cleaned
            aliased += 1
    session.commit()
    return {"aliased": aliased, "hidden": hidden}
