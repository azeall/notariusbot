import uuid
from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request as HttpRequest, status
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import Staff, Tenant

SESSION_COOKIE = "notarybot_session"


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="staff-session")


def issue_session_cookie(staff: Staff) -> str:
    return _serializer().dumps({"staff_id": str(staff.id)})


def read_session_cookie(raw: str) -> uuid.UUID | None:
    try:
        payload = _serializer().loads(raw)
    except BadSignature:
        return None
    try:
        return uuid.UUID(payload["staff_id"])
    except (KeyError, ValueError, TypeError):
        return None


async def db_session() -> AsyncIterator[AsyncSession]:
    async for session in get_session():
        yield session


async def resolve_tenant(slug: str, session: AsyncSession = Depends(db_session)) -> Tenant:
    tenant = await session.scalar(
        select(Tenant).where(Tenant.slug == slug, Tenant.is_active.is_(True))
    )
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")
    return tenant


async def optional_staff(
    request: HttpRequest, session: AsyncSession = Depends(db_session)
) -> Staff | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    staff_id = read_session_cookie(raw)
    if staff_id is None:
        return None
    staff = await session.get(Staff, staff_id)
    if staff is None or not staff.is_active:
        return None
    return staff


async def current_staff(staff: Staff | None = Depends(optional_staff)) -> Staff:
    if staff is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    return staff


async def current_owner(staff: Staff = Depends(current_staff)) -> Staff:
    if not staff.can_manage_catalog:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Доступно только владельцу")
    return staff


def public_base_url(request: HttpRequest) -> str:
    """Публичный адрес сервиса с точки зрения пришедшего запроса.

    Одноразовые ссылки на загрузку обязаны вести на тот же хост, с которого
    клиент только что общался с нами. Брать его из настройки нельзя: за прокси
    или туннелем адрес меняется, и настройка протухает — ссылка уходит клиенту
    битой. Заголовки X-Forwarded-* ставит прокси, поэтому им можно верить
    ровно настолько, насколько прокси свой.
    """
    forwarded_host = request.headers.get("x-forwarded-host", "")
    host = forwarded_host.split(",")[0].strip() or request.headers.get("host", "")
    if not host:
        return get_settings().public_base_url.rstrip("/")

    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    if forwarded_proto:
        scheme = forwarded_proto
    elif host.split(":")[0] in {"localhost", "127.0.0.1", "::1"}:
        scheme = request.url.scheme
    else:
        # Прокси не сказал схему, а хост не локальный. По этой ссылке клиент
        # понесёт скан паспорта, поэтому http тут недопустим ни при каких
        # обстоятельствах — публичный адрес считаем https.
        scheme = "https"
    return f"{scheme}://{host}"


def client_ip(request: HttpRequest) -> str:
    """IP клиента с учётом обратного прокси."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
