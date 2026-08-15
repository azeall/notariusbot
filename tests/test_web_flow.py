"""Сквозной путь клиента и сотрудника — через настоящие HTTP-запросы к приложению."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Attachment, Request
from app.web.deps import db_session
from app.web.main import app


@pytest.fixture
async def http(engine, session):
    """HTTP-клиент поверх приложения.

    Зависимость сессии подменяем на тестовый движок: иначе приложение заведёт
    собственный, привязанный к другому event loop, и соединения развалятся.
    """
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


async def test_services_endpoint_lists_catalog(http, tenant, service, visit_service):
    response = await http.get(f"/api/v1/{tenant.slug}/services")
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert service.title in titles
    assert visit_service.title in titles


async def test_services_include_checklist(http, tenant, service):
    response = await http.get(f"/api/v1/{tenant.slug}/services")
    payload = next(item for item in response.json() if item["id"] == str(service.id))
    assert [doc["title"] for doc in payload["documents"]] == [
        "Паспорт доверителя",
        "Свидетельство о регистрации ТС",
        "Паспортные данные представителя",
    ]


async def test_search_endpoint(http, tenant, service):
    response = await http.get(f"/api/v1/{tenant.slug}/services/search", params={"q": "машина"})
    assert response.status_code == 200
    assert response.json()[0]["id"] == str(service.id)


async def test_unknown_tenant_is_404(http, service):
    assert (await http.get("/api/v1/nonexistent/services")).status_code == 404


async def test_submit_request_returns_upload_link(http, session, tenant, service):
    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+7 999 000-00-01",
            "comment": "срочно",
            "consent": True,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["public_number"] == 1
    assert body["upload_url"].startswith("http://test/upload/")
    assert len(body["checklist"]) == 3


async def test_request_without_consent_is_rejected(http, tenant, service):
    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": False,
        },
    )
    assert response.status_code == 422


async def test_honeypot_blocks_bot(http, tenant, service):
    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Bot Botovich",
            "phone": "+79990000009",
            "consent": True,
            "website": "http://spam.example",
        },
    )
    assert response.status_code == 400


async def test_short_phone_rejected(http, tenant, service):
    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "123",
            "consent": True,
        },
    )
    assert response.status_code == 422


async def test_visit_service_requires_slot(http, tenant, visit_service):
    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(visit_service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )
    assert response.status_code == 400
    assert "время визита" in response.json()["detail"]


async def test_visit_service_books_slot(http, tenant, visit_service):
    slots = (await http.get(f"/api/v1/{tenant.slug}/services/{visit_service.id}/slots")).json()
    assert slots, "должны быть свободные окна"

    response = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(visit_service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
            "slot": slots[0]["starts_at"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["appointment_at"] is not None
    assert response.json()["upload_url"] is None

    # Занятое окно больше не предлагается.
    again = (await http.get(f"/api/v1/{tenant.slug}/services/{visit_service.id}/slots")).json()
    assert slots[0]["starts_at"] not in [s["starts_at"] for s in again]


async def test_rate_limit_kicks_in(http, tenant, service):
    payload = {
        "service_id": str(service.id),
        "full_name": "Спамов Спам",
        "phone": "+79990000005",
        "consent": True,
    }
    codes = []
    for _ in range(7):
        codes.append((await http.post(f"/api/v1/{tenant.slug}/requests", json=payload)).status_code)
    assert 429 in codes
    assert codes.count(201) == 5


async def test_upload_flow_end_to_end(http, session, tenant, service, employee):
    created = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )
    token = created.json()["upload_url"].rsplit("/", 1)[-1]

    page = await http.get(f"/upload/{token}")
    assert page.status_code == 200
    assert "Паспорт доверителя" in page.text

    payload = b"%PDF-1.4 scan of passport"
    upload = await http.post(
        f"/upload/{token}",
        files=[("files", ("passport.pdf", payload, "application/pdf"))],
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["saved"] == ["passport.pdf"]

    # Ссылка одноразовая.
    second = await http.post(
        f"/upload/{token}", files=[("files", ("again.pdf", payload, "application/pdf"))]
    )
    assert second.status_code == 410

    attachment = await session.scalar(select(Attachment))
    assert attachment is not None
    assert attachment.original_filename == "passport.pdf"


async def test_upload_rejects_unsupported_type(http, tenant, service):
    created = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )
    token = created.json()["upload_url"].rsplit("/", 1)[-1]
    response = await http.post(
        f"/upload/{token}",
        files=[("files", ("script.exe", b"MZ", "application/x-msdownload"))],
    )
    assert response.status_code == 415


async def test_expired_link_page(http, tenant):
    response = await http.get("/upload/definitely-not-a-real-token")
    assert response.status_code == 410
    assert "недействительна" in response.text


async def test_staff_login_and_claim(http, session, tenant, service, employee):
    await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )

    # Без входа очередь недоступна.
    assert (await http.get("/staff")).status_code == 401

    login = await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )
    assert login.status_code == 303

    queue = await http.get("/staff")
    assert queue.status_code == 200
    assert "Смирнов Алексей" in queue.text

    request = await session.scalar(select(Request))
    claim = await http.post(f"/staff/requests/{request.id}/claim")
    assert claim.status_code == 303

    detail = await http.get(f"/staff/requests/{request.id}")
    assert detail.status_code == 200
    assert "В работе" in detail.text


async def test_wrong_password_denied(http, tenant, employee):
    response = await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "Неверная почта или пароль" in response.text


async def test_employee_cannot_open_admin(http, tenant, employee):
    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )
    assert (await http.get("/admin")).status_code == 403


async def test_owner_can_edit_catalog(http, session, tenant, owner, service):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    assert (await http.get("/admin")).status_code == 200

    response = await http.post(
        "/admin/services",
        data={
            "service_id": str(service.id),
            "title": service.title,
            "slug": service.slug,
            "submission_mode": "documents",
            "visit_duration_minutes": "30",
            "keywords": "машина, авто",
            "sort_order": "1",
            "is_active": "1",
            "documents": "Паспорт — все страницы\n? Согласие супруга",
        },
    )
    assert response.status_code == 303

    listing = (await http.get(f"/api/v1/{tenant.slug}/services")).json()
    documents = next(s for s in listing if s["id"] == str(service.id))["documents"]
    assert [d["title"] for d in documents] == ["Паспорт", "Согласие супруга"]
    assert documents[0]["description"] == "все страницы"
    assert documents[1]["is_required"] is False


async def test_staff_downloads_decrypted_document(http, session, tenant, service, employee):
    created = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )
    token = created.json()["upload_url"].rsplit("/", 1)[-1]
    payload = b"%PDF-1.4 secret content"
    await http.post(
        f"/upload/{token}", files=[("files", ("doc.pdf", payload, "application/pdf"))]
    )

    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )

    request = await session.scalar(select(Request))
    attachment = await session.scalar(select(Attachment))
    response = await http.get(f"/staff/requests/{request.id}/documents/{attachment.id}")

    assert response.status_code == 200
    assert response.content == payload


async def test_staff_cannot_reach_other_tenant_request(
    http, session, tenant, other_tenant, service, employee
):
    created = await http.post(
        f"/api/v1/{tenant.slug}/requests",
        json={
            "service_id": str(service.id),
            "full_name": "Смирнов Алексей",
            "phone": "+79990000001",
            "consent": True,
        },
    )
    assert created.status_code == 201

    employee.tenant_id = other_tenant.id
    await session.commit()

    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )
    request = await session.scalar(select(Request))
    assert (await http.get(f"/staff/requests/{request.id}")).status_code in (401, 404)


async def test_embed_script_is_served(http, tenant):
    response = await http.get("/embed.js")
    assert response.status_code == 200
    assert "data-notary" in response.text
    assert "nb-launcher" in response.text


async def test_widget_page_renders(http, tenant, service):
    response = await http.get(f"/widget/{tenant.slug}")
    assert response.status_code == 200
    assert tenant.display_name in response.text


async def test_healthz(http):
    assert (await http.get("/healthz")).json() == {"status": "ok"}
