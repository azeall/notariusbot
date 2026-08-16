"""Самостоятельная регистрация нотариуса."""

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Service, ServiceDocument, Staff, StaffRole, Tenant, WorkingHours
from app.web.deps import db_session
from app.web.main import app
from app.web.signup import suggest_slug


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


VALID = {
    "display_name": "Нотариус Петров Пётр Петрович",
    "city": "Казань",
    "phone": "+7 843 000-00-00",
    "slug": "petrov",
    "full_name": "Петров Пётр Петрович",
    "email": "Petrov@Example.RU",
    "password": "very-secret-1",
}


async def test_signup_page_opens(http):
    response = await http.get("/signup")
    assert response.status_code == 200
    assert "Создать кабинет" in response.text


async def test_signup_creates_tenant_owner_and_catalog(http, session):
    response = await http.post("/signup", data=VALID)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin?welcome=1"

    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "petrov"))
    assert tenant is not None
    assert tenant.display_name == VALID["display_name"]
    assert tenant.city == "Казань"

    owner = await session.scalar(select(Staff).where(Staff.tenant_id == tenant.id))
    assert owner.role is StaffRole.OWNER
    assert owner.email == "petrov@example.ru", "почта должна нормализоваться"

    services = await session.scalar(
        select(func.count(Service.id)).where(Service.tenant_id == tenant.id)
    )
    documents = await session.scalar(
        select(func.count(ServiceDocument.id)).where(ServiceDocument.tenant_id == tenant.id)
    )
    hours = await session.scalar(
        select(func.count(WorkingHours.id)).where(WorkingHours.tenant_id == tenant.id)
    )
    assert services > 0, "пустой каталог — плохая первая встреча"
    assert documents > 0
    assert hours == 7


async def test_signup_logs_owner_in(http, session):
    await http.post("/signup", data=VALID)
    # Кука уже стоит на клиенте — кабинет открывается без отдельного входа.
    assert (await http.get("/admin")).status_code == 200


async def test_welcome_shows_embed_snippet(http):
    await http.post("/signup", data=VALID)
    page = await http.get("/admin?welcome=1")
    assert "Кабинет создан" in page.text
    assert 'data-notary="petrov"' in page.text


async def test_duplicate_slug_rejected(http):
    await http.post("/signup", data=VALID)
    again = await http.post("/signup", data={**VALID, "email": "other@example.ru"})
    assert again.status_code == 400
    assert "уже занят" in again.text


async def test_short_password_rejected(http, session):
    response = await http.post("/signup", data={**VALID, "password": "1234"})
    assert response.status_code == 400
    assert "8 символов" in response.text
    assert await session.scalar(select(Tenant).where(Tenant.slug == "petrov")) is None


async def test_bad_slug_rejected(http):
    response = await http.post("/signup", data={**VALID, "slug": "Нотариус Петров"})
    assert response.status_code == 400
    assert "латинских букв" in response.text


async def test_bad_email_rejected(http):
    response = await http.post("/signup", data={**VALID, "email": "нетсобаки"})
    assert response.status_code == 400


async def test_form_values_survive_error(http):
    response = await http.post("/signup", data={**VALID, "password": "123"})
    assert VALID["display_name"] in response.text
    assert "Казань" in response.text


async def test_slug_is_generated_when_empty(http, session):
    response = await http.post("/signup", data={**VALID, "slug": ""})
    assert response.status_code == 303
    tenant = await session.scalar(select(Tenant))
    assert tenant.slug == "petrov-petr"


async def test_new_tenant_is_isolated(http, session, tenant, service):
    """Свежий нотариус не видит чужие услуги и не мешает существующему."""
    await http.post("/signup", data=VALID)
    fresh = await session.scalar(select(Tenant).where(Tenant.slug == "petrov"))

    # Названия у стартового набора и у фикстуры совпадают, поэтому сверяем
    # по идентификаторам: важно, что это разные записи разных нотариусов.
    listing = (await http.get(f"/api/v1/{fresh.slug}/services")).json()
    assert str(service.id) not in [item["id"] for item in listing]

    old_listing = (await http.get(f"/api/v1/{tenant.slug}/services")).json()
    assert str(service.id) in [item["id"] for item in old_listing]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Нотариус Иванов Иван Иванович", "ivanov-ivan"),
        ("Нотариальная контора Смирновой", "notarialnaya-kontora"),
        ("Щукин", "schukin"),
        ("!!!", "notary"),
    ],
)
def test_suggest_slug(name, expected):
    assert suggest_slug(name) == expected
