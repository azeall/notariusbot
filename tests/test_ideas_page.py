"""Страница с идеями в кабинете владельца.

Проверок немного, но они не декоративные: страница показывается только
владельцу сервиса, и её содержимое обязано быть разобранным, а не списком
заголовков — иначе она превращается в свалку и её перестают открывать.
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.security import hash_password
from app.models import PlatformAdmin
from app.web import ideas
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


def test_every_idea_is_analysed():
    """Заголовок без разбора бесполезен: нужен довод и вывод.

    Разбор может лежать в «зачем» либо в «против» — у отклонённых идей
    вся суть как раз во втором, и требовать длинного «зачем» от них
    значит заставлять писать воду.
    """
    assert ideas.GROUPS
    for group in ideas.GROUPS:
        assert group.items, f"пустая группа: {group.title}"
        for idea in group.items:
            assert idea.title
            assert len(idea.what) > 20, idea.title
            assert len(idea.why) + len(idea.against) > 120, idea.title
            assert len(idea.verdict) > 20, idea.title
            assert idea.effort


def test_there_are_ideas_to_reject():
    """Список, где всё стоит делать, — не разбор, а перечень желаний."""
    rejected = [g for g in ideas.GROUPS if "не стал" in g.title.lower()]
    assert rejected, "нет группы с отказами"
    for group in rejected:
        for idea in group.items:
            assert idea.against, f"отказ без довода против: {idea.title}"


async def test_page_requires_login(http):
    response = await http.get("/platform/todo", headers={"accept": "application/json"})
    assert response.status_code == 401


async def test_owner_sees_the_page(http, platform_admin):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    page = await http.get("/platform/todo")
    assert page.status_code == 200
    assert "Что доделать" in page.text
    for group in ideas.GROUPS:
        assert group.title in page.text


async def test_notary_cannot_open_vendor_ideas(http, tenant, owner):
    """Кабинет нотариуса и кабинет владельца сервиса — разные двери."""
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    response = await http.get("/platform/todo", headers={"accept": "application/json"})
    assert response.status_code == 401


# --- направления для нового дела ---------------------------------------------


def test_every_direction_names_its_buyer():
    """Направление без покупателя — не направление, а желание.

    Самая частая ошибка в таких списках: описано что делать и ни слова
    о том, кому это продавать.
    """
    from app.web import directions

    assert directions.BLOCKS
    for block in directions.BLOCKS:
        assert block.items, f"пустой раздел: {block.title}"
        for item in block.items:
            assert item.who, item.title
            assert len(item.why) + len(item.against) > 200, item.title
            assert len(item.verdict) > 20, item.title


def test_directions_include_refusals():
    """Список без отказов — перечень желаний, а не разбор."""
    from app.web import directions

    refused = [
        item
        for block in directions.BLOCKS
        for item in block.items
        if item.badge == "closed"
    ]
    assert refused, "нет ни одного отвергнутого направления"
    for item in refused:
        assert item.against, f"отказ без довода: {item.title}"


async def test_owner_sees_directions(http, platform_admin):
    await http.post(
        "/platform/login",
        data={"email": platform_admin.email, "password": "vendor12345"},
    )
    page = await http.get("/platform/ideas")
    assert page.status_code == 200
    assert "Куда можно уйти" in page.text


async def test_directions_need_login(http):
    response = await http.get("/platform/ideas", headers={"accept": "application/json"})
    assert response.status_code == 401
