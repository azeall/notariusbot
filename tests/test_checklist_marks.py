"""Отметки о полученных документах.

Ради этого сервис и покупают: смысл в том, что клиент приходит
подготовленным. Ошибка тут тихая в обе стороны — либо сотрудник считает
комплект полным, когда чего-то нет, либо клиент шлёт второй раз то,
что уже дошло.
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.requests import create_request, issue_upload_token
from app.models import Channel, RequestEvent
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
async def req(session, tenant, client, service):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    assert request.checklist, "услуга без перечня — тест бессмыслен"
    return request


async def _login(http, tenant, person, password="secret123"):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": person.email, "password": password}
    )


# --- сама отметка ------------------------------------------------------------


async def test_staff_marks_document_received(http, session, tenant, owner, req):
    await _login(http, tenant, owner)
    response = await http.post(f"/staff/requests/{req.id}/checklist/0")
    assert response.status_code == 303

    await session.refresh(req)
    assert req.received_documents == [0]


async def test_mark_can_be_removed(http, session, tenant, owner, req):
    await _login(http, tenant, owner)
    url = f"/staff/requests/{req.id}/checklist/0"
    await http.post(url)
    await http.post(url)

    await session.refresh(req)
    assert req.received_documents == []


async def test_marks_are_written_to_history(http, session, tenant, owner, req):
    """По истории должно восстанавливаться, кто и что отметил."""
    await _login(http, tenant, owner)
    await http.post(f"/staff/requests/{req.id}/checklist/0")

    event = await session.scalar(
        select(RequestEvent).where(
            RequestEvent.request_id == req.id,
            RequestEvent.comment.contains("отмечен полученным"),
        )
    )
    assert event is not None
    assert "отмечен полученным" in event.comment
    assert event.actor_label == owner.full_name


async def test_snapshot_is_not_touched(http, session, tenant, owner, req):
    """Слепок перечня обязан остаться тем, что видел клиент.

    Если смешать в нём обещанное и полученное, однажды не выйдет ответить
    на вопрос «а что мы вообще просили».
    """
    before = [dict(d) for d in req.checklist]
    await _login(http, tenant, owner)
    await http.post(f"/staff/requests/{req.id}/checklist/0")

    await session.refresh(req)
    assert req.checklist == before


async def test_unknown_item_is_rejected(http, tenant, owner, req):
    await _login(http, tenant, owner)
    assert (await http.post(f"/staff/requests/{req.id}/checklist/99")).status_code == 404


async def test_colleague_cannot_mark_someone_elses_request(
    http, session, tenant, employee, second_employee, req
):
    """Отметка — изменение заявки, значит те же права, что у смены статуса."""
    from app.domain.requests import claim_request

    await claim_request(session, request_id=req.id, staff=employee)
    await session.commit()

    await _login(http, tenant, second_employee)
    assert (await http.post(f"/staff/requests/{req.id}/checklist/0")).status_code == 403


# --- чего не хватает ---------------------------------------------------------


def test_missing_counts_only_required(req):
    req.received_documents = []
    required = [d for d in req.checklist if d.get("is_required", True)]
    assert len(req.missing_documents) == len(required)


def test_missing_shrinks_as_documents_arrive(req):
    req.received_documents = [0]
    titles = [d["title"] for d in req.missing_documents]
    assert req.checklist[0]["title"] not in titles


def test_optional_document_never_counts_as_missing(req):
    optional = [
        i for i, d in enumerate(req.checklist) if not d.get("is_required", True)
    ]
    if not optional:
        pytest.skip("в перечне нет необязательных пунктов")
    req.received_documents = []
    titles = [d["title"] for d in req.missing_documents]
    assert req.checklist[optional[0]]["title"] not in titles


# --- что видит клиент --------------------------------------------------------


async def test_client_sees_what_is_already_received(http, session, tenant, req):
    _, token = await issue_upload_token(session, request=req)
    req.received_documents = [0]
    await session.commit()

    page = await http.get(f"/upload/{token}")
    assert page.status_code == 200
    assert "получено" in page.text
    assert "Осталось прислать" in page.text


async def test_client_sees_plain_list_when_nothing_received(http, session, tenant, req):
    _, token = await issue_upload_token(session, request=req)
    await session.commit()

    page = await http.get(f"/upload/{token}")
    assert "Нужны эти документы" in page.text
    assert "получено" not in page.text
