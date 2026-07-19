"""發票記帳助手 1.0 schema enhancements."""

from alembic import op
import sqlalchemy as sa

revision = "0001_v1_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_lines") as batch:
        batch.add_column(sa.Column("quality_confidence", sa.String(16), nullable=False, server_default="high"))
        batch.add_column(sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.create_index("ix_invoice_lines_quality_confidence", ["quality_confidence"])
        batch.create_index("ix_invoice_lines_needs_review", ["needs_review"])
    with op.batch_alter_table("sync_runs") as batch:
        batch.add_column(sa.Column("current_stage", sa.String(32), nullable=False, server_default="queued"))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("stats_json", sa.Text(), nullable=False, server_default="{}"))
    op.execute("UPDATE sync_runs SET current_stage = status")

    op.create_table("sync_run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("sync_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False), sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"), sa.Column("error_code", sa.String(64)),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()))
    op.create_index("ix_sync_run_events_sync_run_id", "sync_run_events", ["sync_run_id"])
    op.create_index("ix_sync_run_events_stage", "sync_run_events", ["stage"])

    op.create_table("data_quality_issues",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("sync_run_id", sa.Integer(), sa.ForeignKey("sync_runs.id", ondelete="SET NULL")),
        sa.Column("invoice_id", sa.Integer(), sa.ForeignKey("invoices.id", ondelete="CASCADE")),
        sa.Column("invoice_line_id", sa.Integer(), sa.ForeignKey("invoice_lines.id", ondelete="CASCADE")),
        sa.Column("issue_type", sa.String(64), nullable=False), sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("confidence", sa.String(16), nullable=False, server_default="high"), sa.Column("repair_rule", sa.String(128)),
        sa.Column("raw_data_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime()))
    for column in ("sync_run_id", "invoice_id", "invoice_line_id", "issue_type", "severity", "confidence", "status"):
        op.create_index(f"ix_data_quality_issues_{column}", "data_quality_issues", [column])

    op.create_table("category_budgets",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("category", sa.String(100), nullable=False, unique=True),
        sa.Column("monthly_limit", sa.Numeric(14, 2), nullable=False), sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_month", sa.String(7), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_category_budgets_category", "category_budgets", ["category"], unique=True)
    op.create_index("ix_category_budgets_active", "category_budgets", ["active"])

    op.create_table("price_alerts",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("target_price", sa.Numeric(14, 3)), sa.Column("notify_new_low", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_price_alerts_product_id", "price_alerts", ["product_id"], unique=True)
    op.create_index("ix_price_alerts_enabled", "price_alerts", ["enabled"])

    op.create_table("notification_events",
        sa.Column("id", sa.Integer(), primary_key=True), sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("event_type", sa.String(64), nullable=False), sa.Column("title", sa.String(255), nullable=False), sa.Column("message", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(14, 3)), sa.Column("category", sa.String(100)),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE")),
        sa.Column("invoice_line_id", sa.Integer(), sa.ForeignKey("invoice_lines.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("published_at", sa.DateTime()), sa.Column("acknowledged_at", sa.DateTime()))
    for column in ("dedupe_key", "event_type", "category", "product_id", "invoice_line_id", "created_at"):
        op.create_index(f"ix_notification_events_{column}", "notification_events", [column], unique=column == "dedupe_key")


def downgrade() -> None:
    for table in ("notification_events", "price_alerts", "category_budgets", "data_quality_issues", "sync_run_events"):
        op.drop_table(table)
    with op.batch_alter_table("sync_runs") as batch:
        batch.drop_column("stats_json"); batch.drop_column("attempt_count"); batch.drop_column("current_stage")
    with op.batch_alter_table("invoice_lines") as batch:
        batch.drop_index("ix_invoice_lines_needs_review"); batch.drop_index("ix_invoice_lines_quality_confidence")
        batch.drop_column("needs_review"); batch.drop_column("quality_confidence")
