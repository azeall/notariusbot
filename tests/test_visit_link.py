"""Перенос и отмена визита клиентом.

Здесь посторонний человек без входа меняет расписание нотариуса. Значит,
проверять надо не только «работает ли», но и «что нельзя»: подделать ссылку,
увидеть чужие данные, остаться вообще без записи из-за гонки за окно.
"""


import httpx
import pytest
import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain import visit_links
from app.domain.requests import create_request, transition_request
from app.domain.schedule import available_slots, book_slot
from app.models import Appointment, Channel, Request, RequestEvent, RequestStatus
from app.web.deps import db_session
from app.web.main import app


@pytest.fixture
async def http(engine, session):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with maker() as request_session:
            try:
                yield request_session
                await request_session.commit()
            except Exception:
                await request_session.rollback()
                raise

    app.dependency_overrides[db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def booked(session, tenant, client, visit_service):
    """Заявка с назначенным приёмом и свободные окна на будущее."""
    request = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()

    slots = await available_slots(session, tenant=tenant, service=visit_service)
    assert len(slots) >= 2, "для теста нужно хотя бы два свободных окна"
    await book_slot(session, request=request, service=visit_service, starts_at=slots[0])
    await session.commit()
    return request, slots


# --- сама ссылка -------------------------------------------------------------


def test_link_survives_a_round_trip():
    import uuid

    who = uuid.uuid4()
    assert visit_links.read(visit_links.issue(who)) == who


def test_forged_link_is_rejected():
    import uuid

    token = visit_links.issue(uuid.uuid4())
    assert visit_links.read(token[:-3] + "aaa") is None


# --- страница ----------------------------------------------------------------


async def test_client_sees_own_appointment(http, booked):
    request, slots = booked
    page = await http.get(f"/visit/{visit_links.issue(request.id)}")
    assert page.status_code == 200
    assert slots[0].strftime("%H:%M") in page.text


async def test_appointment_is_displayed_in_office_timezone(http, session, tenant, booked):
    request, slots = booked
    tenant.timezone = 'Asia/Vladivostok'
    await session.commit()
    page = await http.get(f'/visit/{visit_links.issue(request.id)}')
    local = slots[0].astimezone(ZoneInfo(tenant.timezone))
    assert local.strftime('%d.%m.%Y, %H:%M') in page.text
    assert tenant.timezone in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert page.headers['referrer-policy'] == 'no-referrer'


async def test_busy_message_is_visible(http, booked):
    request, _ = booked
    page = await http.get(f'/visit/{visit_links.issue(request.id)}?busy=1')
    assert 'Прежняя запись сохранена' in page.text


@pytest.mark.parametrize('target', ['2026-01-01T10:00:00+03:00', '2027-12-31T03:00:00+03:00', '2026-10-01T09:00:00'])
async def test_invalid_move_keeps_original_appointment(http, session, booked, target):
    request, slots = booked
    response = await http.post(f'/visit/{visit_links.issue(request.id)}/move', data={'slot': target})
    assert response.status_code in (303, 400)
    saved = await session.scalar(select(Appointment).where(Appointment.request_id == request.id, Appointment.is_cancelled.is_(False)))
    assert saved.starts_at == slots[0]


async def test_expired_request_link_is_gone(http, session, booked):
    request, _ = booked
    request.created_at = datetime.now(UTC) - timedelta(days=46)
    await session.commit()
    assert (await http.get(f'/visit/{visit_links.issue(request.id)}')).status_code == 410


async def test_disabled_tenant_cannot_manage_visit(http, session, tenant, booked):
    request, _ = booked
    tenant.is_active = False
    await session.commit()
    assert (await http.post(f'/visit/{visit_links.issue(request.id)}/cancel')).status_code == 410


async def test_calendar_download_uses_utc_and_has_no_client_details(http, booked, client):
    request, slots = booked
    token = visit_links.issue(request.id)
    response = await http.get(f'/visit/{token}/calendar.ics')
    assert response.status_code == 200
    assert response.headers['content-type'].startswith('text/calendar')
    assert response.headers['cache-control'] == 'no-store'
    assert 'attachment;' in response.headers['content-disposition']
    assert 'DTSTART:' + slots[0].astimezone(UTC).strftime('%Y%m%dT%H%M%SZ') in response.text
    assert client.full_name not in response.text
    assert client.phone not in response.text
    assert token not in response.text
    assert 'BEGIN:VEVENT' in response.text
    assert all(len(line) <= 75 for line in response.content.split(b'\r\n'))


async def test_cancelled_visit_has_no_calendar(http, booked):
    request, _ = booked
    token = visit_links.issue(request.id)
    await http.post(f'/visit/{token}/cancel')
    assert (await http.get(f'/visit/{token}/calendar.ics')).status_code == 410


async def test_closing_request_and_moving_concurrently_leaves_no_live_booking(http, engine, session, booked):
    request, slots = booked
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def close():
        async with maker() as s:
            item = await s.get(Request, request.id)
            await transition_request(s, request=item, target=RequestStatus.CANCELLED)
            await s.commit()

    await asyncio.wait_for(asyncio.gather(close(), http.post(
        f'/visit/{visit_links.issue(request.id)}/move', data={'slot': slots[1].isoformat()}
    )), timeout=10)
    saved = list(await session.scalars(select(Appointment).where(
        Appointment.request_id == request.id, Appointment.is_cancelled.is_(False))))
    assert saved == []


async def test_page_does_not_leak_personal_data(http, booked, client):
    """Ссылку пересылают в мессенджерах, и она переживёт получателя."""
    request, _ = booked
    page = await http.get(f"/visit/{visit_links.issue(request.id)}")
    assert client.phone not in page.text
    assert client.full_name not in page.text


async def test_broken_link_is_gone(http):
    assert (await http.get("/visit/явно-не-подпись")).status_code == 410


# --- перенос -----------------------------------------------------------------


async def test_client_moves_the_visit(http, session, booked):
    request, slots = booked
    token = visit_links.issue(request.id)

    response = await http.post(f"/visit/{token}/move", data={"slot": slots[1].isoformat()})
    assert response.status_code == 303

    fresh = await session.scalar(
        select(Appointment).where(
            Appointment.request_id == request.id, Appointment.is_cancelled.is_(False)
        )
    )
    assert fresh.starts_at == slots[1]


async def test_move_is_written_to_history(http, session, booked):
    request, slots = booked
    await http.post(
        f"/visit/{visit_links.issue(request.id)}/move", data={"slot": slots[1].isoformat()}
    )

    event = await session.scalar(
        select(RequestEvent).where(RequestEvent.comment.contains("перенёс"))
    )
    assert event is not None
    assert event.actor_staff_id is None, "переносил клиент, а не сотрудник"


async def test_taken_slot_leaves_the_old_time(http, session, booked, tenant, visit_service):
    """Окно заняли, пока клиент выбирал.

    Остаться вообще без записи хуже, чем не перенести, поэтому прежнее время
    обязано вернуться.
    """
    request, slots = booked

    other = await create_request(
        session, tenant=tenant, client=request.client, service=visit_service,
        channel=Channel.WIDGET,
    )
    await session.commit()
    await book_slot(session, request=other, service=visit_service, starts_at=slots[1])
    await session.commit()

    response = await http.post(
        f"/visit/{visit_links.issue(request.id)}/move", data={"slot": slots[1].isoformat()}
    )
    assert response.status_code == 303

    kept = await session.scalar(
        select(Appointment).where(
            Appointment.request_id == request.id, Appointment.is_cancelled.is_(False)
        )
    )
    assert kept is not None, "клиент остался без записи — так нельзя"
    assert kept.starts_at == slots[0]


async def test_garbage_time_is_rejected(http, booked):
    request, _ = booked
    response = await http.post(
        f"/visit/{visit_links.issue(request.id)}/move", data={"slot": "не время"}
    )
    assert response.status_code == 410


# --- отмена ------------------------------------------------------------------


async def test_client_cancels(http, session, booked):
    request, _ = booked
    page = await http.post(f"/visit/{visit_links.issue(request.id)}/cancel")
    assert page.status_code == 200
    assert "отменена" in page.text.lower()

    left = await session.scalar(
        select(Appointment).where(
            Appointment.request_id == request.id, Appointment.is_cancelled.is_(False)
        )
    )
    assert left is None


async def test_cancelled_link_stops_working(http, booked):
    request, _ = booked
    token = visit_links.issue(request.id)
    await http.post(f"/visit/{token}/cancel")
    assert (await http.get(f"/visit/{token}")).status_code == 410


async def test_freed_slot_becomes_available_again(
    http, session, booked, tenant, visit_service
):
    request, slots = booked
    before = await available_slots(session, tenant=tenant, service=visit_service)
    assert slots[0] not in before

    await http.post(f"/visit/{visit_links.issue(request.id)}/cancel")

    after = await available_slots(session, tenant=tenant, service=visit_service)
    assert slots[0] in after


# --- напоминание -------------------------------------------------------------


def test_reminder_lists_only_missing_documents(tenant, session):
    """Перечислять снова присланное — верный способ, чтобы список не читали."""
    from app.models import Request, RequestStatus, SubmissionMode
    from app.reminders import render_reminder

    request = Request(
        tenant_id=tenant.id,
        public_number=1,
        service_title="Доверенность",
        submission_mode=SubmissionMode.VISIT,
        status=RequestStatus.NEW,
        channel=Channel.WIDGET,
        checklist=[
            {"title": "Паспорт", "description": "", "is_required": True},
            {"title": "СТС", "description": "", "is_required": True},
        ],
        received_documents=[0],
    )
    text = render_reminder(tenant, request, "завтра в 10:00", True, visit_url="https://x/visit/t")
    assert "СТС" in text
    assert "Паспорт" not in text
    assert "https://x/visit/t" in text


def test_reminder_falls_back_to_phone_without_link(tenant):
    from app.models import Request, RequestStatus, SubmissionMode
    from app.reminders import render_reminder

    tenant.phone = "+7 495 000-00-00"
    request = Request(
        tenant_id=tenant.id,
        public_number=1,
        service_title="Доверенность",
        submission_mode=SubmissionMode.VISIT,
        status=RequestStatus.NEW,
        channel=Channel.WIDGET,
        checklist=[],
        received_documents=[],
    )
    text = render_reminder(tenant, request, "завтра в 10:00", True)
    assert tenant.phone in text
