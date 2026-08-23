"""Отметки о полученных документах

Revision ID: a1c4e8f20b31
Revises: 0df3929cf291
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a1c4e8f20b31"
down_revision: str | None = "0df3929cf291"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default обязателен: в существующих заявках поле иначе окажется
    # пустым, а NOT NULL этого не простит.
    op.add_column(
        "requests",
        sa.Column("received_documents", JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("requests", "received_documents")
