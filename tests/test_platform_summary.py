"""Сводка в кабинете владельца.

Главный сигнал здесь — молчащая контора. Ошибка в подсчёте тихая: цифры
всё равно выглядят правдоподобно, а решения по ним принимаются неверные.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.requests import create_request
from app.domain.security import hash_password
from app.models import Channel, PlatformAdmin
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


@pytest.fixture
async def active_tenant(session, tenant):
    """Контора, которая приняла приглашение.

    Пока приглашение не принято, у конторы свой значок «ждёт активации»,
    и сведения о заявках ей неуместны — поэтому тесты про заявки говорят
    об активированной конторе прямо, а не рассчитывают на умолчание.
    """
    tenant.invite_accepted_at = datetime.now(UTC)
    await session.commit()
    return tenant


@pytest.fixture
async def as_vendor(http, platform_admin):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    return http


async def test_empty_service_shows_no_summary(as_vendor, session, active_tenant):
    """Одна заведённая контора без заявок — сводка есть, но без тревог."""
    page = await as_vendor.get("/platform")
    assert page.status_code == 200
    assert "заведено контор" in page.text
    assert "заявок ещё не было" in page.text


async def test_counts_requests(as_vendor, session, tenant, client, service):
    for _ in range(3):
        await create_request(
            session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
        )
    await session.commit()

    page = await as_vendor.get("/platform")
    assert "заявок всего" in page.text
    assert "заявок: <b>3</b>" in page.text


async def test_silent_tenant_is_flagged(as_vendor, session, active_tenant, client, service):
    """Заявки были давно — контора помечается как молчащая."""
    request = await create_request(
        session, tenant=active_tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    request.created_at = datetime.now(UTC) - timedelta(days=10)
    await session.commit()

    page = await as_vendor.get("/platform")
    assert "неделю тихо" in page.text
    assert "неделю без заявок" in page.text


async def test_fresh_request_is_not_silent(as_vendor, session, tenant, client, service):
    await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    page = await as_vendor.get("/platform")
    assert "неделю тихо" not in page.text


async def test_requests_of_one_tenant_do_not_count_for_another(
    as_vendor, session, active_tenant, other_tenant, client, service
):
    """Считать чужие заявки своими — тихая ошибка: цифра выглядит правдоподобно."""
    other_tenant.invite_accepted_at = datetime.now(UTC)
    await create_request(
        session, tenant=active_tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    page = await as_vendor.get("/platform")
    # У второй конторы заявок нет, и она обязана быть помечена как пустая.
    assert "заявок ещё не было" in page.text
