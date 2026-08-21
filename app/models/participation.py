import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class ParticipationStatus(StrEnum):
    REQUESTED = "requested"  # сотрудник попросился в работу, ждёт ответа
    ACTIVE = "active"  # работает над заявкой вместе с ведущим
    DECLINED = "declined"  # ведущий отказал
    LEFT = "left"  # вышел сам или его убрали


PARTICIPATION_LABELS: dict[ParticipationStatus, str] = {
    ParticipationStatus.REQUESTED: "просится в работу",
    ParticipationStatus.ACTIVE: "работает вместе",
    ParticipationStatus.DECLINED: "отказано",
    ParticipationStatus.LEFT: "вышел",
}


class RequestParticipant(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Второй сотрудник на заявке.

    Ведущий остаётся один — тот, кто взял заявку. Остальные подключаются
    по его согласию: сложное дело нередко ведут вдвоём, и раньше для этого
    приходилось передавать заявку целиком, теряя историю.

    Нотариус подключает кого угодно сразу, без спроса: это его контора.
    """

    __tablename__ = "request_participants"
    __table_args__ = (
        UniqueConstraint("request_id", "staff_id", name="uq_participant_request_staff"),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    staff_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("staff.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    status: Mapped[ParticipationStatus] = mapped_column(
        Enum(ParticipationStatus, name="participation_status", native_enum=False, length=32),
        default=ParticipationStatus.REQUESTED,
        nullable=False,
    )

    # Зачем понадобился второй человек — видно и ведущему, и нотариусу.
    note: Mapped[str] = mapped_column(String(500), default="", nullable=False)

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )

    request: Mapped["Request"] = relationship(back_populates="participants")  # noqa: F821
    staff: Mapped["Staff"] = relationship(foreign_keys=[staff_id])  # noqa: F821

    @property
    def is_active(self) -> bool:
        return self.status is ParticipationStatus.ACTIVE

    def __repr__(self) -> str:
        return f"<RequestParticipant {self.staff_id} {self.status}>"
