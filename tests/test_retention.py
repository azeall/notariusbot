"""Удаление документов: по сроку, по предельному возрасту и вручную.

Проверять это тестами важнее, чем кажется. Ошибка в одну сторону — паспорта
клиентов лежат вечно; в другую — документы исчезают из-под работающей заявки.
Обе тихие: ничего не падает, никто не замечает.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.domain.requests import create_request
from app.domain.storage import DocumentStorage
from app.models import Attachment, AuditLog, Channel, RequestStatus
from app.retention import purge_expired_documents
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


async def _attach(session, tenant, request, *, age_days: int = 0) -> Attachment:
    """Положить настоящий файл в хранилище и завести запись о нём."""
    storage = DocumentStorage()
    path = storage.save(tenant.id, request.id, b"%PDF-1.4 scan")
    attachment = Attachment(
        tenant_id=tenant.id,
        request_id=request.id,
        original_filename="паспорт.pdf",
        content_type="application/pdf",
        size_bytes=16,
        storage_path=path,
    )
    session.add(attachment)
    await session.flush()
    if age_days:
        attachment.created_at = datetime.now(UTC) - timedelta(days=age_days)
        await session.flush()
    return attachment


@pytest.fixture
async def request_with_doc(session, tenant, client, service):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    attachment = await _attach(session, tenant, request)
    await session.commit()
    return request, attachment


# --- обычный ход: закрыли заявку, вышел срок ---------------------------------


async def test_closed_long_ago_is_purged(session, request_with_doc):
    request, attachment = request_with_doc
    days = get_settings().document_retention_days
    request.status = RequestStatus.COMPLETED
    request.closed_at = datetime.now(UTC) - timedelta(days=days + 1)
    await session.commit()

    assert await purge_expired_documents(session=session) == 1

    await session.refresh(attachment)
    assert attachment.purged_at is not None
    assert not DocumentStorage().exists(attachment.storage_path)


async def test_closed_recently_is_kept(session, request_with_doc):
    """Заявка закрыта вчера — документ ещё нужен, клиент может вернуться."""
    request, attachment = request_with_doc
    request.status = RequestStatus.COMPLETED
    request.closed_at = datetime.now(UTC) - timedelta(days=1)
    await session.commit()

    assert await purge_expired_documents(session=session) == 0

    await session.refresh(attachment)
    assert attachment.purged_at is None
    assert DocumentStorage().exists(attachment.storage_path)


async def test_open_request_keeps_documents(session, request_with_doc):
    """Заявка в работе — документы трогать нельзя, по ним работают."""
    _, attachment = request_with_doc
    assert await purge_expired_documents(session=session) == 0
    await session.refresh(attachment)
    assert attachment.purged_at is None


# --- предохранитель: заявку забыли закрыть -----------------------------------


async def test_forgotten_request_still_loses_documents(session, tenant, client, service):
    """Главное правило: незакрытая заявка не означает вечного хранения.

    Раньше удаление висело на closed_at, и забытая заявка держала паспорта
    без срока — молча, потому что ничего не ломалось.
    """
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    attachment = await _attach(
        session, tenant, request, age_days=get_settings().document_max_age_days + 1
    )
    await session.commit()
    assert request.closed_at is None

    assert await purge_expired_documents(session=session) == 1

    await session.refresh(attachment)
    assert attachment.purged_at is not None
    assert not DocumentStorage().exists(attachment.storage_path)


async def test_purge_reason_says_which_rule_worked(session, tenant, client, service):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await _attach(session, tenant, request, age_days=get_settings().document_max_age_days + 1)
    await session.commit()

    await purge_expired_documents(session=session)

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "document_purged"))
    assert entry is not None
    assert "предельный срок" in entry.details


async def test_dry_run_counts_but_keeps_files(session, request_with_doc):
    request, attachment = request_with_doc
    request.closed_at = datetime.now(UTC) - timedelta(
        days=get_settings().document_retention_days + 1
    )
    await session.commit()

    assert await purge_expired_documents(dry_run=True, session=session) == 1

    await session.refresh(attachment)
    assert attachment.purged_at is None
    assert DocumentStorage().exists(attachment.storage_path)


async def test_purge_is_not_repeated(session, request_with_doc):
    request, attachment = request_with_doc
    request.closed_at = datetime.now(UTC) - timedelta(
        days=get_settings().document_retention_days + 1
    )
    await session.commit()

    assert await purge_expired_documents(session=session) == 1
    assert await purge_expired_documents(session=session) == 0


# --- удаление вручную --------------------------------------------------------


async def _login(http, tenant, person, password="secret123"):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": person.email, "password": password}
    )


async def test_owner_deletes_document(http, session, tenant, owner, request_with_doc):
    request, attachment = request_with_doc
    await _login(http, tenant, owner)

    response = await http.post(
        f"/staff/requests/{request.id}/documents/{attachment.id}/delete",
        data={"reason": "клиент отозвал согласие"},
    )
    assert response.status_code == 303

    await session.refresh(attachment)
    assert attachment.purged_at is not None
    assert not DocumentStorage().exists(attachment.storage_path)


async def test_deletion_is_written_to_journal(http, session, tenant, owner, request_with_doc):
    request, attachment = request_with_doc
    await _login(http, tenant, owner)
    await http.post(
        f"/staff/requests/{request.id}/documents/{attachment.id}/delete",
        data={"reason": "клиент отозвал согласие"},
    )

    entry = await session.scalar(select(AuditLog).where(AuditLog.action == "document_deleted"))
    assert entry is not None
    assert entry.actor_label == owner.full_name
    assert "отозвал согласие" in entry.details
    assert "паспорт.pdf" in entry.details


async def test_employee_cannot_delete(http, session, tenant, employee, request_with_doc):
    """Удаление необратимо, и отвечает за данные нотариус — не помощник."""
    request, attachment = request_with_doc
    await _login(http, tenant, employee)

    response = await http.post(
        f"/staff/requests/{request.id}/documents/{attachment.id}/delete"
    )
    assert response.status_code == 403

    await session.refresh(attachment)
    assert attachment.purged_at is None
    assert DocumentStorage().exists(attachment.storage_path)


async def test_cannot_delete_twice(http, session, tenant, owner, request_with_doc):
    request, attachment = request_with_doc
    await _login(http, tenant, owner)
    url = f"/staff/requests/{request.id}/documents/{attachment.id}/delete"

    assert (await http.post(url)).status_code == 303
    assert (await http.post(url)).status_code == 410


async def test_owner_cannot_delete_in_other_tenant(
    http, session, tenant, other_tenant, owner, request_with_doc
):
    request, attachment = request_with_doc
    attachment.tenant_id = other_tenant.id
    await session.commit()
    await _login(http, tenant, owner)

    response = await http.post(
        f"/staff/requests/{request.id}/documents/{attachment.id}/delete"
    )
    assert response.status_code == 404


async def test_deleted_document_cannot_be_downloaded(
    http, session, tenant, owner, request_with_doc
):
    request, attachment = request_with_doc
    await _login(http, tenant, owner)
    await http.post(f"/staff/requests/{request.id}/documents/{attachment.id}/delete")

    response = await http.get(f"/staff/requests/{request.id}/documents/{attachment.id}")
    assert response.status_code == 410
