import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, DayOff, Request, Service, Tenant, WorkingHours


class SlotUnavailable(Exception):
    """Время уже занято или лежит вне рабочих часов."""


def _combine(day: date, moment: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, moment).replace(tzinfo=tz)


async def available_slots(
    session: AsyncSession,
    *,
    tenant: Tenant,
    service: Service,
    days_ahead: int = 14,
    starting_from: datetime | None = None,
) -> list[datetime]:
    """Свободные окна записи на ближайшие дни.

    Слоты нарезаются из рабочих часов по длительности услуги, затем вычитаются
    уже занятые и всё, что раньше текущего момента.
    """
    tz = ZoneInfo(tenant.timezone)
    now = (starting_from or datetime.now(UTC)).astimezone(tz)
    first_day = now.date()
    last_day = first_day + timedelta(days=days_ahead)

    hours = {
        wh.weekday: wh
        for wh in await session.scalars(
            select(WorkingHours).where(WorkingHours.tenant_id == tenant.id)
        )
    }
    if not hours:
        return []

    days_off = {
        row.day
        for row in await session.scalars(
            select(DayOff).where(
                DayOff.tenant_id == tenant.id,
                DayOff.day >= first_day,
                DayOff.day <= last_day,
            )
        )
    }

    taken = {
        appt.starts_at.astimezone(tz)
        for appt in await session.scalars(
            select(Appointment).where(
                Appointment.tenant_id == tenant.id,
                Appointment.is_cancelled.is_(False),
                Appointment.starts_at >= _combine(first_day, time(0, 0), tz),
            )
        )
    }

    step = timedelta(minutes=max(service.visit_duration_minutes, 5))
    slots: list[datetime] = []

    for offset in range(days_ahead + 1):
        day = first_day + timedelta(days=offset)
        if day in days_off:
            continue
        working = hours.get(day.weekday())
        if working is None or not working.is_working:
            continue

        cursor = _combine(day, working.opens_at, tz)
        closes = _combine(day, working.closes_at, tz)
        break_start = (
            _combine(day, working.break_starts_at, tz) if working.break_starts_at else None
        )
        break_end = _combine(day, working.break_ends_at, tz) if working.break_ends_at else None

        while cursor + step <= closes:
            slot_end = cursor + step
            in_break = (
                break_start is not None
                and break_end is not None
                and cursor < break_end
                and slot_end > break_start
            )
            if not in_break and cursor > now and cursor not in taken:
                slots.append(cursor)
            cursor = slot_end

    return slots


async def book_slot(
    session: AsyncSession,
    *,
    request: Request,
    service: Service,
    starts_at: datetime,
) -> Appointment:
    """Забронировать время визита.

    Уникальный индекс по (нотариус, время) — последний рубеж: даже если двое
    выбрали одно окно одновременно, второму прилетит SlotUnavailable.
    """
    appointment = Appointment(
        tenant_id=request.tenant_id,
        request_id=request.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=max(service.visit_duration_minutes, 5)),
    )
    session.add(appointment)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise SlotUnavailable("Это время уже заняли") from exc
    return appointment


async def upcoming_appointments(
    session: AsyncSession, tenant_id: uuid.UUID, limit: int = 50
) -> list[Appointment]:
    result = await session.scalars(
        select(Appointment)
        .where(
            Appointment.tenant_id == tenant_id,
            Appointment.is_cancelled.is_(False),
            Appointment.starts_at >= datetime.now(UTC),
        )
        .order_by(Appointment.starts_at)
        .limit(limit)
    )
    return list(result)
