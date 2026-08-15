import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request as HttpRequest, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain import catalog
from app.domain.requests import (
    RequestError,
    count_recent_requests_from_ip,
    create_request,
    issue_upload_token,
)
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Channel, Client, Service, SubmissionMode, Tenant
from app.web.deps import client_ip, db_session, resolve_tenant
from app.web.schemas import DocumentOut, RequestIn, RequestOut, ServiceOut, SlotOut

router = APIRouter(prefix="/api/v1/{slug}", tags=["widget"])

CONSENT_VERSION = "2026-08-15"

WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _service_out(service: Service) -> ServiceOut:
    return ServiceOut(
        id=service.id,
        title=service.title,
        description=service.description,
        submission_mode=service.submission_mode.value,
        lead_time_note=service.lead_time_note,
        price_note=service.price_note,
        visit_duration_minutes=service.visit_duration_minutes,
        documents=[
            DocumentOut(
                title=doc.title, description=doc.description, is_required=doc.is_required
            )
            for doc in sorted(service.documents, key=lambda d: d.sort_order)
        ],
    )


def _slot_label(moment: datetime) -> str:
    return (
        f"{moment.day} {MONTHS_RU[moment.month - 1]}, "
        f"{WEEKDAYS_RU[moment.weekday()]}, {moment:%H:%M}"
    )


@router.get("/services", response_model=list[ServiceOut])
async def list_services(
    tenant: Tenant = Depends(resolve_tenant), session: AsyncSession = Depends(db_session)
) -> list[ServiceOut]:
    services = await catalog.list_services(session, tenant.id)
    return [_service_out(s) for s in services]


@router.get("/services/search", response_model=list[ServiceOut])
async def search_services(
    q: str = "",
    tenant: Tenant = Depends(resolve_tenant),
    session: AsyncSession = Depends(db_session),
) -> list[ServiceOut]:
    """Подбор услуги по свободному тексту.

    Результат клиент всё равно подтверждает вручную: ошибка подбора означает,
    что человек приедет не с теми документами.
    """
    if not q.strip():
        return []
    services = await catalog.search_services(session, tenant.id, q)
    return [_service_out(s) for s in services]


@router.get("/services/{service_id}/slots", response_model=list[SlotOut])
async def service_slots(
    service_id: uuid.UUID,
    tenant: Tenant = Depends(resolve_tenant),
    session: AsyncSession = Depends(db_session),
) -> list[SlotOut]:
    service = await catalog.get_service(session, tenant.id, service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Услуга не найдена")

    tz = ZoneInfo(tenant.timezone)
    slots = await available_slots(session, tenant=tenant, service=service, days_ahead=14)
    return [
        SlotOut(starts_at=slot, label=_slot_label(slot.astimezone(tz))) for slot in slots[:60]
    ]


async def _get_or_create_client(
    session: AsyncSession, *, tenant: Tenant, payload: RequestIn
) -> Client:
    client = await session.scalar(
        select(Client).where(
            Client.tenant_id == tenant.id,
            Client.channel == Channel.WIDGET,
            Client.external_id == payload.phone,
        )
    )
    if client is None:
        client = Client(
            tenant_id=tenant.id,
            channel=Channel.WIDGET,
            external_id=payload.phone,
            full_name=payload.full_name,
            phone=payload.phone,
        )
        session.add(client)
    else:
        client.full_name = payload.full_name

    client.consent_given_at = datetime.now(UTC)
    client.consent_text_version = CONSENT_VERSION
    await session.flush()
    return client


@router.post("/requests", response_model=RequestOut, status_code=status.HTTP_201_CREATED)
async def submit_request(
    payload: RequestIn,
    http_request: HttpRequest,
    tenant: Tenant = Depends(resolve_tenant),
    session: AsyncSession = Depends(db_session),
) -> RequestOut:
    settings = get_settings()

    # Поле-ловушка: заполнено — значит форму отправил не человек.
    if payload.website:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Заявка отклонена")

    ip = client_ip(http_request)
    recent = await count_recent_requests_from_ip(session, tenant_id=tenant.id, source_ip=ip)
    if recent >= settings.requests_per_ip_per_hour:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много заявок подряд. Попробуйте позже или позвоните нотариусу.",
        )

    service = await catalog.get_service(session, tenant.id, payload.service_id)
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Услуга не найдена")

    client = await _get_or_create_client(session, tenant=tenant, payload=payload)

    try:
        request = await create_request(
            session,
            tenant=tenant,
            client=client,
            service=service,
            channel=Channel.WIDGET,
            client_comment=payload.comment,
            source_ip=ip,
        )
    except RequestError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    appointment_at: datetime | None = None
    if service.submission_mode is SubmissionMode.VISIT:
        if payload.slot is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Для этой услуги нужно выбрать время визита"
            )
        try:
            appointment = await book_slot(
                session, request=request, service=service, starts_at=payload.slot
            )
        except SlotUnavailable as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
        appointment_at = appointment.starts_at
        request.preferred_time_note = _slot_label(
            appointment.starts_at.astimezone(ZoneInfo(tenant.timezone))
        )

    upload_url: str | None = None
    if service.submission_mode is SubmissionMode.DOCUMENTS:
        _, token = await issue_upload_token(session, request=request)
        upload_url = f"{settings.public_base_url}/upload/{token}"

    return RequestOut(
        id=request.id,
        public_number=request.public_number,
        status=request.status.value,
        service_title=request.service_title,
        submission_mode=request.submission_mode.value,
        checklist=[DocumentOut(**item) for item in request.checklist],
        upload_url=upload_url,
        appointment_at=appointment_at,
    )
