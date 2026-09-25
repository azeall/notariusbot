"""Public preparation sheets; template tests do not require a database."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound
from sqlalchemy import select
from starlette.requests import Request

from app.models import ServiceDocument
from app.web.deps import db_session


WEB_DIR = Path(__file__).resolve().parents[1] / "app" / "web"


def render_sheet(**changes):
    templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
    try:
        templates.get_template("preparation.html")
    except TemplateNotFound:
        pytest.fail("The public preparation sheet template is not implemented")
    tenant = SimpleNamespace(slug="ivanov", display_name="Нотариус Иванов",
                             city="Москва", address="Улица, 1", phone="+79995550123")
    context = dict(tenant=tenant, service=SimpleNamespace(title="Доверенность", description=""),
                   documents=[], palette_css="", title="Подготовка")
    context.update(changes)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    return templates.TemplateResponse(request, "preparation.html", context).body.decode()


def test_template_escapes_catalog_and_contacts():
    unsafe = '<script>alert("private")</script>'
    page = render_sheet(
        tenant=SimpleNamespace(slug="ivanov", display_name=unsafe, city=unsafe,
                               address=unsafe, phone=unsafe),
        service=SimpleNamespace(title=unsafe, description=unsafe),
        documents=[SimpleNamespace(title=unsafe, description=unsafe, is_required=True)],
    )
    assert unsafe not in page
    assert page.count("&lt;script&gt;") >= 8
    assert 'type="checkbox"' in page


def test_template_empty_catalog_does_not_invent_documents():
    page = render_sheet()
    assert "Перечень документов пока не указан" in page
    assert 'type="checkbox"' not in page
    assert "Окончательный перечень" in page
    assert "Паспорт" not in page


def test_template_demo_warning_and_ephemeral_checklist():
    tenant = SimpleNamespace(slug="demo", display_name="Демо", city="", address="", phone="")
    page = render_sheet(tenant=tenant)
    assert "Демонстрация сервиса" in page
    assert "вымышлен" in page
    assert "Демонстрация сервиса" not in render_sheet()
    assert 'onclick="window.print()"' in page
    assert "localStorage" not in page
    assert "fetch(" not in page
    assert "<form" not in page


@pytest.fixture
async def preparation_http(session):
    from app.web.preparation import router

    app = FastAPI()
    app.include_router(router)

    async def override_session():
        yield session

    app.dependency_overrides[db_session] = override_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test", trust_env=False) as client:
        yield client


async def test_public_sheet_documents_contacts_order_and_no_client_data(
    preparation_http, session, tenant, service, client,
):
    tenant.address = "Москва, улица Тестовая, 7"
    tenant.phone = "+79995550123"
    tenant.widget_mode = "light"
    service.documents[0].description = "Оригинал для сверки"
    await session.commit()
    response = await preparation_http.get(f"/{tenant.slug}/services/{service.id}/prepare")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    page = response.text
    for value in [service.title, tenant.display_name, tenant.address, tenant.phone,
                  "Оригинал для сверки", "Обязательно", "При необходимости"]:
        assert value in page
    assert page.index("Паспорт доверителя") < page.index("Свидетельство о регистрации ТС")
    assert page.count('type="checkbox"') == 3
    assert "--navy:#ffffff" in page
    assert client.full_name not in page
    assert client.phone not in page


@pytest.mark.parametrize("case", ["other_tenant", "inactive_tenant", "archived_service",
                                  "unknown_tenant", "unknown_uuid", "malformed_uuid"])
async def test_public_sheet_hides_unavailable_services(
    preparation_http, session, tenant, other_tenant, service, case,
):
    slug, service_id = tenant.slug, str(service.id)
    if case == "other_tenant":
        slug = other_tenant.slug
    elif case == "inactive_tenant":
        tenant.is_active = False
    elif case == "archived_service":
        service.is_active = False
    elif case == "unknown_tenant":
        slug = "missing-notary"
    elif case == "unknown_uuid":
        service_id = str(uuid.uuid4())
    else:
        service_id = "not-a-uuid"
    await session.commit()
    response = await preparation_http.get(f"/{slug}/services/{service_id}/prepare")
    assert response.status_code == 404
    assert service.title not in response.text


async def test_public_sheet_never_exposes_document_from_other_tenant(
    preparation_http, session, tenant, other_tenant, service,
):
    session.add(ServiceDocument(tenant_id=other_tenant.id, service_id=service.id,
                                title="Чужой документ", sort_order=0))
    await session.commit()
    response = await preparation_http.get(f"/{tenant.slug}/services/{service.id}/prepare")
    assert response.status_code == 200
    assert "Чужой документ" not in response.text
    assert "Паспорт доверителя" in response.text
    assert await session.scalar(select(ServiceDocument).where(
        ServiceDocument.title == "Чужой документ")) is not None
