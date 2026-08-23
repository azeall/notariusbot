"""Сброс пароля по одноразовой ссылке

Revision ID: c7d1a9e40f52
Revises: a1c4e8f20b31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7d1a9e40f52"
down_revision: str | None = "a1c4e8f20b31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("staff", sa.Column("reset_token_hash", sa.String(128), nullable=True))
    op.add_column(
        "staff", sa.Column("reset_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_staff_reset_token_hash", "staff", ["reset_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_staff_reset_token_hash", table_name="staff")
    op.drop_column("staff", "reset_expires_at")
    op.drop_column("staff", "reset_token_hash")
