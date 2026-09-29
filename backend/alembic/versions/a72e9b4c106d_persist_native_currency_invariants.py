"""Persist native currency invariants.

Revision ID: a72e9b4c106d
Revises: f83a2c91d607
"""
from alembic import op
import sqlalchemy as sa

revision = "a72e9b4c106d"
down_revision = "f83a2c91d607"
branch_labels = None
depends_on = None

# Frozen catalog at this revision; do not depend on future runtime catalogs.
_CURRENCIES = (
    "AED AMD AUD AZN BRL BYN CAD CHF CNY CZK EUR GBP GEL HKD HUF IDR ILS INR "
    "JPY KGS KRW KZT MDL MXN NZD PLN RON RUB SAR SGD THB TJS TMT TRY UAH USD UZS VND ZAR"
).split()


def _legacy_currency(connection) -> str:
    # Respect the singleton, not an arbitrary settings row. Configuration is
    # consulted only when id=1 is absent, matching application startup seeding.
    from app.core.config import get_settings

    currency = connection.execute(sa.text("SELECT currency FROM app_settings WHERE id=1")).scalar_one_or_none()
    currency = currency if currency is not None else get_settings().default_currency
    if not isinstance(currency, str) or currency.upper() not in _CURRENCIES:
        raise RuntimeError("Native currency migration requires a valid legacy default currency")
    return currency.upper()


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE accounts, app_settings, budgets, goals, recurring_transactions, transactions IN ACCESS EXCLUSIVE MODE"
    ))
    currency = _legacy_currency(connection)
    missing_source = connection.execute(sa.text("""
        SELECT EXISTS (SELECT 1 FROM recurring_transactions r
        LEFT JOIN accounts a ON a.id=r.account_id
        WHERE a.id IS NULL OR a.currency IS NULL)
    """)).scalar_one()
    if missing_source:
        raise RuntimeError("Cannot backfill recurring amount_currency: source account currency is missing")

    codes = connection.execute(sa.text("""
        SELECT DISTINCT upper(a.currency) FROM accounts a
        JOIN recurring_transactions r ON r.account_id=a.id
            OR (r.type='TRANSFER' AND r.transfer_account_id=a.id)
    """)).scalars()
    if any(code not in _CURRENCIES for code in codes):
        raise RuntimeError("Cannot backfill recurring snapshots: unsupported account currency")

    op.add_column("transactions", sa.Column("transfer_amount", sa.Numeric(20, 6, asdecimal=True), nullable=True))
    op.add_column("budgets", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column("goals", sa.Column("currency", sa.String(3), nullable=True))
    op.add_column("recurring_transactions", sa.Column("amount_currency", sa.String(3), nullable=True))
    op.add_column("recurring_transactions", sa.Column("transfer_amount", sa.Numeric(20, 6, asdecimal=True), nullable=True))
    op.add_column("recurring_transactions", sa.Column("transfer_amount_currency", sa.String(3), nullable=True))
    # SQLAlchemy's existing non-native enums persist enum names (TRANSFER).
    connection.execute(sa.text("""
        UPDATE transactions t SET transfer_amount=t.amount
        FROM accounts src, accounts dst
        WHERE t.type='TRANSFER' AND src.id=t.account_id AND dst.id=t.transfer_account_id
          AND upper(src.currency)=upper(dst.currency)
    """))
    for table in ("budgets", "goals"):
        connection.execute(sa.text(f"UPDATE {table} SET currency=:currency"), {"currency": currency})
        op.alter_column(table, "currency", existing_type=sa.String(3), nullable=False)
    connection.execute(sa.text("""
        UPDATE recurring_transactions r SET amount_currency=upper(a.currency)
        FROM accounts a WHERE a.id=r.account_id
    """))
    connection.execute(sa.text("""
        UPDATE recurring_transactions r
        SET transfer_amount_currency=upper(dst.currency),
            transfer_amount=CASE WHEN r.amount_currency=upper(dst.currency) THEN r.amount ELSE NULL END
        FROM accounts dst WHERE r.type='TRANSFER' AND dst.id=r.transfer_account_id
    """))
    op.alter_column("recurring_transactions", "amount_currency", existing_type=sa.String(3), nullable=False)


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "LOCK TABLE accounts, app_settings, budgets, goals, recurring_transactions, transactions IN ACCESS EXCLUSIVE MODE"
    ))
    currency = _legacy_currency(connection)
    checks = {
        "transaction destination amount": """
            SELECT EXISTS (SELECT 1 FROM transactions t
            LEFT JOIN accounts src ON src.id=t.account_id
            LEFT JOIN accounts dst ON dst.id=t.transfer_account_id
            WHERE t.transfer_amount IS NOT NULL AND (
                t.type <> 'TRANSFER' OR t.transfer_amount <> t.amount
                OR src.id IS NULL OR dst.id IS NULL OR upper(src.currency) <> upper(dst.currency)))
        """,
        "budget currency": "SELECT EXISTS (SELECT 1 FROM budgets WHERE currency <> :currency)",
        "goal currency": "SELECT EXISTS (SELECT 1 FROM goals WHERE currency <> :currency)",
        "recurring currency instruction": """
            SELECT EXISTS (SELECT 1 FROM recurring_transactions r
            LEFT JOIN accounts src ON src.id=r.account_id
            LEFT JOIN accounts dst ON dst.id=r.transfer_account_id
            WHERE src.id IS NULL OR r.amount_currency IS DISTINCT FROM upper(src.currency)
              OR (r.transfer_amount_currency IS NOT NULL AND (
                  r.type <> 'TRANSFER' OR dst.id IS NULL
                  OR r.transfer_amount_currency IS DISTINCT FROM upper(dst.currency)))
              OR (r.transfer_amount IS NOT NULL AND (
                  r.type <> 'TRANSFER' OR dst.id IS NULL OR r.transfer_amount <> r.amount
                  OR r.transfer_amount_currency IS NULL OR upper(src.currency) <> upper(dst.currency))))
        """,
    }
    for label, query in checks.items():
        if connection.execute(sa.text(query), {"currency": currency}).scalar_one():
            raise RuntimeError(f"Cannot downgrade native currencies: {label} cannot be represented by the old schema")
    for column in ("transfer_amount_currency", "transfer_amount", "amount_currency"):
        op.drop_column("recurring_transactions", column)
    op.drop_column("goals", "currency")
    op.drop_column("budgets", "currency")
    op.drop_column("transactions", "transfer_amount")
