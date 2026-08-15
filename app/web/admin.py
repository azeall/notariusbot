import uuid
from datetime import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.security import hash_password
from app.models import (
    Service,
    ServiceDocument,
    Staff,
    StaffRole,
    SubmissionMode,
    Tenant,
    WorkingHours,
)
from app.web.deps import current_owner, db_session

router = APIRouter(prefix="/admin", tags=["admin"])

WEEKDAY_NAMES = [
    "Понедельник",
    "Вторник",
    "Среда",
    "Четверг",
    "Пятница",
    "Суббота",
    "Воскресенье",
]


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


def parse_documents(raw: str) -> list[tuple[str, str, bool]]:
    """Разбор перечня документов из текстового поля.

    Одна строка — один документ. Строка, начинающаяся с «?», помечает документ
    необязательным. Пояснение отделяется знаком «—».

        Паспорт доверителя — все страницы
        ? Согласие супруга

    Так нотариус правит перечень как обычный список, не воюя с динамической формой.
    """
    items: list[tuple[str, str, bool]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        required = True
        if line.startswith("?"):
            required = False
            line = line[1:].strip()
        title, _, description = line.partition("—")
        title = title.strip()
        if not title:
            continue
        items.append((title[:255], description.strip()[:2000], required))
    return items


def format_documents(documents: list[ServiceDocument]) -> str:
    lines = []
    for doc in sorted(documents, key=lambda d: d.sort_order):
        prefix = "" if doc.is_required else "? "
        suffix = f" — {doc.description}" if doc.description else ""
        lines.append(f"{prefix}{doc.title}{suffix}")
    return "\n".join(lines)


@router.get("", response_class=HTMLResponse)
async def services_index(
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    services = list(
        await session.scalars(
            select(Service)
            .where(Service.tenant_id == owner.tenant_id)
            .options(selectinload(Service.documents))
            .order_by(Service.sort_order, Service.title)
        )
    )
    tenant = await session.get(Tenant, owner.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "admin_services.html",
        {"title": "Услуги", "staff": owner, "tenant": tenant, "services": services},
    )


@router.get("/services/new", response_class=HTMLResponse)
async def new_service_form(
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    tenant = await session.get(Tenant, owner.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "admin_service_form.html",
        {
            "title": "Новая услуга",
            "staff": owner,
            "tenant": tenant,
            "service": None,
            "documents_text": "",
        },
    )


@router.get("/services/{service_id}", response_class=HTMLResponse)
async def edit_service_form(
    service_id: uuid.UUID,
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    service = await session.scalar(
        select(Service)
        .where(Service.id == service_id, Service.tenant_id == owner.tenant_id)
        .options(selectinload(Service.documents))
    )
    if service is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Услуга не найдена")
    tenant = await session.get(Tenant, owner.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "admin_service_form.html",
        {
            "title": service.title,
            "staff": owner,
            "tenant": tenant,
            "service": service,
            "documents_text": format_documents(service.documents),
        },
    )


@router.post("/services")
async def save_service(
    service_id: str = Form(""),
    title: str = Form(...),
    slug: str = Form(...),
    description: str = Form(""),
    submission_mode: str = Form(SubmissionMode.DOCUMENTS.value),
    visit_duration_minutes: int = Form(30),
    lead_time_note: str = Form(""),
    price_note: str = Form(""),
    keywords: str = Form(""),
    sort_order: int = Form(0),
    is_active: str = Form(""),
    documents: str = Form(""),
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    if service_id:
        service = await session.scalar(
            select(Service).where(
                Service.id == uuid.UUID(service_id), Service.tenant_id == owner.tenant_id
            )
        )
        if service is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Услуга не найдена")
    else:
        service = Service(tenant_id=owner.tenant_id)
        session.add(service)

    service.title = title.strip()
    service.slug = slug.strip() or title.strip().lower().replace(" ", "-")[:128]
    service.description = description.strip()
    service.submission_mode = SubmissionMode(submission_mode)
    service.visit_duration_minutes = max(5, visit_duration_minutes)
    service.lead_time_note = lead_time_note.strip()
    service.price_note = price_note.strip()
    service.keywords = [w.strip() for w in keywords.split(",") if w.strip()]
    service.sort_order = sort_order
    service.is_active = bool(is_active)
    await session.flush()

    # Перечень переписываем целиком: у уже созданных заявок лежит собственный
    # слепок, поэтому правки задним числом им ничем не грозят.
    await session.execute(
        delete(ServiceDocument).where(ServiceDocument.service_id == service.id)
    )
    for index, (doc_title, doc_description, required) in enumerate(parse_documents(documents)):
        session.add(
            ServiceDocument(
                tenant_id=owner.tenant_id,
                service_id=service.id,
                title=doc_title,
                description=doc_description,
                is_required=required,
                sort_order=index,
            )
        )

    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/services/{service_id}/delete")
async def delete_service(
    service_id: uuid.UUID,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    service = await session.scalar(
        select(Service).where(
            Service.id == service_id, Service.tenant_id == owner.tenant_id
        )
    )
    if service is not None:
        await session.delete(service)
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_form(
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    rows = {
        wh.weekday: wh
        for wh in await session.scalars(
            select(WorkingHours).where(WorkingHours.tenant_id == owner.tenant_id)
        )
    }
    tenant = await session.get(Tenant, owner.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "admin_schedule.html",
        {
            "title": "Расписание",
            "staff": owner,
            "tenant": tenant,
            "rows": rows,
            "weekday_names": WEEKDAY_NAMES,
        },
    )


@router.post("/schedule")
async def save_schedule(
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    form = await http_request.form()

    def parse_time(value: str, fallback: time) -> time:
        try:
            hours, minutes = value.split(":")
            return time(int(hours), int(minutes))
        except (ValueError, AttributeError):
            return fallback

    existing = {
        wh.weekday: wh
        for wh in await session.scalars(
            select(WorkingHours).where(WorkingHours.tenant_id == owner.tenant_id)
        )
    }

    for weekday in range(7):
        row = existing.get(weekday)
        if row is None:
            row = WorkingHours(tenant_id=owner.tenant_id, weekday=weekday)
            session.add(row)
        row.is_working = bool(form.get(f"working_{weekday}"))
        row.opens_at = parse_time(str(form.get(f"opens_{weekday}", "")), time(9, 0))
        row.closes_at = parse_time(str(form.get(f"closes_{weekday}", "")), time(18, 0))
        break_start = str(form.get(f"break_start_{weekday}", "")).strip()
        break_end = str(form.get(f"break_end_{weekday}", "")).strip()
        row.break_starts_at = parse_time(break_start, time(13, 0)) if break_start else None
        row.break_ends_at = parse_time(break_end, time(14, 0)) if break_end else None

    return RedirectResponse("/admin/schedule", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/staff", response_class=HTMLResponse)
async def staff_index(
    http_request: HttpRequest,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    people = list(
        await session.scalars(
            select(Staff).where(Staff.tenant_id == owner.tenant_id).order_by(Staff.full_name)
        )
    )
    tenant = await session.get(Tenant, owner.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "admin_staff.html",
        {"title": "Сотрудники", "staff": owner, "tenant": tenant, "people": people},
    )


@router.post("/staff")
async def add_staff(
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(StaffRole.EMPLOYEE.value),
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    if len(password) < 8:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Пароль короче 8 символов")

    session.add(
        Staff(
            tenant_id=owner.tenant_id,
            full_name=full_name.strip(),
            email=email.strip().lower(),
            password_hash=hash_password(password),
            role=StaffRole(role),
        )
    )
    return RedirectResponse("/admin/staff", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/staff/{staff_id}/toggle")
async def toggle_staff(
    staff_id: uuid.UUID,
    owner: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    person = await session.scalar(
        select(Staff).where(Staff.id == staff_id, Staff.tenant_id == owner.tenant_id)
    )
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Сотрудник не найден")
    if person.id == owner.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Нельзя отключить самого себя")
    person.is_active = not person.is_active
    return RedirectResponse("/admin/staff", status_code=status.HTTP_303_SEE_OTHER)
