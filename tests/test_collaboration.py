"""Совместная работа над заявкой и права сотрудников.

Раньше проверок не было вовсе: любой сотрудник мог сменить статус чужой заявки.
Здесь закреплено, кто что может, — перепутать такое легко, а последствия
тихие: двое незаметно правят одно дело.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain import access
from app.domain.requests import claim_request, create_request
from app.models import Channel, ParticipationStatus, Request, RequestParticipant
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
async def taken_request(session, tenant, client, service, employee):
    """Заявка, которую ведёт employee."""
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    await session.refresh(request)
    return request


async def _login(http, tenant, person, password="secret123"):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": person.email, "password": password}
    )


# --- права ------------------------------------------------------------------


async def test_lead_can_edit(session, taken_request, employee):
    rights = access.evaluate(taken_request, employee, participants=[])
    assert rights.can_edit
    assert rights.is_lead
    assert rights.can_manage_participants


async def test_colleague_sees_but_cannot_edit(session, taken_request, second_employee):
    rights = access.evaluate(taken_request, second_employee, participants=[])
    assert rights.can_view
    assert not rights.can_edit
    assert rights.can_ask_to_join


async def test_owner_can_edit_anything(session, taken_request, owner):
    rights = access.evaluate(taken_request, owner, participants=[])
    assert rights.can_edit
    assert rights.can_manage_participants
    assert not rights.is_lead


async def test_unclaimed_is_editable_by_anyone(session, tenant, client, service, second_employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    assert access.evaluate(request, second_employee, participants=[]).can_edit


async def test_other_tenant_sees_nothing(session, taken_request, other_tenant, second_employee):
    second_employee.tenant_id = other_tenant.id
    await session.commit()
    rights = access.evaluate(taken_request, second_employee, participants=[])
    assert not rights.can_view
    assert not rights.can_edit


# --- через приложение -------------------------------------------------------


async def test_colleague_cannot_change_status(http, tenant, taken_request, second_employee):
    await _login(http, tenant, second_employee)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/status",
        data={"target": "completed", "comment": "я мимо проходил"},
    )
    assert response.status_code == 403
    assert "ведёт другой сотрудник" in response.json()["detail"]


async def test_colleague_can_open_request(http, tenant, taken_request, second_employee):
    await _login(http, tenant, second_employee)
    page = await http.get(f"/staff/requests/{taken_request.id}")
    assert page.status_code == 200
    assert "Попроситься в работу" in page.text
    assert "Изменить статус" not in page.text


async def test_lead_can_change_status(http, tenant, taken_request, employee):
    await _login(http, tenant, employee)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/status",
        data={"target": "awaiting_documents", "comment": "жду паспорт"},
    )
    assert response.status_code == 303


async def test_owner_can_change_someone_elses_request(http, tenant, taken_request, owner):
    await _login(http, tenant, owner)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/status",
        data={"target": "awaiting_documents", "comment": "проверил"},
    )
    assert response.status_code == 303


# --- просьба о помощи -------------------------------------------------------


async def test_join_request_and_acceptance(
    http, session, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, second_employee)
    asked = await http.post(
        f"/staff/requests/{taken_request.id}/join", data={"note": "помогу со сделкой"}
    )
    assert asked.status_code == 303

    row = await session.scalar(select(RequestParticipant))
    assert row.status is ParticipationStatus.REQUESTED
    assert row.note == "помогу со сделкой"

    # Ведущий видит просьбу в очереди.
    await http.post("/staff/logout")
    await _login(http, tenant, employee)
    queue = await http.get("/staff")
    assert "Просятся в помощь" in queue.text
    assert second_employee.full_name in queue.text

    accepted = await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide",
        data={"accept": "1"},
    )
    assert accepted.status_code == 303
    await session.refresh(row)
    assert row.status is ParticipationStatus.ACTIVE


async def test_accepted_helper_can_edit(
    http, session, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, second_employee)
    await http.post(f"/staff/requests/{taken_request.id}/join")
    row = await session.scalar(select(RequestParticipant))

    await http.post("/staff/logout")
    await _login(http, tenant, employee)
    await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide",
        data={"accept": "1"},
    )

    await http.post("/staff/logout")
    await _login(http, tenant, second_employee)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/status",
        data={"target": "awaiting_documents", "comment": "запросил документы"},
    )
    assert response.status_code == 303, "подключённый помощник должен уметь менять заявку"


async def test_declined_helper_still_cannot_edit(
    http, session, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, second_employee)
    await http.post(f"/staff/requests/{taken_request.id}/join")
    row = await session.scalar(select(RequestParticipant))

    await http.post("/staff/logout")
    await _login(http, tenant, employee)
    await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide", data={}
    )
    await session.refresh(row)
    assert row.status is ParticipationStatus.DECLINED

    await http.post("/staff/logout")
    await _login(http, tenant, second_employee)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/status", data={"target": "completed"}
    )
    assert response.status_code == 403


async def test_colleague_cannot_decide_for_lead(
    http, session, tenant, taken_request, second_employee
):
    """Решает ведущий, а не тот, кто просится."""
    await _login(http, tenant, second_employee)
    await http.post(f"/staff/requests/{taken_request.id}/join")
    row = await session.scalar(select(RequestParticipant))

    response = await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide",
        data={"accept": "1"},
    )
    assert response.status_code == 403


async def test_owner_adds_helper_without_asking(
    http, session, tenant, taken_request, owner, second_employee
):
    await _login(http, tenant, owner)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/participants",
        data={"staff_id": str(second_employee.id)},
    )
    assert response.status_code == 303

    row = await session.scalar(select(RequestParticipant))
    assert row.status is ParticipationStatus.ACTIVE
    assert row.staff_id == second_employee.id


async def test_employee_cannot_add_helper_directly(
    http, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, employee)
    response = await http.post(
        f"/staff/requests/{taken_request.id}/participants",
        data={"staff_id": str(second_employee.id)},
    )
    assert response.status_code == 400


async def test_helper_can_be_removed(
    http, session, tenant, taken_request, owner, second_employee
):
    await _login(http, tenant, owner)
    await http.post(
        f"/staff/requests/{taken_request.id}/participants",
        data={"staff_id": str(second_employee.id)},
    )
    row = await session.scalar(select(RequestParticipant))

    response = await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/remove"
    )
    assert response.status_code == 303
    await session.refresh(row)
    assert row.status is ParticipationStatus.LEFT


async def test_lead_cannot_ask_to_join_own_request(http, tenant, taken_request, employee):
    await _login(http, tenant, employee)
    response = await http.post(f"/staff/requests/{taken_request.id}/join")
    assert response.status_code == 400
    assert "и так ведёте" in response.json()["detail"]


# --- очередь ----------------------------------------------------------------


async def test_queue_shows_others_requests(http, tenant, taken_request, second_employee):
    await _login(http, tenant, second_employee)
    page = await http.get("/staff")
    assert "У других сотрудников" in page.text
    assert f"№ {taken_request.public_number}" in page.text


async def test_owner_queue_shows_who_leads(http, tenant, taken_request, owner, employee):
    await _login(http, tenant, owner)
    page = await http.get("/staff")
    assert "В работе у сотрудников" in page.text
    assert employee.full_name in page.text


async def test_helper_sees_request_among_own(
    http, session, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, second_employee)
    await http.post(f"/staff/requests/{taken_request.id}/join")
    row = await session.scalar(select(RequestParticipant))

    await http.post("/staff/logout")
    await _login(http, tenant, employee)
    await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide",
        data={"accept": "1"},
    )

    await http.post("/staff/logout")
    await _login(http, tenant, second_employee)
    page = await http.get("/staff")
    assert "помогаю" in page.text


async def test_history_records_collaboration(
    http, session, tenant, taken_request, employee, second_employee
):
    await _login(http, tenant, second_employee)
    await http.post(f"/staff/requests/{taken_request.id}/join", data={"note": "нужен второй"})
    row = await session.scalar(select(RequestParticipant))

    await http.post("/staff/logout")
    await _login(http, tenant, employee)
    await http.post(
        f"/staff/requests/{taken_request.id}/participants/{row.id}/decide",
        data={"accept": "1"},
    )

    page = await http.get(f"/staff/requests/{taken_request.id}")
    assert "Просится в работу" in page.text
    assert "Подключил" in page.text


async def test_request_of_another_tenant_is_hidden(
    http, session, tenant, other_tenant, taken_request, second_employee
):
    second_employee.tenant_id = other_tenant.id
    await session.commit()
    await _login(http, tenant, second_employee)
    # Вход не пройдёт: сотрудник больше не принадлежит этому нотариусу.
    assert (await http.get(f"/staff/requests/{taken_request.id}")).status_code in (401, 404)


async def test_participants_do_not_leak_between_requests(
    session, tenant, client, service, employee, second_employee
):
    first = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    second = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await claim_request(session, request_id=first.id, staff=employee)
    await claim_request(session, request_id=second.id, staff=employee)
    await session.commit()

    session.add(
        RequestParticipant(
            tenant_id=tenant.id,
            request_id=first.id,
            staff_id=second_employee.id,
            status=ParticipationStatus.ACTIVE,
        )
    )
    await session.commit()

    loaded = await session.get(Request, second.id)
    rights = access.evaluate(
        loaded,
        second_employee,
        participants=[
            p
            for p in await session.scalars(
                select(RequestParticipant).where(RequestParticipant.request_id == second.id)
            )
        ],
    )
    assert not rights.can_edit
