"""Самостоятельная регистрация нотариуса.

Без неё продукт нельзя продавать: подключение каждого нового нотариуса
требовало бы разработчика с доступом к базе.
"""

import re

from fastapi import APIRouter, Depends, Form, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.security import hash_password
from app.domain.starter import create_default_schedule, create_starter_catalog
from app.models import Staff, StaffRole, Tenant
from app.web.deps import SESSION_COOKIE, db_session, issue_session_cookie

router = APIRouter(tags=["signup"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def suggest_slug(display_name: str) -> str:
    """Код нотариуса из названия: «Нотариус Иванов И. И.» → ivanov."""
    lowered = display_name.lower().replace("нотариус", " ")
    latin = "".join(TRANSLIT.get(ch, ch) for ch in lowered)
    words = [w for w in re.split(r"[^a-z0-9]+", latin) if len(w) > 1]
    return "-".join(words[:2])[:64] or "notary"


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


def _render(http_request: HttpRequest, error: str | None = None, form: dict | None = None):
    return _templates().TemplateResponse(
        http_request,
        "signup.html",
        {"title": "Регистрация", "error": error, "form": form or {}},
        status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
    )


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(http_request: HttpRequest):
    return _render(http_request)


@router.post("/signup")
async def signup(
    http_request: HttpRequest,
    display_name: str = Form(...),
    city: str = Form(""),
    phone: str = Form(""),
    slug: str = Form(""),
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(db_session),
):
    form = {
        "display_name": display_name,
        "city": city,
        "phone": phone,
        "slug": slug,
        "full_name": full_name,
        "email": email,
    }

    display_name = display_name.strip()
    email = email.strip().lower()
    code = (slug.strip() or suggest_slug(display_name)).lower()

    if len(display_name) < 3:
        return _render(http_request, "Укажите, как вас увидит клиент.", form)
    if len(password) < 8:
        return _render(http_request, "Пароль короче 8 символов.", form)
    if "@" not in email:
        return _render(http_request, "Почта выглядит неправильно.", form)
    if not SLUG_RE.match(code):
        return _render(
            http_request,
            "Код может состоять из латинских букв, цифр и дефиса — от 3 символов.",
            form,
        )

    taken = await session.scalar(select(Tenant).where(Tenant.slug == code))
    if taken is not None:
        return _render(http_request, f"Код «{code}» уже занят. Придумайте другой.", form)

    tenant = Tenant(
        slug=code,
        display_name=display_name,
        city=city.strip(),
        phone=phone.strip(),
        allowed_origins=[],
    )
    session.add(tenant)
    await session.flush()

    owner = Staff(
        tenant_id=tenant.id,
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        role=StaffRole.OWNER,
    )
    session.add(owner)

    await create_default_schedule(session, tenant)
    await create_starter_catalog(session, tenant)
    await session.flush()

    # Сразу впускаем внутрь: заставлять логиниться после регистрации — лишний шаг.
    response = RedirectResponse("/admin?welcome=1", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session_cookie(owner),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response
