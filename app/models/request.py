import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.models.enums import Channel, RequestStatus, SubmissionMode


class Request(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Заявка клиента. Центральная сущность: её разбирают сотрудники."""

    __tablename__ = "requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", "public_number", name="uq_request_tenant_number"),
    )

    # Короткий номер в пределах нотариуса — им удобно оперировать по телефону.
    public_number: Mapped[int] = mapped_column(Integer, nullable=False)

    client_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
    )
    # Услугу могут удалить из каталога — заявка обязана пережить это,
    # поэтому ниже лежит слепок всего, что показали клиенту.
    service_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )

    service_title: Mapped[str] = mapped_column(String(255), nullable=False)
    submission_mode: Mapped[SubmissionMode] = mapped_column(
        Enum(SubmissionMode, name="submission_mode", native_enum=False, length=32),
        nullable=False,
    )
    # Перечень документов ровно в том виде, в каком его увидел клиент.
    checklist: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)

    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status", native_enum=False, length=32),
        default=RequestStatus.NEW,
        nullable=False,
        index=True,
    )

    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, name="channel", native_enum=False, length=32), nullable=False
    )

    assigned_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    client_comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    staff_note: Mapped[str] = mapped_column(Text, default="", nullable=False)

    # Пожелание клиента по времени, если услуга требует личного визита.
    preferred_time_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    source_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    client: Mapped["Client"] = relationship()  # noqa: F821
    assigned_staff: Mapped["Staff | None"] = relationship()  # noqa: F821
    attachments: Mapped[list["Attachment"]] = relationship(  # noqa: F821
        back_populates="request", cascade="all, delete-orphan"
    )
    events: Mapped[list["RequestEvent"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestEvent.created_at",
    )
    participants: Mapped[list["RequestParticipant"]] = relationship(  # noqa: F821
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestParticipant.created_at",
    )

    @property
    def is_open(self) -> bool:
        from app.models.enums import TERMINAL_STATUSES

        return self.status not in TERMINAL_STATUSES

    def __repr__(self) -> str:
        return f"<Request #{self.public_number} {self.status}>"


class RequestEvent(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """История заявки: кто и когда что сделал.

    Для нотариата это не украшение — по журналу восстанавливается, какой перечень
    документов клиент видел и кто из сотрудников его вёл.
    """

    __tablename__ = "request_events"

    request_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    # Пусто, если действие совершил клиент, а не сотрудник.
    actor_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    from_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)

    request: Mapped[Request] = relationship(back_populates="events")

    def __repr__(self) -> str:
        return f"<RequestEvent {self.from_status}->{self.to_status}>"
