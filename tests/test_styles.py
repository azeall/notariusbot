"""Две стороны продукта выглядят по-разному, и это намеренно.

Кабинет владельца сервиса — в стиле его сайта, чёрно-белый. Всё, что видит
нотариус и его сотрудники, — в сине-золотой палитре нотариальной практики.
Тесты держат это разделение: перепутать стили легко, заметить трудно.
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.security import hash_password
from app.models import PlatformAdmin
from app.web.deps import db_session
from app.web.main import app

PLATFORM = "/static/platform.css"
NOTARY = "/static/notary.css"


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
async def as_vendor(http, session):
    admin = PlatformAdmin(
        email="vendor@example.ru",
        password_hash=hash_password("vendor12345"),
        full_name="Владелец",
    )
    session.add(admin)
    await session.commit()
    await http.post(
        "/platform/login", data={"email": admin.email, "password": "vendor12345"}
    )
    return http


# --- сторона владельца сервиса: чёрно-белая ---------------------------------


async def test_platform_login_uses_platform_style(http):
    page = await http.get("/platform/login")
    assert PLATFORM in page.text
    assert NOTARY not in page.text


async def test_platform_pages_use_platform_style(as_vendor):
    for path in ("/platform", "/platform/new", "/platform/password"):
        page = await as_vendor.get(path)
        assert PLATFORM in page.text, path
        assert NOTARY not in page.text, path


async def test_invite_page_uses_platform_style(as_vendor, http):
    created = await as_vendor.post(
        "/platform/tenants",
        data={"display_name": "Нотариус Новиков", "slug": "novikov"},
    )
    token = created.headers["location"].split("token=")[1]
    page = await http.get(f"/invite/{token}")
    assert PLATFORM in page.text


# --- сторона нотариуса: сине-золотая ----------------------------------------


async def test_staff_login_uses_notary_style(http, tenant):
    page = await http.get(f"/staff/{tenant.slug}/login")
    assert NOTARY in page.text
    assert PLATFORM not in page.text


async def test_staff_pages_use_notary_style(http, tenant, owner):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    for path in ("/staff", "/staff/password", "/admin", "/admin/schedule", "/admin/audit"):
        page = await http.get(path)
        assert NOTARY in page.text, path
        assert PLATFORM not in page.text, path


# --- сами файлы ---------------------------------------------------------------


async def test_stylesheets_are_served(http):
    for path in (PLATFORM, NOTARY):
        response = await http.get(path)
        assert response.status_code == 200, path


async def test_palettes_actually_differ(http):
    platform = (await http.get(PLATFORM)).text
    notary = (await http.get(NOTARY)).text

    assert "--paper: #ffffff" in platform, "кабинет владельца — на белом"
    assert "--navy: #0a1628" in notary, "панель нотариуса — на тёмно-синем"
    assert "--gold" in notary
    assert "--gold" not in platform
