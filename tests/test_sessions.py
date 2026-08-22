"""Сессии кабинета: срок, продление, «запомнить меня».

За этой кукой стоит доступ к паспортам клиентов. Ошибка в одну сторону —
человека выкидывает посреди работы; в другую — украденная кука живёт вечно.
Обе тихие: ни та ни другая ничего не ломает заметно.
"""

import time
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.web import sessions
from app.web.deps import SESSION_COOKIE, db_session
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


# --- сама кука ---------------------------------------------------------------


def test_issue_and_read():
    who = uuid.uuid4()
    value, ttl = sessions.issue(who, remember=False)
    assert ttl == sessions.SHORT_TTL
    found = sessions.read(value)
    assert found is not None
    assert found.subject_id == who


def test_remember_gives_long_life():
    _, short = sessions.issue(uuid.uuid4(), remember=False)
    _, long = sessions.issue(uuid.uuid4(), remember=True)
    assert long > short
    assert long == 30 * 24 * 60 * 60


def test_tampered_cookie_is_rejected():
    value, _ = sessions.issue(uuid.uuid4(), remember=True)
    assert sessions.read(value[:-4] + "aaaa") is None


def test_expired_cookie_is_rejected(monkeypatch):
    """Срок живёт в подписи, а не только в куке.

    Браузер можно попросить хранить куку сколько угодно — и раньше
    украденная работала бы вечно, потому что подпись давность не проверяла.
    """
    value, _ = sessions.issue(uuid.uuid4(), remember=False)
    assert sessions.read(value) is not None

    later = time.time() + sessions.SHORT_TTL + 60
    monkeypatch.setattr(time, "time", lambda: later)
    assert sessions.read(value) is None


def test_staff_cookie_does_not_open_owner_cabinet():
    """Соль у кабинетов разная: кука одного не подходит к другому."""
    value, _ = sessions.issue(uuid.uuid4(), remember=True, key="staff_id")
    assert sessions.read(value, key="admin_id") is None


def test_cookie_without_deadline_is_rejected():
    """Куки прежней версии полей срока не имеют — пусть войдут заново."""
    from itsdangerous import URLSafeSerializer

    old = URLSafeSerializer(get_settings().session_secret, salt="staff-session")
    assert sessions.read(old.dumps({"staff_id": str(uuid.uuid4())})) is None


def test_forged_lifetime_is_rejected():
    """Срок берётся из двух известных значений, произвольный не принимается."""
    from itsdangerous import URLSafeSerializer

    forger = URLSafeSerializer(get_settings().session_secret, salt="staff-session")
    raw = forger.dumps(
        {"staff_id": str(uuid.uuid4()), "iat": int(time.time()), "ttl": 100 * 365 * 24 * 3600}
    )
    assert sessions.read(raw) is None


# --- продление ---------------------------------------------------------------


def test_short_session_is_not_renewed():
    """Выбрали «на один день» — значит на один день, решать за человека не наше дело."""
    value, _ = sessions.issue(uuid.uuid4(), remember=False)
    found = sessions.read(value)
    assert not found.needs_renewal(int(time.time()) + sessions.RENEW_AFTER + 10)


def test_long_session_renews_after_a_day():
    value, _ = sessions.issue(uuid.uuid4(), remember=True)
    found = sessions.read(value)
    assert not found.needs_renewal(int(time.time()) + 60)
    assert found.needs_renewal(int(time.time()) + sessions.RENEW_AFTER + 10)


# --- через приложение --------------------------------------------------------


async def test_login_without_remember_is_short(http, tenant, owner):
    r = await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    assert r.status_code == 303
    raw = r.cookies.get(SESSION_COOKIE)
    assert sessions.read(raw).ttl == sessions.SHORT_TTL


async def test_login_with_remember_is_long(http, tenant, owner):
    r = await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": owner.email, "password": "secret123", "remember": "1"},
    )
    assert r.status_code == 303
    raw = r.cookies.get(SESSION_COOKIE)
    assert sessions.read(raw).ttl == sessions.LONG_TTL


async def test_session_survives_a_new_visit(http, tenant, owner):
    """То, ради чего всё затевалось: закрыл браузер, вернулся — всё ещё внутри."""
    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": owner.email, "password": "secret123", "remember": "1"},
    )
    raw = http.cookies.get(SESSION_COOKIE)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as fresh:
        fresh.cookies.set(SESSION_COOKIE, raw)
        page = await fresh.get("/staff")
        assert page.status_code == 200


async def test_logout_really_ends_session(http, tenant, owner):
    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": owner.email, "password": "secret123", "remember": "1"},
    )
    await http.post("/staff/logout")
    assert (await http.get("/staff")).status_code in (401, 403)


async def test_cookie_is_httponly(http, tenant, owner):
    """Скрипту на странице кука видна быть не должна."""
    r = await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    header = r.headers.get("set-cookie", "")
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()
