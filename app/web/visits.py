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

from fastapi import APIRouter, Depends, Form, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain import visit_links
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Appointment, Request, RequestEvent, Service, Tenant
from app.web.deps import db_session

router = APIRouter(tags=["visits"])


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


async def _load(session: AsyncSession, token: str):
    """Заявка, её запись и услуга по подписанной ссылке."""
    request_id = visit_links.read(token)
    if request_id is None:
        return None, None, None, None

    request = await session.get(Request, request_id)
    if request is None:
        return None, None, None, None

    appointment = await session.scalar(
        select(Appointment).where(
            Appointment.request_id == request.id,
            Appointment.is_cancelled.is_(False),
        )
    )
    service = await session.get(Service, request.service_id) if request.service_id else None
    tenant = await session.get(Tenant, request.tenant_id)
    return request, appointment, service, tenant


def _gone(http_request: HttpRequest) -> Response:
    return _templates().TemplateResponse(
        http_request,
        "visit_gone.html",
        {"title": "Ссылка недействительна"},
        status_code=status.HTTP_410_GONE,
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
    return _templates().TemplateResponse(
        http_request,
        "visit.html",
        {
            "title": "Ваша запись",
            "token": token,
            "tenant": tenant,
            "req": request,
            "appointment": appointment,
            "slots": [s for s in slots if s != appointment.starts_at][:12],
            "moved": http_request.query_params.get("moved") == "1",
        },
    )


@router.post("/visit/{token}/move")
async def move_visit(
    token: str,
    http_request: HttpRequest,
    slot: str = Form(...),
    session: AsyncSession = Depends(db_session),
) -> Response:
    from datetime import datetime

    request, appointment, service, tenant = await _load(session, token)
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
        return RedirectResponse(f"/visit/{token}?busy=1", status_code=status.HTTP_303_SEE_OTHER)

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            comment=(
                f"Клиент перенёс приём: {appointment.starts_at:%d.%m %H:%M} → "
                f"{fresh.starts_at:%d.%m %H:%M}"
            ),
        )
    )
    await session.flush()
    return RedirectResponse(f"/visit/{token}?moved=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/visit/{token}/cancel")
async def cancel_visit(
    token: str,
    http_request: HttpRequest,
    session: AsyncSession = Depends(db_session),
) -> Response:
    request, appointment, _, _ = await _load(session, token)
    if request is None or appointment is None:
        return _gone(http_request)

    appointment.is_cancelled = True
    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            comment=f"Клиент отменил приём {appointment.starts_at:%d.%m %H:%M}",
        )
    )
    await session.flush()

    return _templates().TemplateResponse(
        http_request,
        "visit_cancelled.html",
        {"title": "Запись отменена", "tenant": await session.get(Tenant, request.tenant_id)},
    )


def visit_url(base: str, request_id: uuid.UUID) -> str:
    return visit_links.url_for(base, request_id)
