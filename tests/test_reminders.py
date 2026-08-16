"""Напоминания о визите и статус заявок для клиента."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.channels import flow
from app.domain.requests import create_request, transition_request
from app.domain.schedule import book_slot
from app.models import Appointment, Channel, Client, RequestStatus
from app.reminders import DAY_BEFORE, render_reminder, send_due_reminders


def maker(engine):
    """Рассылка должна ходить в ту же базу, что и тест."""
    return async_sessionmaker(engine, expire_on_commit=False)


async def _booked(session, tenant, client, visit_service, *, starts_in: timedelta):
    request = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.flush()
    appointment = await book_slot(
        session,
        request=request,
        service=visit_service,
        starts_at=datetime.now(UTC) + starts_in,
    )
    await session.commit()
    return request, appointment


async def test_reminder_marks_appointment(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=20)
    )

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at is not None


async def test_reminder_is_not_sent_twice(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=20)
    )

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)
    first_stamp = appointment.reminded_day_before_at

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at == first_stamp


async def test_far_appointment_is_untouched(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(days=5)
    )

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at is None


async def test_same_day_reminder_fires_separately(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=2)
    )

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at is not None
    assert appointment.reminded_same_day_at is not None


async def test_cancelled_appointment_is_skipped(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=10)
    )
    appointment.is_cancelled = True
    await session.commit()

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at is None


async def test_past_appointment_is_skipped(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=-2)
    )

    await send_due_reminders(session_factory=maker(engine))
    await session.refresh(appointment)

    assert appointment.reminded_day_before_at is None


async def test_reminder_text_has_documents_and_address(session, tenant, client, visit_service):
    request, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=20)
    )
    tenant.address = "ул. Тверская, 1"
    tenant.phone = "+7 495 000-00-00"
    await session.commit()

    text = render_reminder(tenant, request, "17 августа, понедельник, 10:00", True)

    assert "Напоминаем" in text
    assert visit_service.title in text
    assert "ул. Тверская, 1" in text
    assert "Паспорт" in text
    assert "+7 495 000-00-00" in text


async def test_same_day_text_differs(session, tenant, client, visit_service):
    request, _ = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=2)
    )
    text = render_reminder(tenant, request, "сегодня в 14:00", False)
    assert "сегодня" in text.lower()


async def test_dry_run_changes_nothing(engine, session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(hours=20)
    )
    await send_due_reminders(dry_run=True, session_factory=maker(engine))
    await session.refresh(appointment)
    assert appointment.reminded_day_before_at is None


async def test_window_covers_a_day(session):
    assert DAY_BEFORE == timedelta(hours=24)


# --- статус заявок для клиента ---------------------------------------------


async def test_my_requests_lists_recent(session, tenant, service):
    client = Client(
        tenant_id=tenant.id,
        channel=Channel.TELEGRAM,
        external_id="777",
        full_name="Смирнов",
        phone="+79990000001",
    )
    session.add(client)
    await session.flush()

    for _ in range(2):
        await create_request(
            session, tenant=tenant, client=client, service=service, channel=Channel.TELEGRAM
        )
    await session.commit()

    found = await flow.my_requests(
        session, tenant=tenant, channel=Channel.TELEGRAM, external_id="777"
    )
    assert len(found) == 2
    assert found[0].public_number == 2, "сначала свежие"


async def test_my_requests_empty_for_stranger(session, tenant):
    found = await flow.my_requests(
        session, tenant=tenant, channel=Channel.TELEGRAM, external_id="нет-такого"
    )
    assert found == []
    assert "пока нет заявок" in flow.render_my_requests(found)


async def test_my_requests_text_shows_human_status(session, tenant, service, employee):
    client = Client(
        tenant_id=tenant.id, channel=Channel.TELEGRAM, external_id="778", phone="+79990000002"
    )
    session.add(client)
    await session.flush()

    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.TELEGRAM
    )
    await session.commit()

    text = flow.render_my_requests([request])
    assert f"№ {request.public_number}" in text
    assert "принята, ждёт сотрудника" in text
    assert "new" not in text, "клиенту показываем словами, а не кодом статуса"


async def test_my_requests_does_not_leak_other_clients(session, tenant, service, client):
    await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    other = await flow.my_requests(
        session, tenant=tenant, channel=Channel.TELEGRAM, external_id=client.external_id
    )
    assert other == [], "канал у клиента другой — заявка не его"


async def test_status_shown_after_staff_takes_it(session, tenant, service, employee):
    client = Client(
        tenant_id=tenant.id, channel=Channel.TELEGRAM, external_id="779", phone="+79990000003"
    )
    session.add(client)
    await session.flush()
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.TELEGRAM
    )
    await session.commit()

    from app.domain.requests import claim_request

    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    await session.refresh(request)

    assert "в работе" in flow.render_my_requests([request])


async def test_completed_status_is_readable(session, tenant, service, employee):
    client = Client(
        tenant_id=tenant.id, channel=Channel.TELEGRAM, external_id="780", phone="+79990000004"
    )
    session.add(client)
    await session.flush()
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.TELEGRAM
    )
    await session.commit()

    from app.domain.requests import claim_request

    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    await session.refresh(request)
    await transition_request(
        session, request=request, target=RequestStatus.COMPLETED, staff=employee
    )
    await session.commit()

    assert "завершена" in flow.render_my_requests([request])


async def test_appointments_table_has_reminder_columns(session, tenant, client, visit_service):
    _, appointment = await _booked(
        session, tenant, client, visit_service, starts_in=timedelta(days=3)
    )
    row = await session.scalar(select(Appointment).where(Appointment.id == appointment.id))
    assert row.reminded_day_before_at is None
    assert row.reminded_same_day_at is None
