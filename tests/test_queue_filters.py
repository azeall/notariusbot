"""Queue filtering must narrow the existing tenant-scoped work lists."""

from datetime import UTC, datetime
from urllib.parse import urlencode

import pytest
from sqlalchemy import select
from starlette.requests import Request as HttpRequest

from app.models import (
    Channel, Client, ParticipationStatus, Request, RequestParticipant,
    RequestStatus, Staff, StaffRole, SubmissionMode,
)
from app.web.staff import queue


@pytest.fixture
async def queue_rows(session, tenant, other_tenant, employee, second_employee):
    rows = {}
    for number, (key, name, phone, state, lead, tenant_id, created) in enumerate([
        ("free", "Анна Смирнова", "+7 (999) 123-45-67", RequestStatus.NEW,
         None, tenant.id, datetime(2026, 9, 15, 21, tzinfo=UTC)),
        ("mine", "Борис Петров", "+79990000002", RequestStatus.CLAIMED,
         employee.id, tenant.id, datetime(2026, 9, 16, 20, 59, 59, tzinfo=UTC)),
        ("helping", "Вера Соколова", "+79990000003", RequestStatus.AWAITING_DOCUMENTS,
         second_employee.id, tenant.id, datetime(2026, 9, 16, 21, tzinfo=UTC)),
        ("other", "Галина Орлова", "+79990000004", RequestStatus.AWAITING_VISIT,
         second_employee.id, tenant.id, datetime(2026, 9, 15, 20, 59, 59, tzinfo=UTC)),
        ("closed", "Анна Завершённая", "+79991234567", RequestStatus.COMPLETED,
         employee.id, tenant.id, datetime(2026, 9, 16, 12, tzinfo=UTC)),
        ("foreign", "Анна Чужая", "+79991234567", RequestStatus.NEW,
         None, other_tenant.id, datetime(2026, 9, 16, 12, tzinfo=UTC)),
    ], start=1):
        person = Client(tenant_id=tenant_id, channel=Channel.WIDGET,
                        external_id=f"queue-{key}", full_name=name, phone=phone)
        session.add(person)
        await session.flush()
        row = Request(tenant_id=tenant_id, client_id=person.id, public_number=number,
                      service_title=f"Услуга {key}", submission_mode=SubmissionMode.DOCUMENTS,
                      channel=Channel.WIDGET, status=state, assigned_staff_id=lead,
                      created_at=created, claimed_at=created if lead else None, checklist=[])
        session.add(row)
        await session.flush()
        rows[key] = row
    session.add(RequestParticipant(
        tenant_id=tenant.id, request_id=rows["helping"].id, staff_id=employee.id,
        status=ParticipationStatus.ACTIVE,
    ))
    foreign_staff = Staff(tenant_id=other_tenant.id, full_name="Чужой сотрудник",
                          email="foreign@example.com", password_hash="unused",
                          role=StaffRole.EMPLOYEE)
    session.add(foreign_staff)
    await session.commit()
    rows["foreign_staff"] = foreign_staff
    return rows


async def page(session, employee, **params):
    request = HttpRequest({"type": "http", "method": "GET", "path": "/staff",
                           "query_string": urlencode(params).encode(),
                           "headers": [], "scheme": "http", "server": ("test", 80)})
    return await queue(request, staff=employee, session=session)


def visible(response):
    return {row.id for key in ("unclaimed", "mine", "others")
            for row in response.context[key]}


async def test_default_queue_preserves_lead_helper_and_other_groups(session, employee, queue_rows):
    response = await page(session, employee)
    assert {r.id for r in response.context["mine"]} == {
        queue_rows["mine"].id, queue_rows["helping"].id,
    }
    assert [r.id for r in response.context["others"]] == [queue_rows["other"].id]
    assert visible(response) == {queue_rows[key].id for key in ("free", "mine", "helping", "other")}
    assert f'href="/staff/requests/{queue_rows["free"].id}"' in response.body.decode()


@pytest.mark.parametrize("query", ["  аНнА  ", "9991234567", "+7 (999) 123-45-67", "8 999 123 45 67"])
async def test_search_name_or_formatted_phone_is_tenant_scoped(session, employee, queue_rows, query):
    response = await page(session, employee, q=query)
    assert visible(response) == {queue_rows["free"].id}


@pytest.mark.parametrize("query", ["алёна", "АЛЁНА", "аЛёНа", "сёмина"])
async def test_russian_name_search_folds_yo_independently_of_database_locale(
    session, employee, queue_rows, query,
):
    person = await session.scalar(select(Client).where(
        Client.id == queue_rows["free"].client_id,
        Client.tenant_id == employee.tenant_id,
    ))
    person.full_name = "АЛЁНА СЁМИНА"
    await session.commit()
    response = await page(session, employee, q=query)
    assert visible(response) == {queue_rows["free"].id}


@pytest.mark.parametrize("query", ["%", "_", "nobody-matches"])
async def test_search_is_literal_and_empty_result_does_not_claim_queue_is_clear(
    session, employee, queue_rows, query,
):
    response = await page(session, employee, q=query)
    assert visible(response) == set()
    assert response.context["new_count"] == 1
    assert "Найдено заявок: 0" in response.body.decode()
    assert "Все заявки разобраны" not in response.body.decode()


async def test_status_assignee_and_search_combine_without_losing_helper_visibility(
    session, employee, second_employee, queue_rows,
):
    response = await page(session, employee, q="вера", status="awaiting_documents",
                          assignee=str(second_employee.id))
    assert visible(response) == {queue_rows["helping"].id}
    assert [r.id for r in response.context["mine"]] == [queue_rows["helping"].id]


async def test_unassigned_filter(session, employee, queue_rows):
    assert visible(await page(session, employee, assignee="unassigned")) == {queue_rows["free"].id}


async def test_received_date_range_includes_whole_tenant_local_day(session, employee, queue_rows):
    response = await page(session, employee, date_from="2026-09-16", date_to="2026-09-16")
    assert visible(response) == {queue_rows["free"].id, queue_rows["mine"].id}
    assert "16.09.2026 00:00" in response.body.decode()


async def test_date_filter_uses_configured_tenant_timezone(session, tenant, employee, queue_rows):
    tenant.timezone = "Asia/Yekaterinburg"
    await session.commit()
    response = await page(session, employee, date_from="2026-09-16", date_to="2026-09-16")
    assert visible(response) == {queue_rows["free"].id, queue_rows["other"].id}


async def test_foreign_assignee_cannot_reveal_requests_or_staff(session, employee, queue_rows):
    response = await page(session, employee, assignee=str(queue_rows["foreign_staff"].id))
    assert not visible(response)
    assert "Чужой сотрудник" not in response.body.decode()


@pytest.mark.parametrize("params", [
    {"date_from": "2026-09-18", "date_to": "2026-09-16"},
    {"date_from": "not-a-date"}, {"status": "unknown"}, {"assignee": "broken"},
])
async def test_invalid_filters_show_recoverable_error(session, employee, queue_rows, params):
    response = await page(session, employee, **params)
    assert response.status_code == 400
    assert response.context["filter_error"]
    assert not visible(response)
    assert 'href="/staff"' in response.body.decode()
