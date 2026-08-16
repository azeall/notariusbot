"""Подключение нотариуса владельцем сервиса и приглашение."""

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.security import hash_password
from app.domain.slugs import suggest_slug
from app.models import PlatformAdmin, Service, Staff, StaffRole, Tenant, WorkingHours
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
async def platform_admin(session) -> PlatformAdmin:
    admin = PlatformAdmin(
        email="vendor@example.ru",
        password_hash=hash_password("vendor12345"),
        full_name="Владелец сервиса",
    )
    session.add(admin)
    await session.commit()
    return admin


@pytest.fixture
async def as_vendor(http, platform_admin):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    return http


NEW_TENANT = {
    "display_name": "Нотариус Кузнецова Мария Андреевна",
    "slug": "kuznecova",
    "city": "Самара",
    "address": "ул. Ленина, 5",
    "phone": "+7 846 000-00-00",
    "timezone": "Europe/Samara",
    "allowed_origins": "https://notarius-kuznecova.ru\nhttps://www.notarius-kuznecova.ru",
}


# --- доступ -----------------------------------------------------------------


async def test_platform_requires_login(http):
    assert (await http.get("/platform")).status_code == 401
    assert (await http.get("/platform/new")).status_code == 401


async def test_login_and_logout(http, platform_admin):
    bad = await http.post(
        "/platform/login", data={"email": platform_admin.email, "password": "wrong"}
    )
    assert bad.status_code == 401

    good = await http.post(
        "/platform/login", data={"email": platform_admin.email, "password": "vendor12345"}
    )
    assert good.status_code == 303
    assert (await http.get("/platform")).status_code == 200

    await http.post("/platform/logout")
    assert (await http.get("/platform")).status_code == 401


async def test_notary_staff_cannot_open_platform(http, tenant, owner):
    """Владелец кабинета нотариуса — не владелец сервиса."""
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    assert (await http.get("/platform")).status_code == 401


# --- подключение нотариуса --------------------------------------------------


async def test_vendor_creates_tenant_with_catalog(as_vendor, session):
    response = await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    assert response.status_code == 303
    assert "invited=kuznecova" in response.headers["location"]
    assert "token=" in response.headers["location"]

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))
    assert tenant is not None
    assert tenant.city == "Самара"
    assert tenant.timezone == "Europe/Samara"
    assert tenant.allowed_origins == [
        "https://notarius-kuznecova.ru",
        "https://www.notarius-kuznecova.ru",
    ]
    assert not tenant.is_activated, "вход ещё не создан"

    services = await session.scalar(
        select(func.count(Service.id)).where(Service.tenant_id == tenant.id)
    )
    hours = await session.scalar(
        select(func.count(WorkingHours.id)).where(WorkingHours.tenant_id == tenant.id)
    )
    assert services > 0
    assert hours == 7

    staff = await session.scalar(
        select(func.count(Staff.id)).where(Staff.tenant_id == tenant.id)
    )
    assert staff == 0, "сотрудников заводит сам нотариус по ссылке"


async def test_invite_link_shown_once(as_vendor, session):
    response = await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    token = response.headers["location"].split("token=")[1]

    page = await as_vendor.get(f"/platform?invited=kuznecova&token={token}")
    assert f"/invite/{token}" in page.text

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))
    assert token not in (tenant.invite_token_hash or ""), "в базе только отпечаток"


async def test_duplicate_slug_rejected(as_vendor):
    await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    again = await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    assert again.status_code == 400
    assert "уже занят" in again.text


async def test_bad_slug_rejected(as_vendor):
    response = await as_vendor.post("/platform/tenants", data={**NEW_TENANT, "slug": "КОД"})
    assert response.status_code == 400


async def test_vendor_edits_allowed_origins(as_vendor, session, tenant):
    response = await as_vendor.post(
        "/platform/tenants",
        data={
            "tenant_id": str(tenant.id),
            "display_name": tenant.display_name,
            "slug": tenant.slug,
            "allowed_origins": "https://example.ru",
            "is_active": "1",
        },
    )
    assert response.status_code == 303
    await session.refresh(tenant)
    assert tenant.allowed_origins == ["https://example.ru"]


async def test_widget_embedding_limited_to_listed_sites(as_vendor, http, session, tenant):
    await as_vendor.post(
        "/platform/tenants",
        data={
            "tenant_id": str(tenant.id),
            "display_name": tenant.display_name,
            "slug": tenant.slug,
            "allowed_origins": "https://example.ru",
            "is_active": "1",
        },
    )
    page = await http.get(f"/widget/{tenant.slug}")
    assert "https://example.ru" in page.headers["content-security-policy"]


async def test_empty_origins_allow_any_site(as_vendor, http, tenant):
    await as_vendor.post(
        "/platform/tenants",
        data={
            "tenant_id": str(tenant.id),
            "display_name": tenant.display_name,
            "slug": tenant.slug,
            "allowed_origins": "",
            "is_active": "1",
        },
    )
    page = await http.get(f"/widget/{tenant.slug}")
    assert "content-security-policy" not in page.headers


# --- приглашение ------------------------------------------------------------


async def _invite_token(as_vendor) -> str:
    response = await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    return response.headers["location"].split("token=")[1]


async def test_notary_accepts_invite(as_vendor, http, session):
    token = await _invite_token(as_vendor)

    page = await http.get(f"/invite/{token}")
    assert page.status_code == 200
    assert "Кузнецова" in page.text

    accepted = await http.post(
        f"/invite/{token}",
        data={
            "full_name": "Кузнецова Мария",
            "email": "Maria@Example.RU",
            "password": "parol12345",
        },
    )
    assert accepted.status_code == 303
    assert accepted.headers["location"] == "/admin?welcome=1"

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))
    await session.refresh(tenant)
    assert tenant.is_activated
    assert tenant.invite_token_hash is None

    owner = await session.scalar(select(Staff).where(Staff.tenant_id == tenant.id))
    assert owner.role is StaffRole.OWNER
    assert owner.email == "maria@example.ru"


async def test_invite_is_single_use(as_vendor, http):
    token = await _invite_token(as_vendor)
    data = {"full_name": "Кузнецова", "email": "m@example.ru", "password": "parol12345"}

    assert (await http.post(f"/invite/{token}", data=data)).status_code == 303
    again = await http.post(f"/invite/{token}", data={**data, "email": "m2@example.ru"})
    assert again.status_code == 410


async def test_expired_invite_page(http):
    response = await http.get("/invite/несуществующий-токен")
    assert response.status_code == 410
    assert "недействительна" in response.text


async def test_short_password_rejected_on_invite(as_vendor, http, session):
    token = await _invite_token(as_vendor)
    response = await http.post(
        f"/invite/{token}",
        data={"full_name": "Кузнецова", "email": "m@example.ru", "password": "123"},
    )
    assert response.status_code == 400
    assert "8 символов" in response.text

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))
    assert not tenant.is_activated


async def test_notary_lands_in_own_cabinet(as_vendor, http, session):
    token = await _invite_token(as_vendor)
    await http.post(
        f"/invite/{token}",
        data={"full_name": "Кузнецова", "email": "m@example.ru", "password": "parol12345"},
    )
    # Кука уже стоит — отдельный вход не нужен.
    cabinet = await http.get("/admin")
    assert cabinet.status_code == 200
    assert "Кузнецова" in cabinet.text


async def test_reissue_invalidates_previous_link(as_vendor, http, session):
    old = await _invite_token(as_vendor)
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))

    response = await as_vendor.post(f"/platform/tenants/{tenant.id}/invite")
    new = response.headers["location"].split("token=")[1]

    assert new != old
    assert (await http.get(f"/invite/{old}")).status_code == 410
    assert (await http.get(f"/invite/{new}")).status_code == 200


async def test_vendor_deletes_mistyped_tenant(as_vendor, session):
    """Опечатались при заведении — карточку надо уметь убрать."""
    await as_vendor.post("/platform/tenants", data=NEW_TENANT)
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova"))

    response = await as_vendor.post(f"/platform/tenants/{tenant.id}/delete")
    assert response.status_code == 303
    assert await session.scalar(select(Tenant).where(Tenant.slug == "kuznecova")) is None


async def test_tenant_with_requests_is_not_deletable(as_vendor, session, tenant, service, client):
    """Заявки клиентов стирать нельзя — нотариуса можно только отключить."""
    from app.domain.requests import create_request
    from app.models import Channel

    await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    response = await as_vendor.post(f"/platform/tenants/{tenant.id}/delete")
    assert response.status_code == 400
    assert "нельзя стирать" in response.json()["detail"]
    assert await session.get(Tenant, tenant.id) is not None


async def test_cyrillic_survives_the_form(as_vendor, session):
    """Кириллица в названии должна дойти до базы без искажений."""
    await as_vendor.post(
        "/platform/tenants",
        data={**NEW_TENANT, "slug": "schukina", "display_name": "Нотариус Щукина Ёлка"},
    )
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "schukina"))
    assert tenant.display_name == "Нотариус Щукина Ёлка"
    assert tenant.city == "Самара"


async def test_public_signup_is_gone(http):
    """Регистрацию с улицы убрали: нотариусов подключает владелец сервиса."""
    assert (await http.get("/signup")).status_code == 404


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Нотариус Иванов Иван Иванович", "ivanov-ivan"),
        ("Щукин", "schukin"),
        ("!!!", "notary"),
    ],
)
def test_suggest_slug(name, expected):
    assert suggest_slug(name) == expected
