"""Напоминания клиенту о записи на приём.

Неявка — главная потеря нотариуса: окно занято, никто не пришёл, и заново
его уже не продать. Два напоминания — накануне и в день приёма — закрывают
большую часть таких случаев.

Запуск по расписанию, раз в 15–30 минут:  python -m app.reminders

Каждое напоминание помечается в самой записи, поэтому повторный запуск
ничего не дублирует.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.channels.flow import slot_label
from app.db import dispose_engine, get_sessionmaker
from app.models import Appointment, Client, Request, Tenant
from app.notifications import send_to_channel

log = logging.getLogger(__name__)

# За сколько предупреждаем. Накануне — чтобы человек успел собрать документы,
# в день приёма — чтобы просто не забыл.
DAY_BEFORE = timedelta(hours=24)
SAME_DAY = timedelta(hours=3)


def render_reminder(
    tenant: Tenant, request: Request, when_local: str, day_before: bool
) -> str:
    lines = [
        "Напоминаем о записи к нотариусу." if day_before else "Ваш приём сегодня.",
        "",
        f"{tenant.display_name}",
        f"{request.service_title}",
        f"Время: {when_local}",
    ]
    if tenant.address:
        lines.append(f"Адрес: {tenant.address}")

    required = [item["title"] for item in request.checklist if item.get("is_required")]
    if required:
        lines.append("")
        lines.append("Возьмите с собой:")
        lines.extend(f"• {title}" for title in required)

    if tenant.phone:
        lines.append("")
        lines.append(f"Если планы поменялись, позвоните: {tenant.phone}")
    return "\n".join(lines)


async def _due(
    session: AsyncSession, *, within: timedelta, column
) -> list[Appointment]:
    """Записи, до которых осталось меньше указанного срока и кому ещё не писали."""
    now = datetime.now(UTC)
    return list(
        await session.scalars(
            select(Appointment)
            .where(
                Appointment.is_cancelled.is_(False),
                Appointment.starts_at > now,
                Appointment.starts_at <= now + within,
                column.is_(None),
            )
            .options(selectinload(Appointment.request))
            .order_by(Appointment.starts_at)
        )
    )


async def send_due_reminders(
    dry_run: bool = False,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    """Разослать напоминания. Возвращает (накануне, в день приёма).

    Фабрику сессий можно передать снаружи — так рассылку удаётся проверить
    тестами, не полагаясь на глобальный движок приложения.
    """
    sent_day_before = 0
    sent_same_day = 0

    maker = session_factory or get_sessionmaker()
    async with maker() as session:
        batches = (
            (DAY_BEFORE, Appointment.reminded_day_before_at, True),
            (SAME_DAY, Appointment.reminded_same_day_at, False),
        )

        for within, column, is_day_before in batches:
            for appointment in await _due(session, within=within, column=column):
                request = appointment.request
                if request is None:
                    continue

                tenant = await session.get(Tenant, appointment.tenant_id)
                client = await session.get(Client, request.client_id)
                if tenant is None or client is None:
                    continue

                when_local = slot_label(
                    appointment.starts_at.astimezone(ZoneInfo(tenant.timezone))
                )
                text = render_reminder(tenant, request, when_local, is_day_before)

                if dry_run:
                    delivered = True
                else:
                    delivered = await send_to_channel(client, text)

                # Отметку ставим в любом случае: клиент из виджета не имеет
                # канала для сообщений, и пытаться писать ему каждые полчаса
                # бессмысленно — сотрудник позвонит.
                stamp = datetime.now(UTC)
                if is_day_before:
                    appointment.reminded_day_before_at = stamp
                    sent_day_before += int(delivered)
                else:
                    appointment.reminded_same_day_at = stamp
                    sent_same_day += int(delivered)

        if not dry_run:
            await session.commit()

    return sent_day_before, sent_same_day


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    day_before, same_day = await send_due_reminders()
    print(f"Напоминаний отправлено: накануне {day_before}, в день приёма {same_day}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
