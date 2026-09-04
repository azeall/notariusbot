"""Уведомления сотрудникам о новых заявках.

Пока канал один — Telegram, но вызывающий код об этом не знает: он просто
сообщает «появилась заявка». Добавится почта или MAX — поменяется только этот файл.

Ни одна ошибка отправки не должна ронять создание заявки: клиент уже нажал
кнопку, и заявка важнее уведомления.
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Channel, Client, Request, Staff, SubmissionMode

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def render_new_request(request: Request, client_name: str, client_phone: str) -> str:
    lines = [
        f"Новая заявка № {request.public_number}",
        request.service_title,
        "",
        f"{client_name} · {client_phone}",
    ]
    if request.submission_mode is SubmissionMode.VISIT and request.preferred_time_note:
        lines.append(f"Приём: {request.preferred_time_note}")
    elif request.submission_mode is SubmissionMode.DOCUMENTS:
        lines.append("Клиент присылает документы онлайн")
    if request.client_comment:
        lines.append(f"Комментарий: {request.client_comment}")
    return "\n".join(lines)


async def _send(chat_id: str, text: str) -> bool:
    token = get_settings().telegram_bot_token
    if not token:
        return False
    try:
        # trust_env=False по той же причине, что и в клиенте MAX: прокси
        # машины не должен молча оказаться на пути уведомлений с данными заявки.
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            response = await client.post(
                f"{TELEGRAM_API}/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            )
        if response.status_code != 200:
            log.warning("Telegram отклонил уведомление: %s", response.text[:200])
            return False
        return True
    except Exception:
        log.exception("Не удалось отправить уведомление в Telegram")
        return False


async def send_to_channel(client: Client, text: str) -> bool:
    """Написать клиенту в тот канал, из которого он пришёл.

    Из виджета человек приходит без чата, поэтому отправить ему нечего —
    это не ошибка, просто с ним свяжется сотрудник.
    """
    if client.channel is Channel.TELEGRAM and client.external_id:
        return await _send(client.external_id, text)
    # MAX подключается здесь же, когда канал заработает.
    return False


async def notify_new_request(
    session: AsyncSession, *, request: Request, client_name: str, client_phone: str
) -> int:
    """Разослать уведомление подключённым сотрудникам. Возвращает число доставленных."""
    if not get_settings().telegram_bot_token:
        return 0

    recipients = list(
        await session.scalars(
            select(Staff).where(
                Staff.tenant_id == request.tenant_id,
                Staff.is_active.is_(True),
                Staff.notify_new_requests.is_(True),
                Staff.telegram_chat_id.is_not(None),
            )
        )
    )
    if not recipients:
        return 0

    text = render_new_request(request, client_name, client_phone)
    delivered = 0
    for staff in recipients:
        if await _send(staff.telegram_chat_id, text):
            delivered += 1
    return delivered
