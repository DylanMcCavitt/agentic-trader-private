"""gates and ledger schema: account/sleeve equity, order details, quotes

Revision ID: 8b2f4c7d91e0
Revises: 61eb69034254
Create Date: 2026-07-06 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8b2f4c7d91e0'
down_revision: Union[str, None] = '61eb69034254'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('equity', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('accounts', sa.Column('hwm', sa.Numeric(precision=14, scale=2), nullable=True))
    op.add_column('accounts', sa.Column('equity_updated_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column('sleeves', sa.Column('equity', sa.Numeric(precision=14, scale=2), nullable=True))

    op.add_column('orders', sa.Column('sleeve_id', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('symbol', sa.String(length=20), nullable=True))
    op.add_column('orders', sa.Column('instrument', sa.String(length=20), nullable=True))
    op.add_column('orders', sa.Column('payload', sa.JSON(), nullable=True))
    op.create_index(op.f('ix_orders_sleeve_id'), 'orders', ['sleeve_id'], unique=False)
    op.create_index(op.f('ix_orders_symbol'), 'orders', ['symbol'], unique=False)
    op.create_foreign_key('fk_orders_sleeve_id_sleeves', 'orders', 'sleeves', ['sleeve_id'], ['id'])

    op.create_table('quotes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('symbol', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('price', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('bid', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('ask', sa.Numeric(precision=14, scale=4), nullable=True),
    sa.Column('avg_dollar_volume', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('open_interest', sa.Numeric(precision=14, scale=0), nullable=True),
    sa.Column('occ_symbol', sa.String(length=40), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('quoted_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_quotes_account_id'), 'quotes', ['account_id'], unique=False)
    op.create_index(op.f('ix_quotes_symbol'), 'quotes', ['symbol'], unique=False)
    op.create_index(op.f('ix_quotes_occ_symbol'), 'quotes', ['occ_symbol'], unique=False)
    op.create_index(op.f('ix_quotes_quoted_at'), 'quotes', ['quoted_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_quotes_quoted_at'), table_name='quotes')
    op.drop_index(op.f('ix_quotes_occ_symbol'), table_name='quotes')
    op.drop_index(op.f('ix_quotes_symbol'), table_name='quotes')
    op.drop_index(op.f('ix_quotes_account_id'), table_name='quotes')
    op.drop_table('quotes')

    op.drop_constraint('fk_orders_sleeve_id_sleeves', 'orders', type_='foreignkey')
    op.drop_index(op.f('ix_orders_symbol'), table_name='orders')
    op.drop_index(op.f('ix_orders_sleeve_id'), table_name='orders')
    op.drop_column('orders', 'payload')
    op.drop_column('orders', 'instrument')
    op.drop_column('orders', 'symbol')
    op.drop_column('orders', 'sleeve_id')

    op.drop_column('sleeves', 'equity')

    op.drop_column('accounts', 'equity_updated_at')
    op.drop_column('accounts', 'hwm')
    op.drop_column('accounts', 'equity')
