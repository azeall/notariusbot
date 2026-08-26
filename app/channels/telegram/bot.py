"""Адаптер Telegram.

Тонкий слой: принимает сообщения, рисует кнопки, вызывает app.channels.flow.
Никакой логики услуг и заявок здесь нет — она общая для всех каналов.
"""

import logging
import uuid
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
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


async def _replace(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = None,
) -> None:
    """Перерисовать сообщение на месте вместо отправки нового.

    Иначе после каждой кнопки в чате остаётся мёртвый экран с уже нажатыми
    кнопками, и к концу разговора человек листает десяток таких.
    """
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
    except TelegramBadRequest:
        # Сообщение слишком старое для правки или текст совпал с прежним.
        await callback.message.answer(text, reply_markup=markup, parse_mode=parse_mode)


async def _prompt(
    message: Message,
    state: FSMContext,
    text: str,
    markup: InlineKeyboardMarkup | ReplyKeyboardMarkup | None = None,
) -> None:
    """Задать очередной вопрос, убрав предыдущий: в чате остаётся один активный."""
    data = await state.get_data()
    previous = data.get("prompt_id")
    if previous:
        try:
            await message.bot.delete_message(message.chat.id, previous)
        except TelegramBadRequest:
            pass  # уже удалено или прошло больше 48 часов

    sent = await message.answer(text, reply_markup=markup)
    await state.update_data(prompt_id=sent.message_id)


async def _drop_reply_keyboard(message: Message) -> None:
    """Убрать нижнюю клавиатуру, не оставляя ради этого сообщения в чате."""
    try:
        temp = await message.answer("…", reply_markup=ReplyKeyboardRemove())
        await message.bot.delete_message(message.chat.id, temp.message_id)
    except TelegramBadRequest:
        pass


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
    await _prompt(
        message,
        state,
        f"{tenant.display_name}\n\n{flow.GREETING}",
        services_keyboard(services[:8]),
    )


@dispatcher.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "Напишите, что нужно — например «согласие на выезд ребёнка». "
        "Я подскажу перечень документов и приму заявку.\n\n"
        "/my — мои заявки\n"
        "/start — начать заново"
    )


@dispatcher.message(Command("my"))
async def my_requests_command(message: Message, state: FSMContext) -> None:
    """Статус заявок. Без этого человек звонит нотариусу, чтобы просто спросить."""
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        if tenant is None:
            await message.answer("Напишите /start, чтобы начать.")
            return
        requests = await flow.my_requests(
            session,
            tenant=tenant,
            channel=Channel.TELEGRAM,
            external_id=str(message.chat.id),
        )
    await message.answer(flow.render_my_requests(requests))


@dispatcher.callback_query(F.data == "all")
async def show_all(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        services = await flow.find_services(session, tenant, "")
    await state.set_state(Talk.choosing_service)
    await _replace(callback, "Выберите услугу:", services_keyboard(services[:12]))
    await state.update_data(prompt_id=callback.message.message_id)
    await callback.answer()


@dispatcher.message(Talk.choosing_service, F.text)
async def search(message: Message, state: FSMContext) -> None:
    draft = await _draft(state)
    async with get_sessionmaker()() as session:
        tenant = await _tenant(session, draft)
        services = await flow.find_services(session, tenant, message.text)

    if not services:
        await _prompt(message, state, flow.NOT_FOUND, services_keyboard([]))
        return

    await _prompt(
        message, state, "Вот что подходит. Выберите нужное:", services_keyboard(services)
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
    await _replace(callback, text, confirm_keyboard(service_id), parse_mode="Markdown")
    await state.update_data(prompt_id=callback.message.message_id)
    await callback.answer()


@dispatcher.callback_query(F.data.startswith("go:"))
async def begin_request(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    draft.service_id = uuid.UUID(callback.data.split(":", 1)[1])
    await _save(state, draft)
    await state.set_state(Talk.entering_name)
    await _replace(callback, flow.ASK_NAME)
    await state.update_data(prompt_id=callback.message.message_id)
    await callback.answer()


@dispatcher.message(Talk.entering_name, F.text)
async def got_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    if len(name) < 2:
        await _prompt(message, state, "Слишком коротко. Напишите фамилию и имя.")
        return
    draft = await _draft(state)
    draft.full_name = name[:255]
    await _save(state, draft)
    await state.set_state(Talk.entering_phone)
    await _prompt(message, state, flow.ASK_PHONE, phone_keyboard())


@dispatcher.message(Talk.entering_phone, F.contact)
async def got_contact(message: Message, state: FSMContext) -> None:
    await _accept_phone(message, state, message.contact.phone_number)


@dispatcher.message(Talk.entering_phone, F.text)
async def got_phone_text(message: Message, state: FSMContext) -> None:
    await _accept_phone(message, state, message.text)


async def _accept_phone(message: Message, state: FSMContext, raw: str) -> None:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 10:
        await _prompt(message, state, "Номер выглядит неполным. Пришлите ещё раз.", phone_keyboard())
        return

    draft = await _draft(state)
    draft.phone = raw.strip()[:32]
    await _save(state, draft)
    await state.set_state(Talk.giving_consent)
    await _drop_reply_keyboard(message)
    await _prompt(message, state, flow.ask_consent(draft.tenant_slug), consent_keyboard())


@dispatcher.callback_query(F.data == "consent:no")
async def consent_declined(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _replace(
        callback,
        "Хорошо. Без согласия оформить заявку нельзя, но вы всегда можете "
        "позвонить нотариусу напрямую.\n\n/start — начать заново",
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
            if slots:
                await state.set_state(Talk.choosing_slot)
                await _replace(
                    callback, "Выберите удобное время приёма:", slots_keyboard(slots)
                )
                await state.update_data(prompt_id=callback.message.message_id)
                await callback.answer()
                return

    await _finish(callback, state)
    await callback.answer()


@dispatcher.callback_query(F.data.startswith("slot:"))
async def slot_chosen(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    draft.slot = datetime.fromisoformat(callback.data.split(":", 1)[1])
    await _save(state, draft)
    await _finish(callback, state)
    await callback.answer()


async def _finish(callback: CallbackQuery, state: FSMContext) -> None:
    draft = await _draft(state)
    external_id = str(callback.message.chat.id)

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
            await _replace(callback, str(exc))
            return
        await session.commit()
        text = flow.render_confirmation(request, upload_url, tenant.timezone)

    await state.clear()
    # Итог остаётся в чате единственным сообщением разговора — в нём ссылка
    # на загрузку и номер заявки, они понадобятся клиенту позже.
    await _replace(callback, text)


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
        await _prompt(message, state, flow.NOT_FOUND, services_keyboard([]))
        return
    await state.set_state(Talk.choosing_service)
    await _prompt(message, state, "Вот что подходит:", services_keyboard(services))


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
