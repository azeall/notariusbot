"""Страница, на которой клиент сам переносит или отменяет визит.

Открывается по подписанной ссылке из подтверждения и напоминания. Логина
здесь нет и быть не может: клиент — не пользователь сервиса, и заводить ему
учётную запись ради переноса времени значит гарантировать, что он позвонит
вместо этого.

Личные данные на странице не показываются: по ссылке видно услугу и время,
но не телефон и не документы. Ссылку пересылают в мессенджерах, и она
переживёт того, кому предназначалась.
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import visit_links
from app.domain.calendar_file import appointment_calendar
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Appointment, Request, RequestEvent, Service, Tenant
from app.web.deps import db_session

router = APIRouter(tags=["visits"])
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


async def _load(session: AsyncSession, token: str, *, for_update: bool = False):
    """Заявка, её запись и услуга по подписанной ссылке."""
    request_id = visit_links.read(token)
    if request_id is None:
        return None, None, None, None

    request = await session.get(Request, request_id)
    if request is None:
        return None, None, None, None

    # Тот же порядок блокировок, что при создании записи: контора, затем заявка.
    # Два переноса одной ссылки не должны оставлять две активные записи.
    tenant_query = select(Tenant).where(Tenant.id == request.tenant_id).execution_options(populate_existing=True)
    if for_update:
        tenant_query = tenant_query.with_for_update(key_share=True)
    tenant = await session.scalar(tenant_query)
    if for_update:
        request = await session.scalar(select(Request).where(Request.id == request_id)
                                       .with_for_update().execution_options(populate_existing=True))
    if (tenant is None or not tenant.is_active or request is None or not request.is_open
            or request.created_at < datetime.now(UTC) - timedelta(days=visit_links.TTL_DAYS)):
        return None, None, None, None

    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.request_id == request.id,
            Appointment.tenant_id == tenant.id,
            Appointment.is_cancelled.is_(False),
            Appointment.starts_at > datetime.now(UTC),
        )
    )
    service = await session.get(Service, request.service_id) if request.service_id else None
    if service is not None and service.tenant_id != tenant.id:
        return None, None, None, None
    return request, appointment, service, tenant


def _gone(http_request: HttpRequest) -> Response:
    return _templates().TemplateResponse(
        http_request,
        "visit_gone.html",
        {"title": "Ссылка недействительна"},
        status_code=status.HTTP_410_GONE,
        headers=PRIVATE_HEADERS,
    )


@router.get("/visit/{token}", response_class=HTMLResponse)
async def visit_page(
    token: str,
    http_request: HttpRequest,
    session: AsyncSession = Depends(db_session),
) -> Response:
    request, appointment, service, tenant = await _load(session, token)
    if request is None or appointment is None or service is None:
        return _gone(http_request)

    slots = await available_slots(session, tenant=tenant, service=service)
    tz = ZoneInfo(tenant.timezone)
    return _templates().TemplateResponse(
        http_request,
        "visit.html",
        {
            "title": "Ваша запись",
            "token": token,
            "tenant": tenant,
            "req": request,
            "appointment": appointment,
            "local_start": appointment.starts_at.astimezone(tz),
            "slots": [s.astimezone(tz) for s in slots if s != appointment.starts_at][:60],
            "moved": http_request.query_params.get("moved") == "1",
            "busy": http_request.query_params.get("busy") == "1",
            "preparation_url": f"/{tenant.slug}/services/{service.id}/prepare" if service.is_active else None,
        },
        headers=PRIVATE_HEADERS,
    )


@router.get("/visit/{token}/calendar.ics")
async def calendar_download(token: str, http_request: HttpRequest,
                            session: AsyncSession = Depends(db_session)) -> Response:
    request, appointment, service, tenant = await _load(session, token)
    if request is None or appointment is None or service is None:
        return _gone(http_request)
    return Response(appointment_calendar(appointment, tenant), media_type="text/calendar; charset=utf-8",
                    headers={**PRIVATE_HEADERS, "Content-Disposition": 'attachment; filename="notary-visit.ics"'})


@router.post("/visit/{token}/move")
async def move_visit(
    token: str,
    http_request: HttpRequest,
    slot: str = Form(...),
    session: AsyncSession = Depends(db_session),
) -> Response:
    request, appointment, service, tenant = await _load(session, token, for_update=True)
    if request is None or appointment is None or service is None:
        return _gone(http_request)

    try:
        starts_at = datetime.fromisoformat(slot)
    except ValueError:
        return _gone(http_request)

    # Старую запись снимаем до создания новой: уникальность окна проверяется
    # по неотменённым, и без этого клиент не смог бы вернуться на своё же время.
    appointment.is_cancelled = True
    await session.flush()

    try:
        fresh = await book_slot(session, request=request, service=service, starts_at=starts_at)
    except SlotUnavailable:
        # Кто-то занял окно, пока клиент выбирал. Возвращаем прежнее время:
        # остаться вообще без записи хуже, чем не перенести.
        appointment.is_cancelled = False
        await session.flush()
        return RedirectResponse(f"/visit/{token}?busy=1", status_code=status.HTTP_303_SEE_OTHER,
                                headers=PRIVATE_HEADERS)

    tz = ZoneInfo(tenant.timezone)
    old_local = appointment.starts_at.astimezone(tz)
    new_local = fresh.starts_at.astimezone(tz)
    request.preferred_time_note = f"{new_local:%d.%m.%Y, %H:%M} ({tenant.timezone})"

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            comment=(
                f"Клиент перенёс приём: {old_local:%d.%m %H:%M} → "
                f"{new_local:%d.%m %H:%M} ({tenant.timezone})"
            ),
        )
    )
    await session.flush()
    return RedirectResponse(f"/visit/{token}?moved=1", status_code=status.HTTP_303_SEE_OTHER,
                            headers=PRIVATE_HEADERS)


@router.post("/visit/{token}/cancel")
async def cancel_visit(
    token: str,
    http_request: HttpRequest,
    session: AsyncSession = Depends(db_session),
) -> Response:
    request, appointment, _, tenant = await _load(session, token, for_update=True)
    if request is None or appointment is None:
        return _gone(http_request)

    appointment.is_cancelled = True
    request.preferred_time_note = "Клиент отменил запись. Новое время не выбрано."
    local_start = appointment.starts_at.astimezone(ZoneInfo(tenant.timezone))
    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            comment=f"Клиент отменил приём {local_start:%d.%m %H:%M} ({tenant.timezone})",
        )
    )
    await session.flush()

    return _templates().TemplateResponse(
        http_request,
        "visit_cancelled.html",
        {"title": "Запись отменена", "tenant": tenant},
        headers=PRIVATE_HEADERS,
    )


def visit_url(base: str, request_id: uuid.UUID) -> str:
    return visit_links.url_for(base, request_id)
