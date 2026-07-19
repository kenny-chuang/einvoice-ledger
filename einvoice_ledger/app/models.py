from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("seller_tax_id", "name", "address", name="uq_store_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    seller_tax_id: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str] = mapped_column(String(500), default="")
    chain_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoices: Mapped[list[Invoice]] = relationship(back_populates="store")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    normalized_name: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    alias_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    size_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    size_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    category: Mapped[str] = mapped_column(String(100), default="待分類")
    aliases: Mapped[list[ProductAlias]] = relationship(back_populates="product", cascade="all, delete-orphan")
    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="product")

    @property
    def display_name(self) -> str:
        return self.alias_name or self.canonical_name


class ProductAlias(Base):
    __tablename__ = "product_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    raw_name: Mapped[str] = mapped_column(String(500), unique=True)
    normalized_name: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    product: Mapped[Product] = relationship(back_populates="aliases")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint(
            "carrier_name", "invoice_date", "invoice_number", "seller_tax_id", "status",
            name="uq_invoice_source_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    carrier_name: Mapped[str] = mapped_column(String(100), default="")
    invoice_date: Mapped[date] = mapped_column(Date, index=True)
    invoice_number: Mapped[str] = mapped_column(String(64), index=True)
    invoice_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    status: Mapped[str] = mapped_column(String(64))
    is_void: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    seller_tax_id: Mapped[str] = mapped_column(String(32), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"))
    discount_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    store: Mapped[Store] = relationship(back_populates="invoices")
    lines: Mapped[list[InvoiceLine]] = relationship(back_populates="invoice", cascade="all, delete-orphan")


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    raw_name: Mapped[str] = mapped_column(String(500))
    normalized_name: Mapped[str] = mapped_column(String(500), index=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    is_discount: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_comparable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    quality_confidence: Mapped[str] = mapped_column(String(16), default="high", index=True)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    invoice: Mapped[Invoice] = relationship(back_populates="lines")
    product: Mapped[Product | None] = relationship(back_populates="lines")
    correction: Mapped[InvoiceLineCorrection | None] = relationship(
        back_populates="line", cascade="all, delete-orphan", uselist=False
    )
    discount_allocations: Mapped[list[DiscountAllocation]] = relationship(
        foreign_keys="DiscountAllocation.discount_line_id", back_populates="discount_line", cascade="all, delete-orphan"
    )
    received_discount_allocations: Mapped[list[DiscountAllocation]] = relationship(
        foreign_keys="DiscountAllocation.target_line_id", back_populates="target_line", cascade="all, delete-orphan"
    )


class InvoiceLineCorrection(Base):
    __tablename__ = "invoice_line_corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_line_id: Mapped[int] = mapped_column(ForeignKey("invoice_lines.id"), unique=True, index=True)
    corrected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    corrected_invoice_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corrected_product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    corrected_store_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    corrected_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    corrected_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    corrected_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    corrected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    line: Mapped[InvoiceLine] = relationship(back_populates="correction")
    corrected_product: Mapped[Product | None] = relationship(foreign_keys=[corrected_product_id])


class DiscountAllocation(Base):
    __tablename__ = "discount_allocations"
    __table_args__ = (UniqueConstraint("discount_line_id", "target_line_id", name="uq_discount_target"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    discount_line_id: Mapped[int] = mapped_column(ForeignKey("invoice_lines.id"), index=True)
    target_line_id: Mapped[int] = mapped_column(ForeignKey("invoice_lines.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    method: Mapped[str] = mapped_column(String(32), default="manual")
    allocated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    discount_line: Mapped[InvoiceLine] = relationship(
        foreign_keys=[discount_line_id], back_populates="discount_allocations"
    )
    target_line: Mapped[InvoiceLine] = relationship(
        foreign_keys=[target_line_id], back_populates="received_discount_allocations"
    )


class CategoryRule(Base):
    __tablename__ = "category_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    rule_type: Mapped[str] = mapped_column(String(32))
    pattern: Mapped[str] = mapped_column(String(500))
    category: Mapped[str] = mapped_column(String(100))


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    months: Mapped[str] = mapped_column(String(32), default="")
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    current_stage: Mapped[str] = mapped_column(String(32), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    events: Mapped[list[SyncRunEvent]] = relationship(back_populates="sync_run", cascade="all, delete-orphan")


class SyncRunEvent(Base):
    __tablename__ = "sync_run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(32), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_run: Mapped[SyncRun] = relationship(back_populates="events")


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id", ondelete="SET NULL"), nullable=True, index=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id", ondelete="CASCADE"), nullable=True, index=True)
    invoice_line_id: Mapped[int | None] = mapped_column(ForeignKey("invoice_lines.id", ondelete="CASCADE"), nullable=True, index=True)
    issue_type: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning", index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="high", index=True)
    repair_rule: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_data_json: Mapped[str] = mapped_column(Text, default="{}")
    message: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CategoryBudget(Base):
    __tablename__ = "category_budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    start_month: Mapped[str] = mapped_column(String(7), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), unique=True, index=True)
    target_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    notify_new_low: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    product: Mapped[Product] = relationship()


class NotificationEvent(Base):
    __tablename__ = "notification_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=True, index=True)
    invoice_line_id: Mapped[int | None] = mapped_column(ForeignKey("invoice_lines.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
