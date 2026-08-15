import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class WorkingHours(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Рабочие часы нотариуса по дням недели. Из них нарезаются слоты записи."""

    __tablename__ = "working_hours"
    __table_args__ = (
        UniqueConstraint("tenant_id", "weekday", name="uq_working_hours_tenant_weekday"),
    )

    # 0 — понедельник, 6 — воскресенье (как в date.weekday()).
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    is_working: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    opens_at: Mapped[time] = mapped_column(Time, default=time(9, 0), nullable=False)
    closes_at: Mapped[time] = mapped_column(Time, default=time(18, 0), nullable=False)
    break_starts_at: Mapped[time | None] = mapped_column(Time, nullable=True)
    break_ends_at: Mapped[time | None] = mapped_column(Time, nullable=True)

    def __repr__(self) -> str:
        return f"<WorkingHours wd={self.weekday} {self.opens_at}-{self.closes_at}>"


class DayOff(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Разовые выходные и праздники — перекрывают обычное расписание."""

    __tablename__ = "days_off"
    __table_args__ = (UniqueConstraint("tenant_id", "day", name="uq_day_off_tenant_day"),)

    day: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)


class Appointment(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Забронированное время личного визита."""

    __tablename__ = "appointments"
    # Частичный уникальный индекс: два человека не займут одно время, но
    # отменённая запись освобождает слот, а не блокирует его навсегда.
    __table_args__ = (
        Index(
            "uq_appointment_tenant_slot",
            "tenant_id",
            "starts_at",
            unique=True,
            postgresql_where=text("NOT is_cancelled"),
        ),
    )

    request_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_cancelled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    request: Mapped["Request"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return f"<Appointment {self.starts_at:%d.%m %H:%M}>"
