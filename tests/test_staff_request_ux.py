"""Render the real staff route without a database; keep permission controls honest."""

import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request as HttpRequest

from app.models import (
    Attachment, Channel, Client, ParticipationStatus, Request, RequestEvent, RequestParticipant,
    RequestStatus, Staff, StaffRole, SubmissionMode, Tenant,
)
from app.web import staff as staff_web


class Controls(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.actions = []
        self.links = []
        self.submit_handlers = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.actions.append(attrs.get("action"))
            if "onsubmit" in attrs:
                self.submit_handlers.append(attrs["onsubmit"])
        if tag == "a":
            self.links.append(attrs.get("href"))


@pytest.fixture
def detail_data():
    tenant = Tenant(id=uuid.uuid4(), display_name="Нотариус", timezone="Europe/Moscow")
    employee = Staff(id=uuid.uuid4(), tenant_id=tenant.id, full_name="Помощник",
                     role=StaffRole.EMPLOYEE)
    req = Request(
        id=uuid.uuid4(), tenant_id=tenant.id, public_number=17, service_title="Доверенность",
        status=RequestStatus.NEW, assigned_staff_id=None, assigned_staff=None,
        submission_mode=SubmissionMode.DOCUMENTS, channel=Channel.WIDGET,
        created_at=datetime(2026, 9, 15, 21, tzinfo=UTC),
        client=Client(full_name="Анна Смирнова", phone="+79991234567"),
        checklist=[{"title": "Паспорт", "is_required": True}], received_documents=[],
        attachments=[Attachment(id=uuid.uuid4(), original_filename="Паспорт.pdf",
                                content_type="application/pdf", size_bytes=2048)],
        participants=[], events=[RequestEvent(
            to_status="new", comment="Заявка поступила", actor_label="Клиент",
            created_at=datetime(2026, 9, 15, 21, tzinfo=UTC),
        )],
    )
    return tenant, employee, req


async def render(monkeypatch, data):
    tenant, employee, req = data
    monkeypatch.setattr(staff_web, "_load_request", AsyncMock(return_value=req))
    session = AsyncMock()
    session.get.return_value = tenant
    session.scalars.return_value = []
    http_request = HttpRequest({"type": "http", "method": "GET", "path": f"/staff/requests/{req.id}",
                                "query_string": b"", "headers": [], "scheme": "http",
                                "server": ("test", 80)})
    return await staff_web.request_detail(req.id, http_request, staff=employee, session=session)


@pytest.mark.parametrize("role", [StaffRole.EMPLOYEE, StaffRole.OWNER])
async def test_free_request_preview_offers_take_without_edit_controls(monkeypatch, detail_data, role):
    _, employee, req = detail_data
    employee.role = role
    response = await render(monkeypatch, detail_data)
    html = response.body.decode()
    actions = Controls(html).actions
    assert f"/staff/requests/{req.id}/claim" in actions
    assert not [action for action in actions if action.startswith(f"/staff/requests/{req.id}/")
                and not action.endswith("/claim")]
    assert "Паспорт" in html and "Заявка поступила" in html
    assert "16.09.2026 00:00" in html
    assert f"/staff/requests/{req.id}/documents/{req.attachments[0].id}" in Controls(html).links
    assert req.status == RequestStatus.NEW and req.assigned_staff_id is None
    assert ("/admin/calendar" in Controls(html).links) == (role == StaffRole.OWNER)


@pytest.mark.parametrize("kind", ["lead", "helper", "owner", "colleague"])
async def test_claimed_request_keeps_edit_and_collaboration_permissions(monkeypatch, detail_data, kind):
    tenant, employee, req = detail_data
    lead_id = employee.id if kind == "lead" else uuid.uuid4()
    req.status = RequestStatus.CLAIMED
    req.assigned_staff_id = lead_id
    req.assigned_staff = Staff(id=lead_id, full_name="Ведущий")
    req.claimed_at = datetime(2026, 9, 16, 1, tzinfo=UTC)
    if kind == "owner":
        employee.role = StaffRole.OWNER
    if kind == "helper":
        req.participants = [RequestParticipant(
            id=uuid.uuid4(), tenant_id=tenant.id, staff_id=employee.id,
            staff=employee, status=ParticipationStatus.ACTIVE,
        )]
    response = await render(monkeypatch, detail_data)
    html = response.body.decode()
    actions = Controls(html).actions
    editable = kind != "colleague"
    assert (f"/staff/requests/{req.id}/status" in actions) == editable
    assert (f"/staff/requests/{req.id}/checklist/0" in actions) == editable
    assert (f"/staff/requests/{req.id}/upload-link" in actions) == editable
    assert (f"/staff/requests/{req.id}/join" in actions) == (kind == "colleague")
    assert (f"/staff/requests/{req.id}/documents/{req.attachments[0].id}/delete" in actions) == (kind == "owner")
    assert f"/staff/requests/{req.id}/claim" not in actions
    assert "Заявка поступила" in html
    assert "/staff" in Controls(html).links


@pytest.mark.parametrize("filename", ["O'Brien.pdf", "'); alert('filename'); // .pdf"])
async def test_delete_confirmation_does_not_interpolate_filename(monkeypatch, detail_data, filename):
    _, employee, req = detail_data
    employee.role = StaffRole.OWNER
    req.status = RequestStatus.CLAIMED
    req.assigned_staff_id = employee.id
    req.assigned_staff = employee
    req.attachments[0].original_filename = filename
    response = await render(monkeypatch, detail_data)
    controls = Controls(response.body.decode())
    assert controls.submit_handlers == [
        "return confirm('Удалить документ без возможности восстановить?')"
    ]
    assert f"/staff/requests/{req.id}/documents/{req.attachments[0].id}/delete" in controls.actions


@pytest.mark.parametrize("minutes, expected", [(0, "менее минуты"), (65, "1 ч 5 мин"), (1500, "1 д 1 ч")])
def test_waiting_time_and_local_received_date(detail_data, minutes, expected):
    from datetime import timedelta
    from zoneinfo import ZoneInfo

    req = detail_data[2]
    timing = staff_web._request_timing(req, req.created_at + timedelta(minutes=minutes),
                                       ZoneInfo("Europe/Moscow"))
    assert timing["waiting"] == expected
    assert timing["received"].strftime("%d.%m.%Y %H:%M") == "16.09.2026 00:00"


def test_waiting_stops_when_claimed(detail_data):
    from zoneinfo import ZoneInfo

    req = detail_data[2]
    req.claimed_at = datetime(2026, 9, 15, 22, tzinfo=UTC)
    timing = staff_web._request_timing(req, datetime(2026, 9, 20, tzinfo=UTC),
                                       ZoneInfo("Europe/Moscow"))
    assert timing["waiting"] == "1 ч 0 мин"


@pytest.mark.parametrize("role, is_lead, can_manage", [
    (StaffRole.OWNER, False, True), (StaffRole.EMPLOYEE, True, True),
    (StaffRole.EMPLOYEE, False, False),
])
async def test_participant_controls_keep_existing_management_rights(
    monkeypatch, detail_data, role, is_lead, can_manage,
):
    tenant, employee, req = detail_data
    employee.role = role
    req.status = RequestStatus.CLAIMED
    req.assigned_staff_id = employee.id if is_lead else uuid.uuid4()
    req.assigned_staff = Staff(id=req.assigned_staff_id, full_name="Ведущий")
    helper = Staff(id=uuid.uuid4(), tenant_id=tenant.id, full_name="Коллега")
    active = RequestParticipant(id=uuid.uuid4(), staff_id=helper.id,
                                staff=helper, status=ParticipationStatus.ACTIVE)
    pending = RequestParticipant(id=uuid.uuid4(), staff_id=uuid.uuid4(),
                                 staff=helper, status=ParticipationStatus.REQUESTED)
    req.participants = [active, pending]
    response = await render(monkeypatch, detail_data)
    actions = Controls(response.body.decode()).actions
    for person, suffix in ((active, "remove"), (pending, "decide")):
        assert (f"/staff/requests/{req.id}/participants/{person.id}/{suffix}" in actions) == can_manage
