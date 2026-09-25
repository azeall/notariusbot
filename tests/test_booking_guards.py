"""Public booking must obey the same schedule shown to the client."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.requests import create_request, transition_request
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Appointment, Channel, Request, RequestStatus, Service, Tenant


async def draft(session, tenant, client, service):
    result = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    return result


@pytest.mark.parametrize('kind', ['past', 'naive', 'off_grid', 'night', 'far_future'])
async def test_invalid_slots_are_rejected(session, tenant, client, visit_service, kind):
    request = await draft(session, tenant, client, visit_service)
    slots = await available_slots(session, tenant=tenant, service=visit_service)
    target = slots[0]
    target = {
        'past': datetime.now(UTC) - timedelta(days=1),
        'naive': target.replace(tzinfo=None),
        'off_grid': target + timedelta(minutes=1),
        'night': target.replace(hour=3),
        'far_future': target + timedelta(days=90),
    }[kind]
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=request, service=visit_service, starts_at=target)
    assert await session.scalar(select(Appointment)) is None


async def test_overlap_hidden_and_rejected(session, tenant, client, service, visit_service):
    long_request = await draft(session, tenant, client, visit_service)
    short_request = await draft(session, tenant, client, service)
    slots = await available_slots(session, tenant=tenant, service=visit_service)
    start = next(s for s in slots if s.hour == 10)
    await book_slot(session, request=long_request, service=visit_service, starts_at=start)
    await session.commit()
    remaining = await available_slots(session, tenant=tenant, service=service)
    middle = start + timedelta(minutes=30)
    assert middle not in remaining
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=short_request, service=service, starts_at=middle)


async def test_failed_booking_does_not_rollback_other_changes(session, tenant, client, visit_service):
    first = await draft(session, tenant, client, visit_service)
    second = await draft(session, tenant, client, visit_service)
    slots = await available_slots(session, tenant=tenant, service=visit_service)
    await book_slot(session, request=first, service=visit_service, starts_at=slots[0])
    await session.commit()
    second.staff_note = 'retain this change'
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=second, service=visit_service, starts_at=slots[0])
    await session.commit()
    await session.refresh(second)
    assert second.staff_note == 'retain this change'


async def test_cannot_book_twice_for_same_request(session, tenant, client, visit_service):
    request = await draft(session, tenant, client, visit_service)
    slots = await available_slots(session, tenant=tenant, service=visit_service)
    await book_slot(session, request=request, service=visit_service, starts_at=slots[0])
    await session.commit()
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=request, service=visit_service, starts_at=slots[1])


async def test_concurrent_overlapping_bookings_only_one_succeeds(engine, session, tenant, client, service, visit_service):
    first = await draft(session, tenant, client, visit_service)
    second = await draft(session, tenant, client, service)
    start = next(s for s in await available_slots(session, tenant=tenant, service=visit_service) if s.hour == 10)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def reserve(request_id, service_id, when):
        async with maker() as s:
            request = await s.get(Request, request_id)
            chosen = await s.get(Service, service_id)
            try:
                await book_slot(s, request=request, service=chosen, starts_at=when)
                await s.commit()
                return 'booked'
            except SlotUnavailable:
                await s.rollback()
                return 'busy'

    outcomes = await asyncio.wait_for(asyncio.gather(
        reserve(first.id, visit_service.id, start),
        reserve(second.id, service.id, start + timedelta(minutes=30)),
    ), timeout=10)
    assert sorted(outcomes) == ['booked', 'busy']


async def test_cross_tenant_service_cannot_be_booked(session, tenant, other_tenant, client, visit_service):
    request = await draft(session, tenant, client, visit_service)
    target = (await available_slots(session, tenant=tenant, service=visit_service))[0]
    foreign = Service(tenant_id=other_tenant.id, slug='foreign', title='Foreign', visit_duration_minutes=30)
    session.add(foreign)
    await session.commit()
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=request, service=foreign, starts_at=target)


@pytest.mark.parametrize('target', [RequestStatus.CANCELLED, RequestStatus.REJECTED, RequestStatus.COMPLETED])
async def test_terminal_request_frees_future_visit(session, tenant, client, visit_service, target):
    request = await draft(session, tenant, client, visit_service)
    request.status = RequestStatus.CLAIMED
    start = (await available_slots(session, tenant=tenant, service=visit_service))[0]
    appt = await book_slot(session, request=request, service=visit_service, starts_at=start)
    await session.commit()
    await transition_request(session, request=request, target=target)
    await session.commit()
    await session.refresh(appt)
    assert appt.is_cancelled
    assert start in await available_slots(session, tenant=tenant, service=visit_service)


async def test_terminal_request_retains_past_visit_history(session, tenant, client, visit_service):
    request = await draft(session, tenant, client, visit_service)
    start = datetime.now(UTC) - timedelta(days=1)
    appt = Appointment(tenant_id=tenant.id, request_id=request.id, starts_at=start, ends_at=start+timedelta(hours=1))
    session.add(appt)
    await session.commit()
    await transition_request(session, request=request, target=RequestStatus.CANCELLED)
    await session.commit()
    await session.refresh(appt)
    assert not appt.is_cancelled


@pytest.mark.parametrize('change', ['tenant_disabled', 'request_closed'])
async def test_booking_refreshes_state_after_another_transaction(engine, session, tenant, client, visit_service, change):
    request = await draft(session, tenant, client, visit_service)
    start = (await available_slots(session, tenant=tenant, service=visit_service))[0]
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as other:
        if change == 'tenant_disabled':
            changed = await other.get(Tenant, tenant.id)
            changed.is_active = False
        else:
            changed = await other.get(Request, request.id)
            await transition_request(other, request=changed, target=RequestStatus.CANCELLED)
        await other.commit()
    with pytest.raises(SlotUnavailable):
        await book_slot(session, request=request, service=visit_service, starts_at=start)
