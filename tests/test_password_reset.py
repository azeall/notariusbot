"""Сброс пароля по одноразовой ссылке.

Ссылка даёт полный доступ к кабинету, где лежат паспорта клиентов. Значит,
проверять надо не «работает ли», а границы: что она одноразовая, что живёт
недолго, что её нельзя получить чужому и нельзя применить дважды.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain import password_reset
from app.domain.security import hash_password, verify_password
from app.models import AuditLog, PlatformAdmin, StaffRole
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
        full_name="Владелец сервиса",
        password_hash=hash_password("vendor12345"),
    )
    session.add(admin)
    await session.commit()
    return admin


# --- сама ссылка -------------------------------------------------------------


async def test_link_finds_its_owner(session, employee):
    token = await password_reset.issue(session, employee)
    await session.commit()
    assert (await password_reset.resolve(session, token)) is employee


async def test_only_the_hash_is_stored(session, employee):
    """Открытый вид существует один раз — на экране у выдавшего."""
    token = await password_reset.issue(session, employee)
    await session.commit()
    assert employee.reset_token_hash
    assert token not in employee.reset_token_hash


async def test_expired_link_is_refused(session, employee):
    await password_reset.issue(session, employee)
    employee.reset_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    assert await password_reset.resolve(session, employee.reset_token_hash) is None


async def test_new_link_kills_the_previous(session, employee):
    first = await password_reset.issue(session, employee)
    await session.commit()
    await password_reset.issue(session, employee)
    await session.commit()
    assert await password_reset.resolve(session, first) is None


async def test_disabled_person_cannot_reset(session, employee):
    token = await password_reset.issue(session, employee)
    employee.is_active = False
    await session.commit()
    assert await password_reset.resolve(session, token) is None


async def test_nonsense_token_is_refused(session):
    assert await password_reset.resolve(session, "не-токен") is None
    assert await password_reset.resolve(session, "") is None


# --- нотариус выдаёт ссылку сотруднику ---------------------------------------


async def _login(http, tenant, person, password="secret123"):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": person.email, "password": password}
    )


async def test_owner_issues_link_for_employee(http, session, tenant, owner, employee):
    await _login(http, tenant, owner)
    response = await http.post(f"/admin/staff/{employee.id}/reset-link")
    assert response.status_code == 303
    assert "reset=" in response.headers["location"]

    await session.refresh(employee)
    assert employee.reset_token_hash


async def test_issuing_is_written_to_journal(http, session, tenant, owner, employee):
    await _login(http, tenant, owner)
    await http.post(f"/admin/staff/{employee.id}/reset-link")

    entry = await session.scalar(
        select(AuditLog).where(AuditLog.action == "password_reset_issued")
    )
    assert entry is not None
    assert entry.actor_label == owner.full_name


async def test_employee_cannot_issue_links(http, tenant, employee, second_employee):
    """Иначе помощник выдаёт себе доступ к делам, которые ему не поручали."""
    await _login(http, tenant, employee)
    response = await http.post(f"/admin/staff/{second_employee.id}/reset-link")
    assert response.status_code == 403


async def test_owner_cannot_reach_another_tenant(
    http, session, tenant, other_tenant, owner, employee
):
    await _login(http, tenant, owner)
    employee.tenant_id = other_tenant.id
    await session.commit()
    assert (await http.post(f"/admin/staff/{employee.id}/reset-link")).status_code == 404


# --- смена пароля по ссылке --------------------------------------------------


async def test_password_changes_and_logs_in(http, session, employee):
    token = await password_reset.issue(session, employee)
    await session.commit()

    response = await http.post(
        f"/staff/reset/{token}",
        data={"new_password": "novyparol123", "repeat_password": "novyparol123"},
    )
    assert response.status_code == 303

    await session.refresh(employee)
    assert verify_password("novyparol123", employee.password_hash)
    assert employee.reset_token_hash is None, "ссылка обязана погаснуть"


async def test_link_works_only_once(http, session, employee):
    token = await password_reset.issue(session, employee)
    await session.commit()
    data = {"new_password": "novyparol123", "repeat_password": "novyparol123"}

    assert (await http.post(f"/staff/reset/{token}", data=data)).status_code == 303
    assert (await http.post(f"/staff/reset/{token}", data=data)).status_code == 410


async def test_short_password_is_refused(http, session, employee):
    token = await password_reset.issue(session, employee)
    await session.commit()
    response = await http.post(
        f"/staff/reset/{token}", data={"new_password": "123", "repeat_password": "123"}
    )
    assert response.status_code == 400
    await session.refresh(employee)
    assert employee.reset_token_hash, "неудачная попытка не должна гасить ссылку"


async def test_mismatched_passwords_are_refused(http, session, employee):
    token = await password_reset.issue(session, employee)
    await session.commit()
    response = await http.post(
        f"/staff/reset/{token}",
        data={"new_password": "novyparol123", "repeat_password": "drugoyparol"},
    )
    assert response.status_code == 400


async def test_expired_link_shows_explanation(http, session, employee):
    token = await password_reset.issue(session, employee)
    employee.reset_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    page = await http.get(f"/staff/reset/{token}")
    assert page.status_code == 410
    assert "не работает" in page.text.lower()


# --- владелец сервиса выдаёт ссылку нотариусу --------------------------------


async def test_vendor_issues_link_for_notary(http, session, platform_admin, tenant, owner):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    response = await http.post(f"/platform/tenants/{tenant.id}/reset-link")
    assert response.status_code == 303

    await session.refresh(owner)
    assert owner.reset_token_hash
    assert owner.role is StaffRole.OWNER


async def test_vendor_link_needs_an_existing_login(
    http, session, platform_admin, other_tenant
):
    """У конторы, которая ещё не приняла приглашение, сбрасывать нечего."""
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    response = await http.post(f"/platform/tenants/{other_tenant.id}/reset-link")
    assert response.status_code == 404


async def test_stranger_cannot_issue_vendor_link(http, tenant):
    response = await http.post(
        f"/platform/tenants/{tenant.id}/reset-link", headers={"accept": "application/json"}
    )
    assert response.status_code == 401
