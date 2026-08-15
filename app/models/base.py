import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKey:
    @declared_attr
    def id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamps:
    @declared_attr
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True), server_default=func.now(), nullable=False
        )

    @declared_attr
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )


class TenantScoped:
    """Каждая запись принадлежит ровно одному нотариусу.

    Забыть этот столбец в новой таблице — значит открыть данные клиентов одного
    нотариуса другому, поэтому он вынесен в миксин, а не пишется руками каждый раз.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PgUUID(as_uuid=True),
            ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
