"""Owner schedule, booked calendar and lossless document editing.

Run TestDatabase serially: its fixtures reset the shared PostgreSQL test DB.
"""

from datetime import UTC, datetime, time, timedelta
from html.parser import HTMLParser
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.datastructures import FormData
from starlette.requests import Request as HttpRequest

from app.models import Appointment, Channel, Request, Service, ServiceDocument, StaffRole, SubmissionMode
from app.web import admin
from app.web.deps import current_staff, db_session, issue_session_cookie, SESSION_COOKIE
from app.web.main import TEMPLATES, app


class Fields(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.inputs = []
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input":
            self.inputs.append(attrs)
        if tag == "a":
            self.links.append(attrs)


def test_schedule_renders_one_labelled_set_of_fields_per_day():
    html = TEMPLATES.get_template("admin_schedule.html").render(
        staff=None, tenant=SimpleNamespace(timezone="Europe/Moscow"),
        rows={0: SimpleNamespace(is_working=True, opens_at=time(8, 30), closes_at=time(17),
                                break_starts_at=time(12), break_ends_at=time(13))},
        weekday_names=admin.WEEKDAY_NAMES, days_off=[], today="2026-09-17",
    )
    fields = Fields(html)
    assert "<table" not in html
    assert html.count('class="schedule-day"') == 7
    assert any(link.get("href") == "/admin/calendar" for link in fields.links)
    for day in range(7):
        for prefix in ("working", "opens", "closes", "break_start", "break_end"):
            inputs = [field for field in fields.inputs if field.get("name") == f"{prefix}_{day}"]
            assert len(inputs) == 1
    assert next(f for f in fields.inputs if f.get("name") == "opens_0")["value"] == "08:30"


def test_document_rows_preserve_punctuation_newlines_and_unchecked_required():
    form = FormData([
        ("document_row", "0"), ("document_title_0", "? Паспорт — оригинал"),
        ("document_description_0", "Первая строка\nВторая — без потери"),
        ("document_required_0", "1"),
        ("document_row", "4"), ("document_title_4", "Согласие"),
        ("document_description_4", "Не всегда"),
    ])
    assert admin.document_rows_from_form(form) == [
        {"title": "? Паспорт — оригинал", "description": "Первая строка\nВторая — без потери", "is_required": True},
        {"title": "Согласие", "description": "Не всегда", "is_required": False},
    ]


@pytest.mark.parametrize("role, expected", [(None, 401), (StaffRole.EMPLOYEE, 403)])
async def test_calendar_requires_owner_without_querying_database(role, expected):
    isolated = FastAPI()
    isolated.include_router(admin.router)
    isolated.dependency_overrides[current_staff] = lambda: (
        SimpleNamespace(can_manage_catalog=False) if role else None
    )
    if role is None:
        isolated.dependency_overrides.pop(current_staff)

    async def no_database():
        yield None

    isolated.dependency_overrides[db_session] = no_database
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url="http://test") as http:
        response = await http.get("/admin/calendar")
    assert response.status_code == expected


@pytest.fixture
async def draft_http():
    """Draft editing needs tenant branding, but never reads/writes service data."""
    isolated = FastAPI()
    isolated.include_router(admin.router)
    owner = SimpleNamespace(tenant_id="test-tenant", can_manage_catalog=True, full_name="Владелец")
    tenant = SimpleNamespace(display_name="Нотариус", timezone="Europe/Moscow")

    class TenantSession:
        async def get(self, model, tenant_id):
            return tenant

        async def scalar(self, query):
            raise AssertionError("Invalid IDs and new drafts must not query a service")

    isolated.dependency_overrides[current_staff] = lambda: owner
    isolated.dependency_overrides[db_session] = lambda: TenantSession()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated), base_url="http://test") as http:
        yield http


async def test_new_service_rows_work_without_js_or_saving(draft_http):
    data = service_data(SimpleNamespace(id="", slug=""))
    data.pop("is_active")
    response = await draft_http.post("/admin/services/documents", data={**data, "document_action": "add"})
    assert response.status_code == 200
    fields = Fields(response.text).inputs
    assert [f["value"] for f in fields if f.get("name") == "document_row"] == ["0", "1", "2"]
    for name in ("title", "slug", "description", "visit_duration_minutes", "lead_time_note", "price_note", "keywords", "sort_order"):
        assert next(f for f in fields if f.get("name") == name)["value"] == data[name]
    assert "checked" not in next(f for f in fields if f.get("name") == "is_active")
    assert "checked" not in next(f for f in fields if f.get("name") == "document_required_1")
    assert data["document_description_0"] in response.text
    assert '<option value="visit" selected' in response.text
    assert '<h1>Новая услуга</h1>' in response.text
    assert "/static/admin.css" in response.text

    response = await draft_http.post("/admin/services/documents", data={**data, "document_action": "remove:0"})
    assert response.status_code == 200
    fields = Fields(response.text).inputs
    assert next(f for f in fields if f.get("name") == "document_title_0")["value"] == "Согласие"
    assert "checked" not in next(f for f in fields if f.get("name") == "document_required_0")


async def test_document_editor_rejects_malformed_service_id(draft_http):
    response = await draft_http.post("/admin/services/documents", data={
        "service_id": "not-a-uuid", "document_action": "add",
    })
    assert response.status_code == 400


@pytest.mark.parametrize("title", ["", "Д" * 256])
async def test_invalid_document_title_returns_draft_without_losing_data(draft_http, title):
    data = service_data(SimpleNamespace(id="", slug=""))
    data["document_title_0"] = title
    response = await draft_http.post("/admin/services", data=data)
    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert data["document_description_0"] in response.text
    assert "Согласие" in response.text
    fields = Fields(response.text).inputs
    assert next(f for f in fields if f.get("name") == "title")["value"] == data["title"]


@pytest.mark.parametrize("timezone, week, start, end", [
    ("Asia/Yekaterinburg", "2026-09-17", "2026-09-13T19:00:00+00:00", "2026-09-20T19:00:00+00:00"),
    ("Europe/Berlin", "2026-03-29", "2026-03-22T23:00:00+00:00", "2026-03-29T22:00:00+00:00"),
])
async def test_calendar_queries_utc_bounds_of_tenant_week(timezone, week, start, end):
    queries = []

    class CalendarSession:
        async def get(self, model, tenant_id):
            return SimpleNamespace(timezone=timezone, display_name="Нотариус")

        async def execute(self, query):
            queries.append(query)
            return []

    response = await admin.calendar_page(
        HttpRequest({"type": "http", "method": "GET", "path": "/admin/calendar", "headers": []}),
        week=week,
        owner=SimpleNamespace(tenant_id="tenant", can_manage_catalog=True, full_name="Владелец"),
        session=CalendarSession(),
    )
    assert response.status_code == 200
    params = queries[0].compile().params
    assert params["starts_at_1"] == datetime.fromisoformat(start)
    assert params["starts_at_2"] == datetime.fromisoformat(end)
    assert params["starts_at_1"].tzinfo is UTC
    assert params["starts_at_2"].tzinfo is UTC


@pytest.fixture
async def admin_http(engine):
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
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
async def owner_http(admin_http, owner):
    admin_http.cookies.set(SESSION_COOKIE, issue_session_cookie(owner)[0])
    return admin_http


def service_data(service):
    return {
        "service_id": str(service.id), "title": "Исправленная услуга", "slug": service.slug,
        "description": "Несохранённое описание", "submission_mode": "visit",
        "visit_duration_minutes": "45", "lead_time_note": "Два дня", "price_note": "По тарифу",
        "keywords": "одно, два", "sort_order": "7", "is_active": "1", "documents_editor": "rows",
        "document_row": ["0", "1"], "document_title_0": "? Паспорт — оригинал",
        "document_description_0": "Строка 1\nСтрока 2 — пояснение", "document_required_0": "1",
        "document_title_1": "Согласие", "document_description_1": "При наличии",
    }


class TestDatabase:
    async def test_document_add_remove_without_js_preserves_unsaved_fields(self, owner_http, session, service):
        data = service_data(service)
        original_title = service.title
        response = await owner_http.post("/admin/services/documents", data={**data, "document_action": "add"})
        assert response.status_code == 200
        fields = Fields(response.text).inputs
        assert [f["value"] for f in fields if f.get("name") == "document_row"] == ["0", "1", "2"]
        for name in ("title", "slug", "description", "visit_duration_minutes", "lead_time_note", "price_note", "keywords", "sort_order"):
            assert next(f for f in fields if f.get("name") == name)["value"] == data[name]
        assert '<option value="visit" selected' in response.text
        assert data["document_description_0"] in response.text
        assert "checked" not in next(f for f in fields if f.get("name") == "document_required_1")
        await session.refresh(service)
        assert service.title == original_title

        response = await owner_http.post("/admin/services/documents", data={**data, "document_action": "remove:0"})
        assert response.status_code == 200
        fields = Fields(response.text).inputs
        assert next(f for f in fields if f.get("name") == "document_title_0")["value"] == "Согласие"
        assert "checked" not in next(f for f in fields if f.get("name") == "document_required_0")

    async def test_structured_save_preserves_documents_and_legacy_post(self, owner_http, session, service):
        data = service_data(service)
        response = await owner_http.post("/admin/services", data=data)
        assert response.status_code == 303
        await session.refresh(service, ["documents"])
        assert [(d.title, d.description, d.is_required) for d in service.documents] == [
            ("? Паспорт — оригинал", "Строка 1\nСтрока 2 — пояснение", True),
            ("Согласие", "При наличии", False),
        ]
        response = await owner_http.get(f"/admin/services/{service.id}")
        assert "textarea name=\"documents\"" not in response.text
        assert '<details class="admin-advanced">' in response.text
        assert "/static/admin.css" in response.text
        response = await owner_http.post("/admin/services", data={
            "service_id": str(service.id), "title": service.title, "slug": service.slug,
            "documents": "Паспорт — все страницы\n? Согласие",
        })
        assert response.status_code == 303
        await session.refresh(service, ["documents"])
        assert [(d.title, d.description, d.is_required) for d in service.documents] == [
            ("Паспорт", "все страницы", True), ("Согласие", "", False),
        ]

    async def test_empty_checklist_and_new_service_without_technical_code(self, owner_http, session, tenant):
        response = await owner_http.post("/admin/services", data={
            "title": "Новая услуга", "documents_editor": "rows", "is_active": "1",
        })
        assert response.status_code == 303
        created = await session.scalar(select(Service).where(Service.tenant_id == tenant.id, Service.title == "Новая услуга"))
        assert created.slug
        assert not list(await session.scalars(select(ServiceDocument).where(ServiceDocument.service_id == created.id)))

    async def test_document_preview_rejects_other_tenants_service(self, owner_http, service, other_tenant, session):
        foreign = Service(tenant_id=other_tenant.id, title="Чужая услуга", slug="foreign")
        session.add(foreign)
        await session.commit()
        data = service_data(service)
        data["service_id"] = str(foreign.id)
        for path in ("/admin/services/documents", "/admin/services"):
            response = await owner_http.post(path, data={**data, "document_action": "add"})
            assert response.status_code == 404

    async def test_calendar_filters_week_cancellations_and_tenant_at_local_midnight(self, owner_http, session, tenant, other_tenant, client):
        tenant.timezone = "Asia/Yekaterinburg"
        fixtures = [
            (tenant.id, tenant.id, "2026-09-13T19:30:00+00:00", False, "Начало недели"),
            (tenant.id, tenant.id, "2026-09-20T18:00:00+00:00", False, "Конец недели"),
            (tenant.id, tenant.id, "2026-09-13T18:00:00+00:00", False, "До недели"),
            (tenant.id, tenant.id, "2026-09-20T19:00:00+00:00", False, "После недели"),
            (tenant.id, tenant.id, "2026-09-15T07:00:00+00:00", True, "Отменённая"),
            (other_tenant.id, other_tenant.id, "2026-09-15T08:00:00+00:00", False, "Чужая запись"),
            (tenant.id, other_tenant.id, "2026-09-15T09:00:00+00:00", False, "Чужая заявка"),
        ]
        requests = []
        for index, (appointment_tenant, request_tenant, starts, cancelled, title) in enumerate(fixtures):
            request = Request(tenant_id=request_tenant, client_id=client.id, public_number=index + 1,
                              service_title=title, submission_mode=SubmissionMode.VISIT, channel=Channel.WIDGET)
            session.add(request)
            await session.flush()
            start = datetime.fromisoformat(starts)
            session.add(Appointment(tenant_id=appointment_tenant, request_id=request.id,
                                    starts_at=start, ends_at=start + timedelta(minutes=30), is_cancelled=cancelled))
            requests.append(request)
        await session.commit()
        response = await owner_http.get("/admin/calendar?week=2026-09-17")
        assert response.status_code == 200
        assert "Asia/Yekaterinburg" in response.text
        assert "00:30" in response.text and "01:00" in response.text
        assert "23:00" in response.text and "23:30" in response.text
        assert "/admin/calendar?week=2026-09-07" in response.text
        assert "/admin/calendar?week=2026-09-21" in response.text
        for request in requests[:2]:
            assert request.service_title in response.text
            assert f"/staff/requests/{request.id}" in response.text
        for request in requests[2:]:
            assert request.service_title not in response.text
            assert str(request.id) not in response.text
        assert response.text.index("Начало недели") < response.text.index("Конец недели")
        empty = await owner_http.get("/admin/calendar?week=2026-10-01")
        assert empty.status_code == 200
        assert "Нет записей" in empty.text

    @pytest.mark.parametrize("week", ["not-a-date", "0001-01-01", "9999-12-31"])
    async def test_calendar_invalid_week_is_a_client_error(self, owner_http, week):
        response = await owner_http.get("/admin/calendar", params={"week": week})
        assert response.status_code == 400
