
from sqlalchemy import Boolean, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Timestamps, UUIDPrimaryKey


class Tenant(Base, UUIDPrimaryKey, Timestamps):
    """Нотариус или нотариальная контора — арендатор системы."""

    __tablename__ = "tenants"

    # Короткий идентификатор для встраивания виджета: <script data-notary="ivanov">
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Moscow", nullable=False)

    # Домены, которым разрешено встраивать виджет. Пусто — разрешено всем,
    # что допустимо только на этапе разработки.
    # Список доменов держим в JSONB, а не отдельной таблицей: их два-три на нотариуса
    # и они всегда читаются целиком.
    allowed_origins: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    staff: Mapped[list["Staff"]] = relationship(  # noqa: F821
        back_populates="tenant", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(  # noqa: F821
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"
