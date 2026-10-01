"""Add dated reference exchange rates and maximum observation age.

Revision ID: c4e8a91b720f
Revises: a72e9b4c106d
"""
from alembic import op
import sqlalchemy as sa

revision = "c4e8a91b720f"
down_revision = "a72e9b4c106d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(38, 18), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("base_currency <> quote_currency", name="ck_fx_rates_distinct_currencies"),
        sa.CheckConstraint("rate > 0 AND rate < 'Infinity'::numeric AND rate <> 'NaN'::numeric", name="ck_fx_rates_positive_finite"),
        # Unique B-tree supports backward scans for latest directed pair/date.
        sa.UniqueConstraint("base_currency", "quote_currency", "as_of_date", name="uq_fx_rates_pair_date"),
    )
    op.add_column("app_settings", sa.Column("fx_max_age_days", sa.Integer(), nullable=True))
    op.execute("UPDATE app_settings SET fx_max_age_days = 7")
    op.alter_column("app_settings", "fx_max_age_days", nullable=False, server_default=sa.text("7"))
    op.create_check_constraint("ck_app_settings_fx_max_age_days", "app_settings", "fx_max_age_days >= 0")


def downgrade() -> None:
    # Expected destructive downgrade: the predecessor has no FX observation store.
    op.drop_table("fx_rates")
    op.drop_constraint("ck_app_settings_fx_max_age_days", "app_settings", type_="check")
    op.drop_column("app_settings", "fx_max_age_days")
