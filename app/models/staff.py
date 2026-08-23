from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.models.enums import StaffRole


class Staff(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Нотариус и его помощники. Логин — email, пароль хранится хешем argon2."""

    __tablename__ = "staff"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_staff_tenant_email"),)

    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[StaffRole] = mapped_column(
        Enum(StaffRole, name="staff_role", native_enum=False, length=32),
        default=StaffRole.EMPLOYEE,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Куда слать уведомления о новых заявках. Пусто — сотрудник не подключил
    # Telegram и видит заявки только в открытой вкладке очереди.
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Одноразовый код привязки: живёт до первого использования.
    telegram_link_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True, unique=True
    )
    notify_new_requests: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Сброс пароля по одноразовой ссылке.
    #
    # Почты у сервиса нет, и заводить её ради одной задачи — лишний узел,
    # который однажды отвалится молча. Ссылку выдаёт тот, кто и так отвечает
    # за доступ: нотариус — своим сотрудникам, владелец сервиса — нотариусу.
    # В базе хранится отпечаток, а не сама ссылка.
    reset_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reset_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="staff")  # noqa: F821

    @property
    def can_manage_catalog(self) -> bool:
        return self.role is StaffRole.OWNER

    def __repr__(self) -> str:
        return f"<Staff {self.email} {self.role}>"
