"""widget theme per tenant

Revision ID: 7c2d9ac550c3
Revises: f5ee9f75e6df
Create Date: 2026-08-16 20:59:49.663930
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '7c2d9ac550c3'
down_revision: str | None = 'f5ee9f75e6df'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default обязателен: у заведённых нотариусов значений нет,
    # и без него ALTER падает на NOT NULL.
    op.add_column(
        'tenants',
        sa.Column('widget_mode', sa.String(length=16), nullable=False, server_default='dark'),
    )
    op.add_column(
        'tenants',
        sa.Column(
            'widget_accent', sa.String(length=16), nullable=False, server_default='#b89a5a'
        ),
    )
    op.add_column(
        'tenants',
        sa.Column('widget_font', sa.String(length=16), nullable=False, server_default='sans'),
    )


def downgrade() -> None:
    op.drop_column('tenants', 'widget_font')
    op.drop_column('tenants', 'widget_accent')
    op.drop_column('tenants', 'widget_mode')
