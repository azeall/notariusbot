from datetime import datetime

from sqlalchemy import DateTime, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.models.enums import Channel


class Client(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Обратившийся человек.

    Персональных данных держим минимум: имя и телефон для связи. Паспортные
    данные в эту таблицу не попадают — они только внутри вложений, зашифрованными.
    """

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "external_id", name="uq_client_channel_ext"),
    )

    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, name="channel", native_enum=False, length=32), nullable=False
    )

    # Идентификатор в канале: telegram user_id, max user_id. Для виджета — телефон.
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)

    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    phone: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    # Согласие на обработку персональных данных — без него нельзя принимать документы.
    consent_given_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_text_version: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    @property
    def has_consent(self) -> bool:
        return self.consent_given_at is not None

    def __repr__(self) -> str:
        return f"<Client {self.channel}:{self.external_id}>"
