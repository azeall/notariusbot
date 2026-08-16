from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
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

    # Приглашение нотариусу: владелец сервиса заводит карточку, а почту и пароль
    # нотариус задаёт сам по ссылке. В базе только хеш — по нему ссылку
    # не восстановить, как и в случае с загрузкой документов.
    invite_token_hash: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    invite_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    invite_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_activated(self) -> bool:
        """Нотариус завёл вход и может работать."""
        return self.invite_accepted_at is not None

    staff: Mapped[list["Staff"]] = relationship(  # noqa: F821
        back_populates="tenant", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(  # noqa: F821
        back_populates="tenant", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"
