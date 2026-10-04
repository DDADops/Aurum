"""Persist conservative crypto and valuation provenance.

Revision ID: e91b7a42c603
Revises: c4e8a91b720f
"""
from alembic import op
import sqlalchemy as sa

revision = "e91b7a42c603"
down_revision = "c4e8a91b720f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("crypto_transactions", sa.Column("quote_currency", sa.String(3), nullable=True))
    op.add_column("crypto_holdings", sa.Column("last_price_currency", sa.String(3), nullable=True))
    op.add_column("crypto_holdings", sa.Column("last_price_as_of_date", sa.Date(), nullable=True))
    op.add_column("asset_valuations", sa.Column("currency", sa.String(3), nullable=True))
    # No existing row has independently trustworthy denomination evidence.
    op.add_column("asset_valuations", sa.Column("currency_confirmed", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column("asset_valuations", "currency_confirmed", server_default=None)


def downgrade():
    op.drop_column("asset_valuations", "currency_confirmed")
    op.drop_column("asset_valuations", "currency")
    op.drop_column("crypto_holdings", "last_price_as_of_date")
    op.drop_column("crypto_holdings", "last_price_currency")
    op.drop_column("crypto_transactions", "quote_currency")
