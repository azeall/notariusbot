"""Смена пароля владельцем сервиса и сотрудниками нотариуса."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.security import hash_password, verify_password
from app.models import PlatformAdmin
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
        password_hash=hash_password("staroe-parol1"),
        full_name="Владелец сервиса",
    )
    session.add(admin)
    await session.commit()
    return admin


@pytest.fixture
async def as_vendor(http, platform_admin):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "staroe-parol1"},
    )
    return http


@pytest.fixture
async def as_staff(http, tenant, employee):
    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )
    return http


# --- владелец сервиса -------------------------------------------------------


async def test_password_page_requires_login(http):
    assert (await http.get("/platform/password")).status_code == 401


async def test_vendor_changes_password(as_vendor, session, platform_admin):
    response = await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "staroe-parol1",
            "new_password": "novoe-parol-2026",
            "repeat_password": "novoe-parol-2026",
        },
    )
    assert response.status_code == 200
    assert "Пароль изменён" in response.text

    await session.refresh(platform_admin)
    assert verify_password("novoe-parol-2026", platform_admin.password_hash)
    assert not verify_password("staroe-parol1", platform_admin.password_hash)


async def test_new_password_works_for_login(as_vendor, http, platform_admin):
    await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "staroe-parol1",
            "new_password": "novoe-parol-2026",
            "repeat_password": "novoe-parol-2026",
        },
    )
    await http.post("/platform/logout")

    old = await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "staroe-parol1"},
    )
    assert old.status_code == 401, "старый пароль обязан перестать работать"

    new = await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "novoe-parol-2026"},
    )
    assert new.status_code == 303


async def test_wrong_current_password_rejected(as_vendor, session, platform_admin):
    response = await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "не тот",
            "new_password": "novoe-parol-2026",
            "repeat_password": "novoe-parol-2026",
        },
    )
    assert response.status_code == 400
    assert "Текущий пароль неверный" in response.text

    await session.refresh(platform_admin)
    assert verify_password("staroe-parol1", platform_admin.password_hash)


async def test_short_password_rejected(as_vendor):
    response = await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "staroe-parol1",
            "new_password": "1234",
            "repeat_password": "1234",
        },
    )
    assert response.status_code == 400
    assert "8 символов" in response.text


async def test_mismatched_repeat_rejected(as_vendor):
    response = await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "staroe-parol1",
            "new_password": "novoe-parol-2026",
            "repeat_password": "novoe-parol-2027",
        },
    )
    assert response.status_code == 400
    assert "не совпадают" in response.text


async def test_same_password_rejected(as_vendor):
    response = await as_vendor.post(
        "/platform/password",
        data={
            "current_password": "staroe-parol1",
            "new_password": "staroe-parol1",
            "repeat_password": "staroe-parol1",
        },
    )
    assert response.status_code == 400
    assert "совпадает со старым" in response.text


# --- сотрудник нотариуса ----------------------------------------------------


async def test_staff_page_requires_login(http):
    assert (await http.get("/staff/password")).status_code == 401


async def test_staff_changes_password(as_staff, session, employee, tenant):
    response = await as_staff.post(
        "/staff/password",
        data={
            "current_password": "secret123",
            "new_password": "moy-novyy-parol",
            "repeat_password": "moy-novyy-parol",
        },
    )
    assert response.status_code == 200
    assert "Пароль изменён" in response.text

    await session.refresh(employee)
    assert verify_password("moy-novyy-parol", employee.password_hash)

    await as_staff.post("/staff/logout")
    ok = await as_staff.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "moy-novyy-parol"},
    )
    assert ok.status_code == 303


async def test_staff_wrong_current_rejected(as_staff, session, employee):
    response = await as_staff.post(
        "/staff/password",
        data={
            "current_password": "не тот",
            "new_password": "moy-novyy-parol",
            "repeat_password": "moy-novyy-parol",
        },
    )
    assert response.status_code == 400
    await session.refresh(employee)
    assert verify_password("secret123", employee.password_hash)


async def test_staff_change_does_not_touch_colleagues(
    as_staff, session, employee, second_employee
):
    """Смена своего пароля не должна задеть чужой."""
    await as_staff.post(
        "/staff/password",
        data={
            "current_password": "secret123",
            "new_password": "moy-novyy-parol",
            "repeat_password": "moy-novyy-parol",
        },
    )
    await session.refresh(second_employee)
    assert verify_password("secret123", second_employee.password_hash)


async def test_password_link_in_header(as_staff):
    page = await as_staff.get("/staff")
    assert "/staff/password" in page.text
