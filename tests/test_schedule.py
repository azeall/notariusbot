from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.domain.requests import create_request
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Channel, DayOff

MSK = ZoneInfo("Europe/Moscow")

# Понедельник, 10:00 по Москве — детерминированная точка отсчёта.
MONDAY_MORNING = datetime(2026, 8, 17, 10, 0, tzinfo=MSK)


async def test_slots_respect_working_hours(session, tenant, visit_service):
    slots = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    assert slots, "в понедельник должны быть свободные окна"
    assert all(slot.date() == MONDAY_MORNING.date() for slot in slots)
    assert all(time(9, 0) <= slot.timetz().replace(tzinfo=None) < time(18, 0) for slot in slots)


async def test_slots_skip_lunch_break(session, tenant, visit_service):
    """Услуга на час не должна попадать на перерыв 13:00–14:00."""
    slots = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    hours = {slot.hour for slot in slots}
    assert 13 not in hours


async def test_past_slots_are_not_offered(session, tenant, visit_service):
    slots = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    assert all(slot > MONDAY_MORNING for slot in slots)


async def test_weekend_is_empty(session, tenant, visit_service):
    saturday = datetime(2026, 8, 22, 10, 0, tzinfo=MSK)
    slots = await available_slots(
        session, tenant=tenant, service=visit_service, days_ahead=0, starting_from=saturday
    )
    assert slots == []


async def test_day_off_removes_slots(session, tenant, visit_service):
    session.add(DayOff(tenant_id=tenant.id, day=MONDAY_MORNING.date(), reason="Праздник"))
    await session.commit()

    slots = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    assert slots == []


async def test_booked_slot_disappears(session, tenant, client, visit_service):
    request = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()

    slots = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    target = slots[0]

    await book_slot(session, request=request, service=visit_service, starts_at=target)
    await session.commit()

    remaining = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=MONDAY_MORNING,
    )
    assert target not in remaining


async def test_double_booking_is_rejected(session, tenant, client, visit_service):
    """Двое не займут одно время — ловится уникальным индексом в базе."""
    first = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()
    second = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()

    target = datetime(2026, 8, 17, 15, 0, tzinfo=MSK)
    await book_slot(session, request=first, service=visit_service, starts_at=target)
    await session.commit()

    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=second, service=visit_service, starts_at=target)


async def test_cancelled_appointment_frees_the_slot(session, tenant, client, visit_service):
    first = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()

    target = datetime(2026, 8, 17, 16, 0, tzinfo=MSK)
    appointment = await book_slot(
        session, request=first, service=visit_service, starts_at=target
    )
    await session.commit()

    appointment.is_cancelled = True
    await session.commit()

    second = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()
    # Повторная бронь того же времени должна пройти.
    again = await book_slot(session, request=second, service=visit_service, starts_at=target)
    await session.commit()
    assert again.starts_at == target


async def test_tenant_without_schedule_has_no_slots(session, other_tenant, visit_service):
    slots = await available_slots(
        session,
        tenant=other_tenant,
        service=visit_service,
        days_ahead=3,
        starting_from=MONDAY_MORNING,
    )
    assert slots == []


async def test_slot_length_follows_service_duration(session, tenant, service, visit_service):
    """Услуга на 30 минут даёт вдвое больше окон, чем часовая."""
    short = await available_slots(
        session,
        tenant=tenant,
        service=service,
        days_ahead=0,
        starting_from=datetime(2026, 8, 17, 8, 0, tzinfo=MSK),
    )
    long = await available_slots(
        session,
        tenant=tenant,
        service=visit_service,
        days_ahead=0,
        starting_from=datetime(2026, 8, 17, 8, 0, tzinfo=MSK),
    )
    assert len(short) > len(long)
