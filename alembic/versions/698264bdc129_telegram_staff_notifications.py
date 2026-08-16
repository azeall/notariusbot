"""telegram staff notifications

Revision ID: 698264bdc129
Revises: 01743d423391
Create Date: 2026-08-16 14:43:02.307046
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '698264bdc129'
down_revision: str | None = '01743d423391'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('staff', sa.Column('telegram_chat_id', sa.String(length=64), nullable=True))
    op.add_column('staff', sa.Column('telegram_link_code', sa.String(length=32), nullable=True))
    # server_default обязателен: у уже заведённых сотрудников значения нет,
    # и без него ALTER падает на NOT NULL.
    op.add_column(
        'staff',
        sa.Column(
            'notify_new_requests',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_unique_constraint(
        'uq_staff_telegram_link_code', 'staff', ['telegram_link_code']
    )


def downgrade() -> None:
    op.drop_constraint('uq_staff_telegram_link_code', 'staff', type_='unique')
    op.drop_column('staff', 'notify_new_requests')
    op.drop_column('staff', 'telegram_link_code')
    op.drop_column('staff', 'telegram_chat_id')
