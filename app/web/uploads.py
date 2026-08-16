from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request as HttpRequest,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.filenames import check_filename
from app.domain.requests import resolve_upload_token, transition_request
from app.domain.storage import DocumentStorage
from app.models import Attachment, AuditLog, Request, RequestStatus
from app.web.deps import client_ip, db_session

router = APIRouter(tags=["uploads"])


@router.get("/upload/{token}", response_class=HTMLResponse)
async def upload_page(
    token: str, http_request: HttpRequest, session: AsyncSession = Depends(db_session)
):
    from app.web.main import TEMPLATES

    record = await resolve_upload_token(session, token)
    if record is None:
        return TEMPLATES.TemplateResponse(
            http_request,
            "upload_expired.html",
            {"title": "Ссылка недействительна"},
            status_code=status.HTTP_410_GONE,
        )

    request = await session.get(Request, record.request_id)
    return TEMPLATES.TemplateResponse(
        http_request,
        "upload.html",
        {
            "title": "Загрузка документов",
            "token": token,
            # "request" в контексте занят Starlette под HTTP-запрос.
            "req": request,
            "checklist": request.checklist if request else [],
            "max_mb": get_settings().max_upload_bytes // (1024 * 1024),
        },
    )


@router.post("/upload/{token}")
async def upload_documents(
    token: str,
    http_request: HttpRequest,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(db_session),
) -> dict[str, object]:
    settings = get_settings()

    record = await resolve_upload_token(session, token)
    if record is None:
        raise HTTPException(status.HTTP_410_GONE, "Ссылка истекла или уже использована")

    request = await session.get(Request, record.request_id)
    if request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")

    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Не выбрано ни одного файла")

    already = await session.scalar(
        select(func.count(Attachment.id)).where(Attachment.request_id == request.id)
    )
    if int(already or 0) + len(files) > settings.max_files_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"К одной заявке можно приложить не больше "
            f"{settings.max_files_per_request} файлов.",
        )

    storage = DocumentStorage()
    saved: list[str] = []

    for upload in files:
        payload = await upload.read()
        if len(payload) == 0:
            continue

        naming_error = check_filename(upload.filename or "")
        if naming_error:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, naming_error)
        if len(payload) > settings.max_upload_bytes:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"Файл «{upload.filename}» больше {settings.max_upload_bytes // 1048576} МБ",
            )
        if upload.content_type not in settings.allowed_upload_types:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Формат «{upload.content_type}» не принимается. Нужен PDF или фотография.",
            )

        relative = storage.save(request.tenant_id, request.id, payload)
        session.add(
            Attachment(
                tenant_id=request.tenant_id,
                request_id=request.id,
                original_filename=upload.filename or "документ",
                content_type=upload.content_type or "application/octet-stream",
                size_bytes=len(payload),
                storage_path=relative,
            )
        )
        saved.append(upload.filename or "документ")

    if not saved:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Файлы оказались пустыми")

    # Ссылку не закрываем: человек вспоминает про забытый документ уже после
    # отправки, и просить у сотрудника новую ссылку каждый раз — лишняя работа
    # для обеих сторон. Ограничивают срок жизни и предел вложений.

    if request.status is RequestStatus.AWAITING_DOCUMENTS:
        await transition_request(
            session,
            request=request,
            target=RequestStatus.CLAIMED,
            comment=f"Клиент загрузил документы: {', '.join(saved)}",
        )

    session.add(
        AuditLog(
            tenant_id=request.tenant_id,
            actor_label="Клиент",
            action="documents_uploaded",
            object_type="request",
            object_id=str(request.id),
            source_ip=client_ip(http_request),
            details=", ".join(saved),
        )
    )
    await session.flush()

    total = int(already or 0) + len(saved)
    return {
        "ok": True,
        "saved": saved,
        "total": total,
        "remaining": max(settings.max_files_per_request - total, 0),
        "request_number": request.public_number,
    }


@router.post("/upload/{token}/finish")
async def finish_upload(
    token: str, session: AsyncSession = Depends(db_session)
) -> dict[str, bool]:
    """Клиент сказал, что прислал всё. Дальше ссылка не работает."""
    record = await resolve_upload_token(session, token)
    if record is None:
        raise HTTPException(status.HTTP_410_GONE, "Ссылка уже закрыта или истекла")

    record.used_at = datetime.now(UTC)
    await session.flush()
    return {"ok": True}
