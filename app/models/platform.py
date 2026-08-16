from sqlalchemy import Boolean, String

from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UUIDPrimaryKey


class PlatformAdmin(Base, UUIDPrimaryKey, Timestamps):
    """Владелец сервиса — тот, кто продаёт и подключает нотариусов.

    Намеренно отдельная таблица, а не роль в staff: сотрудник всегда принадлежит
    одному нотариусу, а этот человек стоит над всеми. Смешивать их в одной
    таблице значит рано или поздно выдать кому-то лишние права.
    """

    __tablename__ = "platform_admins"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<PlatformAdmin {self.email}>"
