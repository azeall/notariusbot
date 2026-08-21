import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain import access, participation
from app.domain.requests import claim_request, issue_upload_token, transition_request
from app.domain.security import hash_password, verify_password
from app.domain.statuses import STATUS_LABELS, ALLOWED_TRANSITIONS, TransitionError
from app.domain.storage import DocumentStorage
from app.models import (
    Attachment,
    AuditLog,
    PARTICIPATION_LABELS,
    ParticipationStatus,
    Request,
    RequestParticipant,
    RequestStatus,
    Staff,
    Tenant,
)
from app.web.deps import (
    SESSION_COOKIE,
    client_ip,
    current_staff,
    db_session,
    issue_session_cookie,
    optional_staff,
    public_base_url,
)

router = APIRouter(prefix="/staff", tags=["staff"])

# Настоящий хеш от заведомо неподходящего пароля: сверка с ним занимает столько же
# времени, сколько сверка с реальным, и не выдаёт, существует ли такой сотрудник.
_DUMMY_HASH = hash_password("bcb1f2c0-none")


def _templates():
    from app.web.main import TEMPLATES

    return TEMPLATES


@router.get("/{slug}/login", response_class=HTMLResponse)
async def login_form(
    slug: str, http_request: HttpRequest, session: AsyncSession = Depends(db_session)
):
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")
    return _templates().TemplateResponse(
        http_request, "staff_login.html", {"title": "Вход", "tenant": tenant, "error": None}
    )


@router.post("/{slug}/login")
async def login(
    slug: str,
    http_request: HttpRequest,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(db_session),
):
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == slug))
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Нотариус не найден")

    staff = await session.scalar(
        select(Staff).where(
            Staff.tenant_id == tenant.id,
            Staff.email == email.strip().lower(),
            Staff.is_active.is_(True),
        )
    )
    # Пароль проверяем всегда, даже если сотрудник не найден: иначе по времени
    # ответа можно перебрать существующие адреса.
    ok = verify_password(password, staff.password_hash if staff else _DUMMY_HASH)
    if staff is None or not ok:
        return _templates().TemplateResponse(
            http_request,
            "staff_login.html",
            {"title": "Вход", "tenant": tenant, "error": "Неверная почта или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    session.add(
        AuditLog(
            tenant_id=tenant.id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            action="login",
            object_type="staff",
            object_id=str(staff.id),
            source_ip=client_ip(http_request),
        )
    )
    response = RedirectResponse("/staff", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session_cookie(staff),
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 12,
    )
    return response


@router.post("/logout")
async def logout(
    staff: Staff | None = Depends(optional_staff),
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Выход возвращает на вход своего нотариуса, а не на корень сервиса.

    Кто именно вышел, известно только до удаления куки, поэтому код нотариуса
    выясняем заранее. Иначе сотрудник попадает на служебную страницу и не
    понимает, куда ему теперь входить.
    """
    target = "/"
    if staff is not None:
        tenant = await session.get(Tenant, staff.tenant_id)
        if tenant is not None:
            target = f"/staff/{tenant.slug}/login"

    response = RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/password", response_class=HTMLResponse)
async def password_form(
    http_request: HttpRequest, staff: Staff = Depends(current_staff)
):
    return _templates().TemplateResponse(
        http_request,
        "change_password.html",
        {
            "title": "Смена пароля",
            "who": staff.full_name or staff.email,
            "action": "/staff/password",
            "back": "/staff",
            "error": None,
            "done": False,
        },
    )


@router.post("/password")
async def change_password(
    http_request: HttpRequest,
    current_password: str = Form(...),
    new_password: str = Form(...),
    repeat_password: str = Form(...),
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    def render(error: str | None, done: bool = False):
        return _templates().TemplateResponse(
            http_request,
            "change_password.html",
            {
                "title": "Смена пароля",
                "who": staff.full_name or staff.email,
                "action": "/staff/password",
                "back": "/staff",
                "error": error,
                "done": done,
            },
            status_code=status.HTTP_400_BAD_REQUEST if error else status.HTTP_200_OK,
        )

    # Текущий пароль обязателен: сотрудник мог отойти от компьютера.
    if not verify_password(current_password, staff.password_hash):
        return render("Текущий пароль неверный.")
    if len(new_password) < 8:
        return render("Новый пароль короче 8 символов.")
    if new_password != repeat_password:
        return render("Новый пароль и повтор не совпадают.")
    if new_password == current_password:
        return render("Новый пароль совпадает со старым.")

    staff.password_hash = hash_password(new_password)
    await session.flush()
    return render(None, done=True)


@router.get("", response_class=HTMLResponse)
async def queue(
    http_request: HttpRequest,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Очередь заявок.

    Сотрудник видит ничьи, свои и — отдельным списком — чужие: без этого
    непонятно, к кому проситься в помощь. Нотариус видит то же самое, но чужие
    для него это «в работе у сотрудников», и он может открыть любую.
    """
    open_statuses = [
        RequestStatus.CLAIMED,
        RequestStatus.AWAITING_DOCUMENTS,
        RequestStatus.AWAITING_VISIT,
    ]
    common = (selectinload(Request.client), selectinload(Request.attachments),
              selectinload(Request.assigned_staff), selectinload(Request.participants))

    unclaimed = list(
        await session.scalars(
            select(Request)
            .where(
                Request.tenant_id == staff.tenant_id,
                Request.status == RequestStatus.NEW,
            )
            .options(*common)
            .order_by(Request.created_at)
        )
    )

    # Свои — это и те, что веду, и те, где помогаю.
    helping_ids = list(
        await session.scalars(
            select(RequestParticipant.request_id).where(
                RequestParticipant.staff_id == staff.id,
                RequestParticipant.status == ParticipationStatus.ACTIVE,
            )
        )
    )
    mine = list(
        await session.scalars(
            select(Request)
            .where(
                Request.tenant_id == staff.tenant_id,
                Request.status.in_(open_statuses),
                or_(
                    Request.assigned_staff_id == staff.id,
                    Request.id.in_(helping_ids) if helping_ids else false(),
                ),
            )
            .options(*common)
            .order_by(Request.claimed_at)
        )
    )

    mine_ids = {r.id for r in mine}
    others = [
        r
        for r in await session.scalars(
            select(Request)
            .where(
                Request.tenant_id == staff.tenant_id,
                Request.status.in_(open_statuses),
                Request.assigned_staff_id.is_not(None),
            )
            .options(*common)
            .order_by(Request.claimed_at)
        )
        if r.id not in mine_ids
    ]

    # Просьбы о помощи по заявкам, которые ведёт этот сотрудник.
    pending = list(
        await session.scalars(
            select(RequestParticipant)
            .join(Request, Request.id == RequestParticipant.request_id)
            .where(
                RequestParticipant.status == ParticipationStatus.REQUESTED,
                Request.assigned_staff_id == staff.id,
            )
            .options(
                selectinload(RequestParticipant.staff),
                selectinload(RequestParticipant.request),
            )
            .order_by(RequestParticipant.created_at)
        )
    )

    tenant = await session.get(Tenant, staff.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "staff_queue.html",
        {
            "title": "Заявки",
            "staff": staff,
            "tenant": tenant,
            "unclaimed": unclaimed,
            "mine": mine,
            "others": others,
            "pending": pending,
            "labels": STATUS_LABELS,
        },
    )


@router.get("/queue-count")
async def queue_count(
    staff: Staff = Depends(current_staff), session: AsyncSession = Depends(db_session)
) -> dict[str, int]:
    """Сколько ничьих заявок сейчас в очереди.

    Очередь опрашивает этот адрес и показывает баннер, когда появляется новая:
    иначе сотрудник узнаёт о заявке, только если сам обновит страницу.
    """
    count = await session.scalar(
        select(func.count(Request.id)).where(
            Request.tenant_id == staff.tenant_id,
            Request.status == RequestStatus.NEW,
        )
    )
    return {"new": int(count or 0)}


async def _load_request(
    session: AsyncSession, staff: Staff, request_id: uuid.UUID
) -> Request:
    request = await session.scalar(
        select(Request)
        .where(Request.id == request_id, Request.tenant_id == staff.tenant_id)
        .options(
            selectinload(Request.client),
            selectinload(Request.attachments),
            selectinload(Request.events),
            selectinload(Request.assigned_staff),
            selectinload(Request.participants).selectinload(RequestParticipant.staff),
        )
    )
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return request


async def _editable(
    session: AsyncSession, staff: Staff, request_id: uuid.UUID
) -> Request:
    """Заявка, которую этот сотрудник вправе менять.

    Раньше проверки не было вовсе: любой мог сменить статус чужой заявки,
    и по журналу потом не разобрать, кто что решил.
    """
    request = await _load_request(session, staff, request_id)
    if not access.evaluate(request, staff).can_edit:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Заявку ведёт другой сотрудник. Попроситесь в работу, чтобы вносить изменения.",
        )
    return request


@router.get("/requests/{request_id}", response_class=HTMLResponse)
async def request_detail(
    request_id: uuid.UUID,
    http_request: HttpRequest,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    request = await _load_request(session, staff, request_id)
    rights = access.evaluate(request, staff)
    tenant = await session.get(Tenant, staff.tenant_id)

    # Нотариусу показываем, кого ещё можно подключить.
    colleagues = []
    if rights.can_manage_participants:
        busy = {p.staff_id for p in request.participants if p.is_active}
        busy.add(request.assigned_staff_id)
        colleagues = [
            person
            for person in await session.scalars(
                select(Staff)
                .where(Staff.tenant_id == staff.tenant_id, Staff.is_active.is_(True))
                .order_by(Staff.full_name)
            )
            if person.id not in busy
        ]

    return _templates().TemplateResponse(
        http_request,
        "staff_request.html",
        {
            "title": f"Заявка № {request.public_number}",
            "staff": staff,
            "tenant": tenant,
            # Ключ "request" занят Starlette под HTTP-запрос, поэтому заявка
            # лежит под "req" — иначе шаблонный ответ падает при рендере.
            "req": request,
            "labels": STATUS_LABELS,
            "next_statuses": sorted(ALLOWED_TRANSITIONS.get(request.status, frozenset())),
            "rights": rights,
            "participants": request.participants,
            "part_labels": PARTICIPATION_LABELS,
            "colleagues": colleagues,
        },
    )


@router.post("/requests/{request_id}/claim")
async def claim(
    request_id: uuid.UUID,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    claimed = await claim_request(session, request_id=request_id, staff=staff)
    if claimed is None:
        # Заявку успел взять кто-то другой — показываем актуальное состояние.
        return RedirectResponse(
            f"/staff/requests/{request_id}?taken=1", status_code=status.HTTP_303_SEE_OTHER
        )
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/status")
async def change_status(
    request_id: uuid.UUID,
    target: str = Form(...),
    comment: str = Form(""),
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    request = await _editable(session, staff, request_id)
    try:
        await transition_request(
            session,
            request=request,
            target=RequestStatus(target),
            staff=staff,
            comment=comment,
        )
    except (TransitionError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/upload-link")
async def new_upload_link(
    request_id: uuid.UUID,
    http_request: HttpRequest,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Выдать клиенту новую одноразовую ссылку на догрузку документов."""
    request = await _editable(session, staff, request_id)
    _, token = await issue_upload_token(session, request=request)
    url = f"{public_base_url(http_request)}/upload/{token}"
    return RedirectResponse(
        f"/staff/requests/{request_id}?link={url}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/join")
async def ask_to_join(
    request_id: uuid.UUID,
    note: str = Form(""),
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Попроситься в помощь к ведущему сотруднику."""
    request = await _load_request(session, staff, request_id)
    try:
        await participation.ask_to_join(session, request=request, staff=staff, note=note)
    except participation.ParticipationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/participants/{participant_id}/decide")
async def decide_participation(
    request_id: uuid.UUID,
    participant_id: uuid.UUID,
    accept: str = Form(""),
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Ведущий или нотариус отвечает на просьбу о помощи."""
    request = await _load_request(session, staff, request_id)
    if not access.evaluate(request, staff).can_manage_participants:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Решает ведущий сотрудник или нотариус"
        )
    try:
        await participation.decide(
            session,
            request=request,
            participant_id=participant_id,
            decided_by=staff,
            accept=bool(accept),
        )
    except participation.ParticipationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/participants")
async def add_participant(
    request_id: uuid.UUID,
    staff_id: uuid.UUID = Form(...),
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Нотариус подключает сотрудника без спроса."""
    request = await _load_request(session, staff, request_id)
    try:
        await participation.add_directly(
            session, request=request, staff_id=staff_id, added_by=staff
        )
    except participation.ParticipationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/participants/{participant_id}/remove")
async def remove_participant(
    request_id: uuid.UUID,
    participant_id: uuid.UUID,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    request = await _load_request(session, staff, request_id)
    if not access.evaluate(request, staff).can_manage_participants:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Решает ведущий сотрудник или нотариус"
        )
    try:
        await participation.remove(
            session, request=request, participant_id=participant_id, removed_by=staff
        )
    except participation.ParticipationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/requests/{request_id}/documents/{attachment_id}")
async def download_document(
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    http_request: HttpRequest,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
) -> Response:
    """Выдача документа сотруднику. Каждое открытие попадает в журнал доступа."""
    attachment = await session.scalar(
        select(Attachment).where(
            Attachment.id == attachment_id,
            Attachment.request_id == request_id,
            Attachment.tenant_id == staff.tenant_id,
        )
    )
    if attachment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Документ не найден")
    if not attachment.is_available:
        raise HTTPException(status.HTTP_410_GONE, "Документ удалён по истечении срока хранения")

    payload = DocumentStorage().load(attachment.storage_path)

    session.add(
        AuditLog(
            tenant_id=staff.tenant_id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            action="document_viewed",
            object_type="attachment",
            object_id=str(attachment.id),
            source_ip=client_ip(http_request),
            details=attachment.original_filename,
        )
    )
    await session.flush()

    return Response(
        content=payload,
        media_type=attachment.content_type,
        headers={
            "Content-Disposition": f'inline; filename="{attachment.id}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
