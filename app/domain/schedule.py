import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TERMINAL_STATUSES, Appointment, DayOff, Request, Service, Tenant, WorkingHours


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
    if service.tenant_id != tenant.id or not tenant.is_active or not service.is_active:
        return []
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

    taken = [
        (appt.starts_at.astimezone(tz), appt.ends_at.astimezone(tz))
        for appt in await session.scalars(
            select(Appointment).where(
                Appointment.tenant_id == tenant.id,
                Appointment.is_cancelled.is_(False),
                Appointment.ends_at > _combine(first_day, time(0, 0), tz),
                Appointment.starts_at < _combine(last_day + timedelta(days=1), time(0, 0), tz),
            )
        )
    ]

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
            overlaps = any(cursor < end and slot_end > start for start, end in taken)
            if not in_break and cursor > now and not overlaps:
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

    Блокировка нотариуса сериализует проверку пересечения интервалов и запись.
    Уникальный индекс остаётся последним рубежом для совпадающих начал.
    """
    if starts_at.tzinfo is None or starts_at.utcoffset() is None:
        raise SlotUnavailable("Выберите время из расписания")
    if service.tenant_id != request.tenant_id or service.id != request.service_id:
        raise SlotUnavailable("Запись недоступна")
    # NO KEY UPDATE совместим с проверкой внешнего ключа при создании заявки.
    # Блокировку держим до commit всей операции, включая перенос.
    with session.no_autoflush:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.id == request.tenant_id).with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        current_status = await session.scalar(
            select(Request.status).where(Request.id == request.id, Request.tenant_id == request.tenant_id)
            .with_for_update()
        )
    if current_status is None or current_status in TERMINAL_STATUSES:
        raise SlotUnavailable("Заявка уже закрыта")
    if tenant is None or not tenant.is_active or not service.is_active:
        raise SlotUnavailable("Запись недоступна")
    existing = await session.scalar(select(Appointment.id).where(
        Appointment.tenant_id == request.tenant_id,
        Appointment.request_id == request.id,
        Appointment.is_cancelled.is_(False),
    ))
    if existing is not None:
        raise SlotUnavailable("У заявки уже есть запись")
    if starts_at not in await available_slots(session, tenant=tenant, service=service):
        raise SlotUnavailable("Это время недоступно. Выберите другое окно из расписания")
    appointment = Appointment(
        tenant_id=request.tenant_id,
        request_id=request.id,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=max(service.visit_duration_minutes, 5)),
    )
    try:
        async with session.begin_nested():
            session.add(appointment)
            await session.flush()
    except IntegrityError as exc:
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
