"""Адаптер MAX.

Разговор ведёт та же app.channels.flow, что и Telegram: отличаются только
транспорт (MaxClient) и хранение состояния — здесь оно в памяти процесса,
без aiogram FSM.

Готов к запуску, как только в MaxClient сверены эндпоинты и задан MAX_BOT_TOKEN.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.channels import flow
from app.channels.max.client import MaxClient, MaxUpdate
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Channel, SubmissionMode

log = logging.getLogger(__name__)

CHOOSING_SERVICE = "choosing_service"
ENTERING_NAME = "entering_name"
ENTERING_PHONE = "entering_phone"
GIVING_CONSENT = "giving_consent"
CHOOSING_SLOT = "choosing_slot"


@dataclass
class Session:
    step: str = CHOOSING_SERVICE
    draft: flow.Draft = field(
        default_factory=lambda: flow.Draft(tenant_slug=get_settings().default_tenant_slug)
    )


class MaxBot:
    """Состояние диалогов держим в памяти.

    Для одного процесса этого достаточно: разговор короткий, а при перезапуске
    клиент просто начнёт заново. Когда процессов станет больше одного,
    состояние переедет в Redis — интерфейс не поменяется.
    """

    def __init__(self, client: MaxClient | None = None) -> None:
        self.client = client or MaxClient()
        self.sessions: dict[str, Session] = {}

    def session(self, chat_id: str) -> Session:
        if chat_id not in self.sessions:
            self.sessions[chat_id] = Session()
        return self.sessions[chat_id]

    async def handle(self, update: MaxUpdate) -> None:
        chat = update.chat_id
        session = self.session(chat)

        if update.text.strip() in {"/start", "начать", "Начать"}:
            self.sessions[chat] = Session()
            await self._greet(chat)
            return

        if update.is_callback:
            await self._on_button(chat, session, update.payload)
            return

        await self._on_text(chat, session, update.text)

    # --- шаги -------------------------------------------------------------

    async def _greet(self, chat: str) -> None:
        session = self.session(chat)
        async with get_sessionmaker()() as db:
            tenant = await flow.resolve_tenant(db, session.draft.tenant_slug)
            if tenant is None:
                await self.client.send(chat, "Нотариус не найден.")
                return
            services = await flow.find_services(db, tenant, "")

        await self.client.send(
            chat,
            f"{tenant.display_name}\n\n{flow.GREETING}",
            buttons=[[(s.title, f"svc:{s.id}")] for s in services[:8]],
        )

    async def _on_text(self, chat: str, session: Session, text: str) -> None:
        if session.step == ENTERING_NAME:
            name = " ".join(text.split())
            if len(name) < 2:
                await self.client.send(chat, "Слишком коротко. Напишите фамилию и имя.")
                return
            session.draft.full_name = name[:255]
            session.step = ENTERING_PHONE
            await self.client.send(chat, flow.ASK_PHONE)
            return

        if session.step == ENTERING_PHONE:
            digits = "".join(ch for ch in text if ch.isdigit())
            if len(digits) < 10:
                await self.client.send(chat, "Номер выглядит неполным. Пришлите ещё раз.")
                return
            session.draft.phone = text.strip()[:32]
            session.step = GIVING_CONSENT
            await self.client.send(
                chat,
                flow.ASK_CONSENT,
                buttons=[[("Согласен", "consent:yes")], [("Отказаться", "consent:no")]],
            )
            return

        # По умолчанию считаем текст поиском услуги.
        async with get_sessionmaker()() as db:
            tenant = await flow.resolve_tenant(db, session.draft.tenant_slug)
            services = await flow.find_services(db, tenant, text)

        if not services:
            await self.client.send(chat, flow.NOT_FOUND)
            return

        session.step = CHOOSING_SERVICE
        await self.client.send(
            chat,
            "Вот что подходит:",
            buttons=[[(s.title, f"svc:{s.id}")] for s in services],
        )

    async def _on_button(self, chat: str, session: Session, payload: str) -> None:
        action, _, value = payload.partition(":")

        if action == "svc":
            await self._show_service(chat, session, uuid.UUID(value))
        elif action == "go":
            session.draft.service_id = uuid.UUID(value)
            session.step = ENTERING_NAME
            await self.client.send(chat, flow.ASK_NAME)
        elif action == "consent" and value == "no":
            self.sessions[chat] = Session()
            await self.client.send(
                chat, "Хорошо. Без согласия оформить заявку нельзя. Напишите «начать» заново."
            )
        elif action == "consent" and value == "yes":
            session.draft.consent = True
            await self._after_consent(chat, session)
        elif action == "slot":
            session.draft.slot = datetime.fromisoformat(value)
            await self._finish(chat, session)

    async def _show_service(self, chat: str, session: Session, service_id: uuid.UUID) -> None:
        async with get_sessionmaker()() as db:
            tenant = await flow.resolve_tenant(db, session.draft.tenant_slug)
            service = await flow.get_service(db, tenant, service_id)
            if service is None:
                await self.client.send(chat, "Услуга недоступна.")
                return
            text = flow.render_service(service)

        session.draft.service_id = service_id
        await self.client.send(
            chat, text, buttons=[[("Оформить заявку", f"go:{service_id}")]]
        )

    async def _after_consent(self, chat: str, session: Session) -> None:
        async with get_sessionmaker()() as db:
            tenant = await flow.resolve_tenant(db, session.draft.tenant_slug)
            service = await flow.get_service(db, tenant, session.draft.service_id)
            if service is None:
                await self.client.send(chat, "Услуга недоступна.")
                return

            if service.submission_mode is SubmissionMode.VISIT:
                slots = await flow.offered_slots(db, tenant, service)
                if slots:
                    session.step = CHOOSING_SLOT
                    await self.client.send(
                        chat,
                        "Выберите удобное время приёма:",
                        buttons=[[(label, f"slot:{m.isoformat()}")] for m, label in slots],
                    )
                    return

        await self._finish(chat, session)

    async def _finish(self, chat: str, session: Session) -> None:
        async with get_sessionmaker()() as db:
            tenant = await flow.resolve_tenant(db, session.draft.tenant_slug)
            try:
                request, upload_url = await flow.submit(
                    db,
                    tenant=tenant,
                    channel=Channel.MAX,
                    external_id=chat,
                    draft=session.draft,
                )
            except flow.FlowError as exc:
                await db.rollback()
                await self.client.send(chat, str(exc))
                return
            await db.commit()
            text = flow.render_confirmation(request, upload_url, tenant.timezone)

        self.sessions[chat] = Session()
        await self.client.send(chat, text)

    # --- цикл -------------------------------------------------------------

    async def run(self) -> None:
        marker: int | None = None
        log.info("MAX-бот запущен")
        while True:
            try:
                updates, marker = await self.client.updates(marker=marker)
            except Exception:
                log.exception("Не удалось получить события MAX, повтор через 5 с")
                await asyncio.sleep(5)
                continue

            for update in updates:
                try:
                    await self.handle(update)
                except Exception:
                    log.exception("Ошибка обработки события MAX")


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    if not get_settings().max_bot_token:
        raise RuntimeError("Не задан MAX_BOT_TOKEN")
    bot = MaxBot()
    try:
        await bot.run()
    finally:
        await bot.client.close()
