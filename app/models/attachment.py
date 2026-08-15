import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class Attachment(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Документ клиента.

    В базе — только метаданные. Само содержимое лежит в файловом хранилище
    зашифрованным; путь бессмысленен без ключа.
    """

    __tablename__ = "attachments"

    request_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Относительный путь внутри storage_dir.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # Проставляется при удалении файла по истечении срока хранения:
    # метаданные и журнал доступа остаются, содержимое — нет.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    request: Mapped["Request"] = relationship(back_populates="attachments")  # noqa: F821

    @property
    def is_available(self) -> bool:
        return self.purged_at is None

    def __repr__(self) -> str:
        return f"<Attachment {self.original_filename!r}>"


class UploadToken(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Одноразовая ссылка на загрузку документов.

    Клиенту в мессенджер уходит ссылка, а не запрос «пришлите паспорт сюда»:
    файл сразу попадает в наше хранилище и не оседает на серверах мессенджера.
    """

    __tablename__ = "upload_tokens"

    request_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Хранится только хеш: утечка базы не даёт доступа к чужим документам.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    request: Mapped["Request"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<UploadToken request={self.request_id}>"
