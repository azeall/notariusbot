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


def client_ip(request: HttpRequest) -> str:
    """IP клиента с учётом обратного прокси."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""
