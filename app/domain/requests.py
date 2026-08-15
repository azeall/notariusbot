import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.security import generate_token, hash_token
from app.domain.statuses import ensure_transition_allowed
from app.models import (
    TERMINAL_STATUSES,
    Channel,
    Client,
    Request,
    RequestEvent,
    RequestStatus,
    Service,
    Staff,
    SubmissionMode,
    Tenant,
    UploadToken,
)


class RequestError(Exception):
    """Нарушение правил работы с заявкой."""


async def _next_public_number(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """Следующий номер заявки в пределах нотариуса.

    Блокируем строку нотариуса: без этого два одновременных обращения получат
    один номер и вставка упадёт на уникальном индексе.
    """
    await session.execute(
        select(Tenant.id).where(Tenant.id == tenant_id).with_for_update()
    )
    current = await session.scalar(
        select(func.coalesce(func.max(Request.public_number), 0)).where(
            Request.tenant_id == tenant_id
        )
    )
    return int(current or 0) + 1


async def create_request(
    session: AsyncSession,
    *,
    tenant: Tenant,
    client: Client,
    service: Service,
    channel: Channel,
    client_comment: str = "",
    preferred_time_note: str = "",
    source_ip: str = "",
) -> Request:
    """Создать заявку, зафиксировав перечень документов на момент обращения."""
    if service.tenant_id != tenant.id:
        raise RequestError("Услуга принадлежит другому нотариусу")
    if client.tenant_id != tenant.id:
        raise RequestError("Клиент принадлежит другому нотариусу")
    if not service.is_active:
        raise RequestError("Услуга сейчас недоступна")

    request = Request(
        tenant_id=tenant.id,
        public_number=await _next_public_number(session, tenant.id),
        client_id=client.id,
        service_id=service.id,
        service_title=service.title,
        submission_mode=service.submission_mode,
        checklist=service.checklist_snapshot(),
        status=RequestStatus.NEW,
        channel=channel,
        client_comment=client_comment,
        preferred_time_note=preferred_time_note,
        source_ip=source_ip,
    )
    session.add(request)
    await session.flush()

    session.add(
        RequestEvent(
            tenant_id=tenant.id,
            request_id=request.id,
            actor_label=client.full_name or "Клиент",
            from_status="",
            to_status=RequestStatus.NEW.value,
            comment="Заявка создана",
        )
    )
    await session.flush()
    return request


async def claim_request(
    session: AsyncSession, *, request_id: uuid.UUID, staff: Staff
) -> Request | None:
    """Взять заявку в работу.

    Захват идёт одним UPDATE с условием «ещё никем не взята»: если два сотрудника
    нажмут кнопку одновременно, база отдаст заявку ровно одному, а второй получит
    None и увидит, что его опередили.
    """
    now = datetime.now(UTC)
    claimed_id = await session.scalar(
        update(Request)
        .where(
            Request.id == request_id,
            Request.tenant_id == staff.tenant_id,
            Request.status == RequestStatus.NEW,
            Request.assigned_staff_id.is_(None),
        )
        .values(
            status=RequestStatus.CLAIMED,
            assigned_staff_id=staff.id,
            claimed_at=now,
        )
        .returning(Request.id)
    )
    if claimed_id is None:
        return None

    session.add(
        RequestEvent(
            tenant_id=staff.tenant_id,
            request_id=claimed_id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            from_status=RequestStatus.NEW.value,
            to_status=RequestStatus.CLAIMED.value,
            comment="Взята в работу",
        )
    )
    await session.flush()
    # UPDATE шёл в обход ORM, поэтому в сессии может лежать устаревший объект.
    # populate_existing перечитывает строку, а не отдаёт кеш.
    return await session.get(Request, claimed_id, populate_existing=True)


async def transition_request(
    session: AsyncSession,
    *,
    request: Request,
    target: RequestStatus,
    staff: Staff | None = None,
    comment: str = "",
) -> Request:
    """Перевести заявку в другой статус с проверкой допустимости перехода."""
    if staff is not None and staff.tenant_id != request.tenant_id:
        raise RequestError("Сотрудник другого нотариуса")

    previous = request.status
    ensure_transition_allowed(previous, target)

    request.status = target
    if target in TERMINAL_STATUSES:
        request.closed_at = datetime.now(UTC)
    if target is RequestStatus.NEW:
        # Возврат в общую очередь: заявка снова ничья.
        request.assigned_staff_id = None
        request.claimed_at = None

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=staff.id if staff else None,
            actor_label=staff.full_name if staff else "Клиент",
            from_status=previous.value,
            to_status=target.value,
            comment=comment,
        )
    )
    await session.flush()
    return request


async def issue_upload_token(
    session: AsyncSession, *, request: Request
) -> tuple[UploadToken, str]:
    """Выдать одноразовую ссылку на загрузку документов.

    Возвращает саму запись и открытый токен — открытый вид существует только
    здесь и в ссылке клиенту, в базе остаётся лишь хеш.
    """
    if request.submission_mode is not SubmissionMode.DOCUMENTS:
        raise RequestError("Для этой услуги нужен личный визит, документы онлайн не принимаем")

    settings = get_settings()
    token = generate_token()
    record = UploadToken(
        tenant_id=request.tenant_id,
        request_id=request.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.upload_token_ttl_minutes),
    )
    session.add(record)
    await session.flush()
    return record, token


async def resolve_upload_token(session: AsyncSession, token: str) -> UploadToken | None:
    """Найти живой токен по значению из ссылки."""
    record = await session.scalar(
        select(UploadToken).where(UploadToken.token_hash == hash_token(token))
    )
    if record is None or record.is_revoked or record.used_at is not None:
        return None
    if record.expires_at <= datetime.now(UTC):
        return None
    return record


async def count_recent_requests_from_ip(
    session: AsyncSession, *, tenant_id: uuid.UUID, source_ip: str, within_hours: int = 1
) -> int:
    """Сколько заявок пришло с этого адреса за последние часы — для антиспама."""
    if not source_ip:
        return 0
    since = datetime.now(UTC) - timedelta(hours=within_hours)
    return int(
        await session.scalar(
            select(func.count(Request.id)).where(
                Request.tenant_id == tenant_id,
                Request.source_ip == source_ip,
                Request.created_at >= since,
            )
        )
        or 0
    )
