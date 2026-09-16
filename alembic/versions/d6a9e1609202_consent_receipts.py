"""Снимок согласия в клиенте и каждой новой заявке.

Revision ID: d6a9e1609202
Revises: c7d1a9e40f52
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d6a9e1609202"
down_revision = "c7d1a9e40f52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("clients", "requests"):
        op.add_column(
            table, sa.Column("consent_receipt", JSONB(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    for table in ("requests", "clients"):
        op.drop_column(table, "consent_receipt")
