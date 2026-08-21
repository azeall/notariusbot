"""Кабинет владельца сервиса.

Отсюда подключают нотариусов: вы заводите карточку и отдаёте нотариусу ссылку,
по которой он сам задаёт почту и пароль. Нотариус не ищет никакую регистрацию —
он получает ссылку от вас и сразу попадает в свой кабинет.
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.security import (
    generate_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.domain.slugs import SLUG_RE, suggest_slug
from app.domain.starter import create_default_schedule, create_starter_catalog
from app.domain.theme import DEFAULT_ACCENT, FONTS, MODES, normalize_accent
from app.models import PlatformAdmin, Request, Staff, StaffRole, Tenant
from app.web.deps import (
    SESSION_COOKIE,
    db_session,
    issue_session_cookie,
    public_base_url,
)

router = APIRouter(tags=["platform"])

PLATFORM_COOKIE = "notarybot_platform"
INVITE_TTL = timedelta(days=14)

_DUMMY_HASH = hash_password("2f1a-none")


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="platform-session")


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


async def current_platform_admin(
    http_request: HttpRequest, session: AsyncSession = Depends(db_session)
) -> PlatformAdmin:
    raw = http_request.cookies.get(PLATFORM_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    try:
        payload = _serializer().loads(raw)
        admin_id = uuid.UUID(payload["admin_id"])
    except (BadSignature, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход") from exc

    admin = await session.get(PlatformAdmin, admin_id)
    if admin is None or not admin.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется вход")
    return admin


# --- вход -------------------------------------------------------------------


@router.get("/platform/login", response_class=HTMLResponse)
async def login_form(http_request: HttpRequest):
    return _templates().TemplateResponse(
        http_request,
        "platform_login.html",
        {"stylesheet": "/static/platform.css", "title": "Вход", "error": None},
    )


@router.post("/platform/login")
async def login(
    http_request: HttpRequest,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(db_session),
):
    admin = await session.scalar(
        select(PlatformAdmin).where(
            PlatformAdmin.email == email.strip().lower(),
            PlatformAdmin.is_active.is_(True),
        )
    )
    ok = verify_password(password, admin.password_hash if admin else _DUMMY_HASH)
    if admin is None or not ok:
        return _templates().TemplateResponse(
            http_request,
            "platform_login.html",
            {
                "stylesheet": "/static/platform.css",
                "title": "Вход", "error": "Неверная почта или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse("/platform", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        PLATFORM_COOKIE,
        _serializer().dumps({"admin_id": str(admin.id)}),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/platform/logout")
async def logout() -> Response:
    response = RedirectResponse("/platform/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(PLATFORM_COOKIE)
    return response


# --- смена пароля -----------------------------------------------------------


@router.get("/platform/password", response_class=HTMLResponse)
async def password_form(
    http_request: HttpRequest, admin: PlatformAdmin = Depends(current_platform_admin)
):
    return _templates().TemplateResponse(
        http_request,
        "change_password.html",
        {
            "stylesheet": "/static/platform.css",
            "title": "Смена пароля",
            "who": admin.full_name or admin.email,
            "action": "/platform/password",
            "back": "/platform",
            "error": None,
            "done": False,
        },
    )


@router.post("/platform/password")
async def change_password(
    http_request: HttpRequest,
    current_password: str = Form(...),
    new_password: str = Form(...),
    repeat_password: str = Form(...),
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    def render(error: str | None, done: bool = False):
        return _templates().TemplateResponse(
            http_request,
            "change_password.html",
            {
                "stylesheet": "/static/platform.css",
                "title": "Смена пароля",
                "who": admin.full_name or admin.email,
                "action": "/platform/password",
                "back": "/platform",
                "error": error,
                "done": done,
            },
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    # Текущий пароль спрашиваем всегда: иначе любой, кто подсел за незапертый
    # компьютер, сменит его и заберёт кабинет себе.
    if not verify_password(current_password, admin.password_hash):
        return render("Текущий пароль неверный.")
    if len(new_password) < 8:
        return render("Новый пароль короче 8 символов.")
    if new_password != repeat_password:
        return render("Новый пароль и повтор не совпадают.")
    if new_password == current_password:
        return render("Новый пароль совпадает со старым.")

    admin.password_hash = hash_password(new_password)
    await session.flush()
    return render(None, done=True)


# --- нотариусы --------------------------------------------------------------


@router.get("/platform", response_class=HTMLResponse)
async def tenants_index(
    http_request: HttpRequest,
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    tenants = list(await session.scalars(select(Tenant).order_by(Tenant.created_at.desc())))
    return _templates().TemplateResponse(
        http_request,
        "platform_tenants.html",
        {
            "stylesheet": "/static/platform.css",
            "title": "Нотариусы",
            "admin": admin,
            "tenants": tenants,
            "http_request": http_request,
            "base": public_base_url(http_request),
        },
    )


@router.get("/platform/new", response_class=HTMLResponse)
async def new_tenant_form(
    http_request: HttpRequest, admin: PlatformAdmin = Depends(current_platform_admin)
):
    return _templates().TemplateResponse(
        http_request,
        "platform_tenant_form.html",
        {
            "stylesheet": "/static/platform.css",
                "title": "Новый нотариус", "admin": admin, "tenant": None, "error": None},
    )


@router.get("/platform/tenants/{tenant_id}", response_class=HTMLResponse)
async def edit_tenant_form(
    tenant_id: uuid.UUID,
    http_request: HttpRequest,
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")
    return _templates().TemplateResponse(
        http_request,
        "platform_tenant_form.html",
        {
            "stylesheet": "/static/platform.css",
                "title": tenant.display_name, "admin": admin, "tenant": tenant, "error": None},
    )


@router.post("/platform/tenants")
async def save_tenant(
    http_request: HttpRequest,
    tenant_id: str = Form(""),
    display_name: str = Form(...),
    slug: str = Form(""),
    city: str = Form(""),
    address: str = Form(""),
    phone: str = Form(""),
    timezone: str = Form("Europe/Moscow"),
    allowed_origins: str = Form(""),
    widget_mode: str = Form("dark"),
    widget_accent: str = Form(DEFAULT_ACCENT),
    widget_font: str = Form("sans"),
    is_active: str = Form(""),
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    display_name = display_name.strip()
    code = (slug.strip() or suggest_slug(display_name)).lower()

    def fail(message: str, tenant=None):
        return _templates().TemplateResponse(
            http_request,
            "platform_tenant_form.html",
            {
                "stylesheet": "/static/platform.css",
                "title": "Нотариус", "admin": admin, "tenant": tenant, "error": message},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(display_name) < 3:
        return fail("Укажите, как нотариуса увидит клиент.")
    if not SLUG_RE.match(code):
        return fail("Код: латинские буквы, цифры и дефис, от 3 символов.")

    # Домены для встраивания виджета: по одному в строке. Пусто — можно
    # встраивать откуда угодно, это нормально для демо и первых клиентов.
    origins = [line.strip() for line in allowed_origins.splitlines() if line.strip()]

    if tenant_id:
        tenant = await session.get(Tenant, uuid.UUID(tenant_id))
        if tenant is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")
        if tenant.slug != code:
            taken = await session.scalar(select(Tenant).where(Tenant.slug == code))
            if taken is not None:
                return fail(f"Код «{code}» уже занят.", tenant)
        fresh = False
    else:
        taken = await session.scalar(select(Tenant).where(Tenant.slug == code))
        if taken is not None:
            return fail(f"Код «{code}» уже занят.")
        tenant = Tenant(slug=code)
        session.add(tenant)
        fresh = True

    tenant.slug = code
    tenant.display_name = display_name
    tenant.city = city.strip()
    tenant.address = address.strip()
    tenant.phone = phone.strip()
    tenant.timezone = timezone.strip() or "Europe/Moscow"
    tenant.allowed_origins = origins
    tenant.widget_mode = widget_mode if widget_mode in MODES else "dark"
    tenant.widget_accent = normalize_accent(widget_accent)
    tenant.widget_font = widget_font if widget_font in FONTS else "sans"
    tenant.is_active = bool(is_active) or fresh
    await session.flush()

    if fresh:
        # Каталог и часы заводим сразу: нотариус получит рабочий кабинет,
        # а не пустой экран с кнопкой «добавить услугу».
        await create_default_schedule(session, tenant)
        await create_starter_catalog(session, tenant)
        token = await _issue_invite(session, tenant)
        return RedirectResponse(
            f"/platform?invited={tenant.slug}&token={token}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    return RedirectResponse("/platform", status_code=status.HTTP_303_SEE_OTHER)


async def _issue_invite(session: AsyncSession, tenant: Tenant) -> str:
    token = generate_token()
    tenant.invite_token_hash = hash_token(token)
    tenant.invite_expires_at = datetime.now(UTC) + INVITE_TTL
    tenant.invite_accepted_at = None
    await session.flush()
    return token


@router.post("/platform/tenants/{tenant_id}/delete")
async def delete_tenant(
    tenant_id: uuid.UUID,
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    """Удалить нотариуса вместе со всем, что за ним стоит.

    Нужно прежде всего для опечаток при заведении: без этого ошибочная карточка
    остаётся в списке навсегда. Заявки и документы удаляются каскадом, поэтому
    у действующего нотариуса это делать нельзя — его лучше отключить галочкой.
    """
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")

    requests_count = await session.scalar(
        select(func.count(Request.id)).where(Request.tenant_id == tenant.id)
    )
    if int(requests_count or 0) > 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "У этого нотариуса уже есть заявки — их нельзя стирать. "
            "Снимите галочку «Обслуживается», чтобы прекратить приём.",
        )

    await session.delete(tenant)
    return RedirectResponse("/platform", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/platform/tenants/{tenant_id}/invite")
async def reissue_invite(
    tenant_id: uuid.UUID,
    admin: PlatformAdmin = Depends(current_platform_admin),
    session: AsyncSession = Depends(db_session),
):
    """Новая ссылка: прежняя перестаёт работать."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")
    token = await _issue_invite(session, tenant)
    return RedirectResponse(
        f"/platform?invited={tenant.slug}&token={token}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# --- приглашение для нотариуса ---------------------------------------------


async def _tenant_by_invite(session: AsyncSession, token: str) -> Tenant | None:
    tenant = await session.scalar(
        select(Tenant).where(Tenant.invite_token_hash == hash_token(token))
    )
    if tenant is None or tenant.invite_expires_at is None:
        return None
    if tenant.invite_expires_at <= datetime.now(UTC):
        return None
    return tenant


@router.get("/invite/{token}", response_class=HTMLResponse)
async def invite_form(
    token: str, http_request: HttpRequest, session: AsyncSession = Depends(db_session)
):
    tenant = await _tenant_by_invite(session, token)
    if tenant is None:
        return _templates().TemplateResponse(
            http_request,
            "invite_expired.html",
            {
                "stylesheet": "/static/platform.css",
                "title": "Ссылка недействительна"},
            status_code=status.HTTP_410_GONE,
        )
    return _templates().TemplateResponse(
        http_request,
        "invite.html",
        {
            "stylesheet": "/static/platform.css",
                "title": "Создание входа", "tenant": tenant, "token": token, "error": None},
    )


@router.post("/invite/{token}")
async def accept_invite(
    token: str,
    http_request: HttpRequest,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(db_session),
):
    tenant = await _tenant_by_invite(session, token)
    if tenant is None:
        raise HTTPException(status.HTTP_410_GONE, "Ссылка истекла")

    email = email.strip().lower()

    def fail(message: str):
        return _templates().TemplateResponse(
            http_request,
            "invite.html",
            {
                "stylesheet": "/static/platform.css",
                "title": "Создание входа",
                "tenant": tenant,
                "token": token,
                "error": message,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(password) < 8:
        return fail("Пароль короче 8 символов.")
    if "@" not in email:
        return fail("Почта выглядит неправильно.")

    taken = await session.scalar(
        select(Staff).where(Staff.tenant_id == tenant.id, Staff.email == email)
    )
    if taken is not None:
        return fail("Такая почта уже заведена у этого нотариуса.")

    owner = Staff(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role=StaffRole.OWNER,
    )
    session.add(owner)

    # Ссылка одноразовая: приняли — больше по ней не войти.
    tenant.invite_token_hash = None
    tenant.invite_expires_at = None
    tenant.invite_accepted_at = datetime.now(UTC)
    await session.flush()

    response = RedirectResponse("/admin?welcome=1", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session_cookie(owner),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response
