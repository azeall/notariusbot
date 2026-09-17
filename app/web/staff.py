import uuid
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request as HttpRequest, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain import access, participation, password_reset
from app.domain.requests import claim_request, issue_upload_token, transition_request
from app.domain.security import hash_password, verify_password
from app.domain.statuses import STATUS_LABELS, ALLOWED_TRANSITIONS, TransitionError
from app.domain.storage import DocumentStorage
from app.models import (
    Attachment,
    AuditLog,
    Client,
    PARTICIPATION_LABELS,
    ParticipationStatus,
    Request,
    RequestEvent,
    RequestParticipant,
    RequestStatus,
    Staff,
    Tenant,
)
from app.web import sessions
from app.web.deps import (
    SESSION_COOKIE,
    client_ip,
    current_owner,
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
    remember: str = Form(""),
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
    value, ttl = issue_session_cookie(staff, remember=bool(remember))
    sessions.attach(response, SESSION_COOKIE, value, ttl)
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


def _request_timing(request: Request, now: datetime, tz: ZoneInfo) -> dict:
    end = request.claimed_at or request.closed_at or now
    minutes = max(0, int((end - request.created_at).total_seconds() // 60))
    if minutes >= 1440:
        waiting = f"{minutes // 1440} д {minutes % 1440 // 60} ч"
    elif minutes >= 60:
        waiting = f"{minutes // 60} ч {minutes % 60} мин"
    else:
        waiting = f"{minutes} мин" if minutes else "менее минуты"
    return {"received": request.created_at.astimezone(tz), "waiting": waiting}


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
    tenant = await session.get(Tenant, staff.tenant_id)
    tz = ZoneInfo(tenant.timezone)
    filters = {key: http_request.query_params.get(key, "").strip()
               for key in ("q", "status", "date_from", "date_to", "assignee")}
    conditions = [Request.tenant_id == staff.tenant_id]
    filter_error = None
    queue_statuses = [RequestStatus.NEW, *open_statuses]
    try:
        if filters["status"]:
            selected_status = RequestStatus(filters["status"])
            if selected_status not in queue_statuses:
                raise ValueError
            conditions.append(Request.status == selected_status)
        start = date.fromisoformat(filters["date_from"]) if filters["date_from"] else None
        end = date.fromisoformat(filters["date_to"]) if filters["date_to"] else None
        if start and end and start > end:
            raise ValueError
        if start:
            conditions.append(Request.created_at >= datetime.combine(start, time.min, tz))
        if end:
            conditions.append(Request.created_at <= datetime.combine(end, time.max, tz))
        if filters["assignee"] == "unassigned":
            conditions.append(Request.assigned_staff_id.is_(None))
        elif filters["assignee"]:
            conditions.append(Request.assigned_staff_id == uuid.UUID(filters["assignee"]))
    except ValueError:
        filter_error = "Проверьте фильтры: даты должны идти по порядку, статус и сотрудник — из списка."
        conditions.append(false())

    if filters["q"]:
        query = filters["q"]
        # В локали C lower/ILIKE не меняют регистр кириллицы.
        normalized_name = func.lower(func.translate(
            Client.full_name,
            "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
            "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        ))
        name_match = normalized_name.contains(query.lower(), autoescape=True)
        # Номер ищется независимо от пробелов, скобок и дефисов.
        digits = "".join(c for c in query if c.isdecimal())
        if len(digits) == 11 and digits[0] in "78":
            digits = digits[1:]
        phone_match = (func.regexp_replace(Client.phone, "[^0-9]", "", "g")
                       .contains(digits, autoescape=True)) if digits else false()
        conditions.append(Request.client.has(
            (Client.tenant_id == staff.tenant_id) & or_(name_match, phone_match)
        ))

    common = (selectinload(Request.client), selectinload(Request.attachments),
              selectinload(Request.assigned_staff), selectinload(Request.participants))

    unclaimed = list(
        await session.scalars(
            select(Request)
            .where(
                *conditions,
                Request.status == RequestStatus.NEW,
            )
            .options(*common)
            .order_by(Request.created_at)
        )
    )

    # Свои — это и те, что веду, и те, где помогаю.
    helping_ids = list(
        await session.scalars(
            select(RequestParticipant.request_id).join(Request).where(
                Request.tenant_id == staff.tenant_id,
                RequestParticipant.tenant_id == staff.tenant_id,
                RequestParticipant.staff_id == staff.id,
                RequestParticipant.status == ParticipationStatus.ACTIVE,
            )
        )
    )
    mine = list(
        await session.scalars(
            select(Request)
            .where(
                *conditions,
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
                *conditions,
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
                Request.tenant_id == staff.tenant_id,
                RequestParticipant.tenant_id == staff.tenant_id,
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

    assignees = list(await session.scalars(
        select(Staff).where(Staff.tenant_id == staff.tenant_id).order_by(Staff.full_name)
    ))
    # Баннер сравнивает всю очередь с тем же счётчиком, даже при активном поиске.
    new_count = await session.scalar(select(func.count(Request.id)).where(
        Request.tenant_id == staff.tenant_id, Request.status == RequestStatus.NEW,
    ))
    now = datetime.now(UTC)
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
            "filters": filters,
            "filters_active": any(filters.values()),
            "filter_error": filter_error,
            "queue_statuses": queue_statuses,
            "assignees": assignees,
            "new_count": new_count or 0,
            "timing": {r.id: _request_timing(r, now, tz) for r in unclaimed + mine + others},
        },
        status_code=status.HTTP_400_BAD_REQUEST if filter_error else status.HTTP_200_OK,
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
    preview = request.status == RequestStatus.NEW and request.assigned_staff_id is None

    # Нотариусу показываем, кого ещё можно подключить.
    colleagues = []
    if rights.can_manage_participants and not preview:
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
            "preview": preview,
            "timing": _request_timing(request, datetime.now(UTC), ZoneInfo(tenant.timezone)),
        },
    )


@router.get("/reset/{token}", response_class=HTMLResponse)
async def reset_form(
    token: str,
    http_request: HttpRequest,
    session: AsyncSession = Depends(db_session),
):
    """Страница смены пароля по одноразовой ссылке."""
    staff = await password_reset.resolve(session, token)
    if staff is None:
        return _templates().TemplateResponse(
            http_request,
            "reset_expired.html",
            {"title": "Ссылка недействительна", "stylesheet": "/static/notary.css"},
            status_code=status.HTTP_410_GONE,
        )
    tenant = await session.get(Tenant, staff.tenant_id)
    return _templates().TemplateResponse(
        http_request,
        "reset_password.html",
        {
            "title": "Новый пароль",
            "stylesheet": "/static/notary.css",
            "who": staff.full_name or staff.email,
            "tenant": tenant,
            "token": token,
            "error": None,
        },
    )


@router.post("/reset/{token}")
async def reset_submit(
    token: str,
    http_request: HttpRequest,
    new_password: str = Form(...),
    repeat_password: str = Form(...),
    session: AsyncSession = Depends(db_session),
):
    staff = await password_reset.resolve(session, token)
    if staff is None:
        return _templates().TemplateResponse(
            http_request,
            "reset_expired.html",
            {"title": "Ссылка недействительна", "stylesheet": "/static/notary.css"},
            status_code=status.HTTP_410_GONE,
        )

    tenant = await session.get(Tenant, staff.tenant_id)

    def again(error: str):
        return _templates().TemplateResponse(
            http_request,
            "reset_password.html",
            {
                "title": "Новый пароль",
                "stylesheet": "/static/notary.css",
                "who": staff.full_name or staff.email,
                "tenant": tenant,
                "token": token,
                "error": error,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 8:
        return again("Пароль короче 8 символов.")
    if new_password != repeat_password:
        return again("Пароли не совпадают.")

    staff.password_hash = hash_password(new_password)
    password_reset.consume(staff)

    session.add(
        AuditLog(
            tenant_id=staff.tenant_id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            action="password_reset",
            object_type="staff",
            object_id=str(staff.id),
            source_ip=client_ip(http_request),
        )
    )
    await session.flush()

    # Входим сразу: человек только что доказал владение ссылкой и задал
    # пароль — просить его ввести тот же пароль ещё раз незачем.
    response = RedirectResponse("/staff", status_code=status.HTTP_303_SEE_OTHER)
    value, ttl = issue_session_cookie(staff, remember=False)
    sessions.attach(response, SESSION_COOKIE, value, ttl)
    return response


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


@router.post("/requests/{request_id}/checklist/{index}")
async def toggle_document(
    request_id: uuid.UUID,
    index: int,
    staff: Staff = Depends(current_staff),
    session: AsyncSession = Depends(db_session),
):
    """Отметить пункт перечня полученным или снять отметку.

    Ради этого сервис и покупают: смысл в том, что клиент приходит
    подготовленным, а до сих пор проверять комплект приходилось глазами,
    и клиенту никто не говорил, чего не хватает.
    """
    request = await _editable(session, staff, request_id)

    if not 0 <= index < len(request.checklist):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Такого пункта нет")

    got = set(request.received_documents or [])
    title = request.checklist[index].get("title", f"пункт {index + 1}")
    if index in got:
        got.discard(index)
        action = "снята отметка"
    else:
        got.add(index)
        action = "отмечен полученным"

    # JSONB меняется только присваиванием нового списка: правку на месте
    # SQLAlchemy не заметит и в базу не отправит.
    request.received_documents = sorted(got)

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            comment=f"{title} — {action}",
        )
    )
    await session.flush()

    return RedirectResponse(
        f"/staff/requests/{request_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/requests/{request_id}/documents/{attachment_id}/delete")
async def delete_document(
    request_id: uuid.UUID,
    attachment_id: uuid.UUID,
    http_request: HttpRequest,
    reason: str = Form(""),
    staff: Staff = Depends(current_owner),
    session: AsyncSession = Depends(db_session),
):
    """Удалить документ до истечения срока хранения.

    Право только у нотариуса: удаление необратимо, а отвечает за данные
    перед клиентом он. Нужно это прежде всего для отзыва согласия —
    по 152-ФЗ клиент вправе потребовать удаления, и до сих пор исполнить
    такое требование было нечем.

    Файл стирается, запись о нём и журнал доступа остаются: оператор
    должен уметь показать, что удалено и по чьей воле.
    """
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
        raise HTTPException(status.HTTP_410_GONE, "Документ уже удалён")

    DocumentStorage().delete(attachment.storage_path)
    attachment.purged_at = datetime.now(UTC)

    note = reason.strip() or "без указания причины"
    session.add(
        AuditLog(
            tenant_id=staff.tenant_id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            action="document_deleted",
            object_type="attachment",
            object_id=str(attachment.id),
            source_ip=client_ip(http_request),
            details=f"{attachment.original_filename} — {note}",
        )
    )
    await session.flush()

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
