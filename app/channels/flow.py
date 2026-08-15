"""Логика диалога, общая для всех мессенджеров.

Здесь нет ни одного вызова Telegram или MAX: адаптеры каналов только принимают
сообщения и рисуют кнопки, а что именно спросить и что ответить — решается тут.
Благодаря этому второй канал стоит примерно столько же, сколько первый.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain import catalog
from app.domain.requests import RequestError, create_request, issue_upload_token
from app.domain.schedule import SlotUnavailable, available_slots, book_slot
from app.models import Channel, Client, Request, Service, SubmissionMode, Tenant

CONSENT_VERSION = "2026-08-15"

WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def slot_label(moment: datetime) -> str:
    return (
        f"{moment.day} {MONTHS_RU[moment.month - 1]}, "
        f"{WEEKDAYS_RU[moment.weekday()]}, {moment:%H:%M}"
    )


@dataclass
class Draft:
    """Что клиент успел рассказать о себе за время разговора."""

    tenant_slug: str
    service_id: uuid.UUID | None = None
    full_name: str = ""
    phone: str = ""
    comment: str = ""
    consent: bool = False
    slot: datetime | None = None
    extra: dict = field(default_factory=dict)


# --- тексты -----------------------------------------------------------------

GREETING = (
    "Здравствуйте. Помогу подготовиться к визиту к нотариусу: подскажу перечень "
    "документов и приму заявку.\n\n"
    "Напишите своими словами, что нужно — например «доверенность на машину», — "
    "или выберите услугу из списка."
)

ASK_NAME = "Как вас зовут? Напишите фамилию и имя."

ASK_PHONE = "Оставьте номер телефона для связи."

ASK_CONSENT = (
    "Для оформления заявки нужно ваше согласие на обработку персональных данных.\n\n"
    "Данные используются только для подготовки нотариального действия и хранятся "
    "ограниченное время."
)

NOT_FOUND = (
    "Не нашёл подходящей услуги по этим словам. Попробуйте написать иначе "
    "или посмотрите полный список."
)

DISCLAIMER = (
    "\n\nЭто предварительная информация. Окончательный перечень документов "
    "подтверждает сотрудник нотариуса."
)


def render_service(service: Service) -> str:
    """Карточка услуги с перечнем документов."""
    lines = [f"*{service.title}*"]
    if service.description:
        lines.append(service.description)

    facts = []
    if service.lead_time_note:
        facts.append(f"срок: {service.lead_time_note}")
    if service.price_note:
        facts.append(f"стоимость: {service.price_note}")
    facts.append(
        "нужен личный визит"
        if service.submission_mode is SubmissionMode.VISIT
        else "документы можно прислать онлайн"
    )
    lines.append(" · ".join(facts))

    documents = sorted(service.documents, key=lambda d: d.sort_order)
    if documents:
        lines.append("\nЧто понадобится:")
        for doc in documents:
            mark = "•" if doc.is_required else "◦"
            suffix = "" if doc.is_required else " (если есть)"
            lines.append(f"{mark} {doc.title}{suffix}")
            if doc.description:
                lines.append(f"   {doc.description}")
    else:
        lines.append("\nОтдельный перечень документов не требуется.")

    return "\n".join(lines) + DISCLAIMER


def render_confirmation(request: Request, upload_url: str | None, tz: str) -> str:
    lines = [f"Заявка № {request.public_number} принята."]

    if request.preferred_time_note:
        lines.append(f"Приём: {request.preferred_time_note}")
        lines.append("Адрес и время подтвердит сотрудник.")
    elif upload_url:
        lines.append(
            "\nДокументы можно прислать прямо сейчас по защищённой ссылке:\n"
            f"{upload_url}\n\n"
            "Ссылка одноразовая и действует 30 минут. Отправлять сканы прямо "
            "в чат не нужно — так они не осядут на серверах мессенджера."
        )
    else:
        lines.append("Сотрудник свяжется с вами для подтверждения.")

    return "\n".join(lines)


# --- действия ---------------------------------------------------------------


async def resolve_tenant(session: AsyncSession, slug: str) -> Tenant | None:
    return await session.scalar(
        select(Tenant).where(Tenant.slug == slug, Tenant.is_active.is_(True))
    )


async def find_services(
    session: AsyncSession, tenant: Tenant, query: str, limit: int = 5
) -> list[Service]:
    """Подбор услуги под свободный текст. Пустой запрос — весь каталог."""
    if query.strip():
        return await catalog.search_services(session, tenant.id, query, limit=limit)
    return await catalog.list_services(session, tenant.id)


async def get_service(
    session: AsyncSession, tenant: Tenant, service_id: uuid.UUID
) -> Service | None:
    return await catalog.get_service(session, tenant.id, service_id)


async def offered_slots(
    session: AsyncSession, tenant: Tenant, service: Service, limit: int = 12
) -> list[tuple[datetime, str]]:
    tz = ZoneInfo(tenant.timezone)
    slots = await available_slots(session, tenant=tenant, service=service, days_ahead=14)
    return [(slot, slot_label(slot.astimezone(tz))) for slot in slots[:limit]]


async def upsert_client(
    session: AsyncSession,
    *,
    tenant: Tenant,
    channel: Channel,
    external_id: str,
    draft: Draft,
) -> Client:
    client = await session.scalar(
        select(Client).where(
            Client.tenant_id == tenant.id,
            Client.channel == channel,
            Client.external_id == external_id,
        )
    )
    if client is None:
        client = Client(
            tenant_id=tenant.id,
            channel=channel,
            external_id=external_id,
        )
        session.add(client)

    client.full_name = draft.full_name or client.full_name
    client.phone = draft.phone or client.phone
    if draft.consent:
        client.consent_given_at = datetime.now(UTC)
        client.consent_text_version = CONSENT_VERSION

    await session.flush()
    return client


class FlowError(Exception):
    """Диалог нельзя продолжить: понятный текст лежит в сообщении."""


async def submit(
    session: AsyncSession,
    *,
    tenant: Tenant,
    channel: Channel,
    external_id: str,
    draft: Draft,
) -> tuple[Request, str | None]:
    """Создать заявку по собранным ответам. Возвращает заявку и ссылку на загрузку."""
    if not draft.consent:
        raise FlowError("Без согласия на обработку персональных данных заявку принять нельзя.")
    if draft.service_id is None:
        raise FlowError("Сначала выберите услугу.")

    service = await get_service(session, tenant, draft.service_id)
    if service is None:
        raise FlowError("Услуга больше недоступна. Выберите другую.")

    client = await upsert_client(
        session, tenant=tenant, channel=channel, external_id=external_id, draft=draft
    )

    try:
        request = await create_request(
            session,
            tenant=tenant,
            client=client,
            service=service,
            channel=channel,
            client_comment=draft.comment,
        )
    except RequestError as exc:
        raise FlowError(str(exc)) from exc

    if service.submission_mode is SubmissionMode.VISIT:
        if draft.slot is None:
            raise FlowError("Для этой услуги нужно выбрать время визита.")
        try:
            appointment = await book_slot(
                session, request=request, service=service, starts_at=draft.slot
            )
        except SlotUnavailable as exc:
            raise FlowError("Это время только что заняли. Выберите другое.") from exc
        request.preferred_time_note = slot_label(
            appointment.starts_at.astimezone(ZoneInfo(tenant.timezone))
        )
        await session.flush()
        return request, None

    _, token = await issue_upload_token(session, request=request)
    upload_url = f"{get_settings().public_base_url}/upload/{token}"
    return request, upload_url
