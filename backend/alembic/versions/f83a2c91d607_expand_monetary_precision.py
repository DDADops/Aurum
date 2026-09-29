"""Expand monetary precision without changing nominal values.

Revision ID: f83a2c91d607
Revises: d1a6f4c8b729
"""
from alembic import op
import sqlalchemy as sa

revision = "f83a2c91d607"
down_revision = "d1a6f4c8b729"
branch_labels = None
depends_on = None

# Frozen migration definitions; never import the application's evolving models.
COLUMNS = (
    ("transactions", "amount", 20, 6),
    ("transaction_splits", "amount", 20, 6),
    ("recurring_transactions", "amount", 20, 6),
    ("assets", "monthly_cash_flow", 20, 6),
    ("asset_valuations", "value", 38, 18),
    ("budgets", "monthly_limit", 20, 6),
    ("goals", "target_amount", 20, 6),
    ("goal_contributions", "amount", 20, 6),
    ("app_settings", "idle_cash_threshold_amount", 20, 6),
)


def upgrade() -> None:
    for table, column, precision, scale in COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Numeric(14, 2),
            type_=sa.Numeric(precision, scale),
            postgresql_using=f'"{column}"::numeric({precision},{scale})',
        )


def downgrade() -> None:
    connection = op.get_bind()
    # Hold locks through all checks and ALTERs so a concurrent writer cannot
    # introduce a lossy value after preflight. PostgreSQL DDL is transactional.
    tables = ", ".join(f'"{table}"' for table in sorted({row[0] for row in COLUMNS}))
    connection.execute(sa.text(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE"))
    for table, column, _, _ in COLUMNS:
        unsafe = connection.execute(sa.text(f'''
            SELECT EXISTS (SELECT 1 FROM "{table}"
            WHERE "{column}" IS NOT NULL AND NOT (
                "{column}" BETWEEN -999999999999.99 AND 999999999999.99
                AND "{column}" = trunc("{column}", 2)))
        ''')).scalar_one()
        if unsafe:
            raise RuntimeError(f"Cannot downgrade {table}.{column}: values do not fit Numeric(14,2) exactly")
    for table, column, precision, scale in COLUMNS:
        op.alter_column(
            table, column,
            existing_type=sa.Numeric(precision, scale),
            type_=sa.Numeric(14, 2),
            postgresql_using=f'"{column}"::numeric(14,2)',
        )
