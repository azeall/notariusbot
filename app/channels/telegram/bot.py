"""Адаптер Telegram.

Тонкий слой: принимает сообщения, рисует кнопки, вызывает app.channels.flow.
Никакой логики услуг и заявок здесь нет — она общая для всех каналов.
"""

import logging
import uuid
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import flow
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Channel, Staff, SubmissionMode

log = logging.getLogger(__name__)
dispatcher = Dispatcher()


class Talk(StatesGroup):
    choosing_service = State()
    entering_name = State()
    entering_phone = State()
    giving_consent = State()
    choosing_slot = State()


def services_keyboard(services) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=service.title, callback_data=f"svc:{service.id}")]
        for service in services
    ]
    rows.append([InlineKeyboardButton(text="Показать все услуги", callback_data="all")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(service_id: uuid.UUID) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Оформить заявку", callback_data=f"go:{service_id}")],
            [InlineKeyboardButton(text="← Другая услуга", callback_data="all")],
        ]
    )


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Согласен", callback_data="consent:yes")],
            [InlineKeyboardButton(text="Отказаться", callback_data="consent:no")],
        ]
    )


def slots_keyboard(slots: list[tuple[datetime, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"slot:{moment.isoformat()}")]
            for moment, label in slots
        ]
    )


def phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Отправить мой номер", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


async def _draft(state: FSMContext) -> flow.Draft:
    data = await state.get_data()
    raw = data.get("draft")
    if raw is None:
        return flow.Draft(tenant_slug=get_settings().default_tenant_slug)
    return flow.Draft(**raw)


async def _save(state: FSMContext, draft: flow.Draft) -> None:
    await state.update_data(draft=draft.__dict__)


async def _tenant(session: AsyncSession, draft: flow.Draft):
    tenant = await flow.resolve_tenant(session, draft.tenant_slug)
    if tenant is None:
        tenant = await flow.resolve_tenant(session, get_settings().default_tenant_slug)
    return tenant


@dispatcher.message(CommandStart(deep_link=True))
async def start_with_link(message: Message, command: CommandObject, state: FSMContext) -> None:
    """Приход по ссылке t.me/<bot>?start=<полезная нагрузка>.

    Клиент приходит с кодом нотариуса, сотрудник — с кодом привязки `link_…`.
    """
    await state.clear()
    payload = (command.args or "").strip()

    if payload.startswith("link_"):
        await _link_staff(message, payload[len("link_") :])
        return

    await _begin(message, state, payload or get_settings().default_tenant_slug)


async def _link_staff(message: Message, code: str) -> None:
    """Привязать чат сотрудника, чтобы слать ему уведомления о заявках."""
    if not code:
        await message.answer("Код привязки пустой. Попросите новую ссылку.")
        return

    async with get_sessionmaker()() as session:
        staff = await session.scalar(
            select(Staff).where(Staff.telegram_link_code == code, Staff.is_active.is_(True))
        )
        if staff is None:
            await message.answer(
                "Код не подошёл — вероятно, ссылка уже использована. "
                "Попросите владельца выдать новую."
            )
            return

        staff.telegram_chat_id = str(message.chat.id)
        staff.telegram_link_code = None  # код одноразовый
        name = staff.full_name
        await session.commit()

    await message.answer(
        f"{name}, готово. Буду присылать сюда новые заявки.\n\n"
        "Чтобы перестать их получать, попросите владельца отвязать Telegram в панели."
    )


@dispatcher.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await _begin(message, state, get_settings().default_tenant_slug)


async def _begin(message: Message, state: FSMContext, slug: str) -> None:
    async with get_sessionmaker()() as session:
        tenant = await flow.resolve_tenant(session, slug)
        if tenant is None:
            await message.answer("Нотариус не найден. Проверьте ссылку.")
            return
        services = await flow.find_services(session, tenant, "")

    await _save(state, flow.Draft(tenant_slug=slug))
    await state.set_state(Talk.choosing_service)
    await message.answer(
        f"{tenant.display_name}\n\n{flow.GREETING}",
        reply_markup=services_keyboard(services[:8]),
    )


@dispatcher.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Напишите, что нужно — например «согласие на выезд ребёнка». "
        "Я подскажу перечень документов и приму заявку.\n\n"
        "/start — начать заново"
    )


@dispatcher.callback_query(F.data == "all")
async def show_all(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        services = await flow.find_services(session, tenant, "")
    await state.set_state(Talk.choosing_service)
    await callback.message.answer(
        "Выберите услугу:", reply_markup=services_keyboard(services[:12])
    )
    await callback.answer()


@dispatcher.message(Talk.choosing_service, F.text)
async def search(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        services = await flow.find_services(session, tenant, message.text)

    if not services:
        await message.answer(flow.NOT_FOUND, reply_markup=services_keyboard([]))
        return

    await message.answer(
        "Вот что подходит. Выберите нужное:", reply_markup=services_keyboard(services)
    )


@dispatcher.callback_query(F.data.startswith("svc:"))
async def show_service(callback: CallbackQuery, state: FSMContext) -> None:
    service_id = uuid.UUID(callback.data.split(":", 1)[1])
    draft = await _draft(state)

    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        service = await flow.get_service(session, tenant, service_id)
        if service is None:
            await callback.answer("Услуга недоступна", show_alert=True)
            return
        text = flow.render_service(service)

    draft.service_id = service_id
    await _save(state, draft)
    await callback.message.answer(
        text, parse_mode="Markdown", reply_markup=confirm_keyboard(service_id)
    )
    await callback.answer()


@dispatcher.callback_query(F.data.startswith("go:"))
async def begin_request(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    draft.service_id = uuid.UUID(callback.data.split(":", 1)[1])
    await _save(state, draft)
    await state.set_state(Talk.entering_name)
    await callback.message.answer(flow.ASK_NAME)
    await callback.answer()


@dispatcher.message(Talk.entering_name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if len(name) < 2:
        await message.answer("Слишком коротко. Напишите фамилию и имя.")
        return
    draft = await _draft(state)
    draft.full_name = name[:255]
    await _save(state, draft)
    await state.set_state(Talk.entering_phone)
    await message.answer(flow.ASK_PHONE, reply_markup=phone_keyboard())


@dispatcher.message(Talk.entering_phone, F.contact)
async def got_contact(message: Message, state: FSMContext) -> None:
    await _accept_phone(message, state, message.contact.phone_number)


@dispatcher.message(Talk.entering_phone, F.text)
async def got_phone_text(message: Message, state: FSMContext) -> None:
    await _accept_phone(message, state, message.text)


async def _accept_phone(message: Message, state: FSMContext, raw: str) -> None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 10:
        await message.answer("Номер выглядит неполным. Пришлите ещё раз.")
        return

    draft = await _draft(state)
    draft.phone = raw.strip()[:32]
    await _save(state, draft)
    await state.set_state(Talk.giving_consent)
    await message.answer("Принято.", reply_markup=ReplyKeyboardRemove())
    await message.answer(flow.ASK_CONSENT, reply_markup=consent_keyboard())


@dispatcher.callback_query(F.data == "consent:no")
async def consent_declined(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(
        "Хорошо. Без согласия оформить заявку нельзя, но вы всегда можете "
        "позвонить нотариусу напрямую.\n\n/start — начать заново"
    )
    await callback.answer()


@dispatcher.callback_query(F.data == "consent:yes")
async def consent_given(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    draft.consent = True
    await _save(state, draft)

    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        service = await flow.get_service(session, tenant, draft.service_id)
        if service is None:
            await callback.answer("Услуга недоступна", show_alert=True)
            return

        if service.submission_mode is SubmissionMode.VISIT:
            slots = await flow.offered_slots(session, tenant, service)
            if not slots:
                await callback.message.answer(
                    "Свободного времени на ближайшие две недели нет. "
                    "Оставьте заявку — сотрудник свяжется и подберёт время."
                )
            else:
                await state.set_state(Talk.choosing_slot)
                await callback.message.answer(
                    "Выберите удобное время приёма:", reply_markup=slots_keyboard(slots)
                )
                await callback.answer()
                return

    await _finish(callback.message, state)
    await callback.answer()


@dispatcher.callback_query(F.data.startswith("slot:"))
async def slot_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    draft.slot = datetime.fromisoformat(callback.data.split(":", 1)[1])
    await _save(state, draft)
    await _finish(callback.message, state)
    await callback.answer()


async def _finish(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    external_id = str(message.chat.id)

    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        try:
            request, upload_url = await flow.submit(
                session,
                tenant=tenant,
                channel=Channel.TELEGRAM,
                external_id=external_id,
                draft=draft,
            )
        except flow.FlowError as exc:
            await session.rollback()
            await message.answer(str(exc))
            return
        await session.commit()
        text = flow.render_confirmation(request, upload_url, tenant.timezone)

    await state.clear()
    await message.answer(text, disable_web_page_preview=True)


@dispatcher.message(F.text)
async def fallback(message: Message, state: FSMContext) -> None:
    """Сообщение вне диалога — считаем поиском услуги."""
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        if tenant is None:
            await message.answer("Напишите /start, чтобы начать.")
            return
        services = await flow.find_services(session, tenant, message.text)

    if not services:
        await message.answer(flow.NOT_FOUND, reply_markup=services_keyboard([]))
        return
    await state.set_state(Talk.choosing_service)
    await message.answer("Вот что подходит:", reply_markup=services_keyboard(services))


def build_bot() -> Bot:
    token = get_settings().telegram_bot_token
    if not token:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN. Получите токен у @BotFather и впишите его в .env"
        )
    return Bot(token=token)


async def run() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = build_bot()
    log.info("Telegram-бот запущен")
    await dispatcher.start_polling(bot)
