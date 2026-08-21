"""Подключение второго сотрудника к заявке."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ParticipationStatus,
    Request,
    RequestEvent,
    RequestParticipant,
    Staff,
)


class ParticipationError(Exception):
    """Подключить сотрудника нельзя: понятная причина в сообщении."""


async def load_participants(
    session: AsyncSession, request_id: uuid.UUID
) -> list[RequestParticipant]:
    result = await session.scalars(
        select(RequestParticipant)
        .where(RequestParticipant.request_id == request_id)
        .order_by(RequestParticipant.created_at)
    )
    return list(result)


async def _find(
    session: AsyncSession, request_id: uuid.UUID, staff_id: uuid.UUID
) -> RequestParticipant | None:
    return await session.scalar(
        select(RequestParticipant).where(
            RequestParticipant.request_id == request_id,
            RequestParticipant.staff_id == staff_id,
        )
    )


async def ask_to_join(
    session: AsyncSession, *, request: Request, staff: Staff, note: str = ""
) -> RequestParticipant:
    """Сотрудник просится в работу к ведущему."""
    if request.tenant_id != staff.tenant_id:
        raise ParticipationError("Заявка другого нотариуса")
    if request.assigned_staff_id == staff.id:
        raise ParticipationError("Вы и так ведёте эту заявку")
    if not request.is_open:
        raise ParticipationError("Заявка закрыта")

    existing = await _find(session, request.id, staff.id)
    if existing is not None:
        if existing.status is ParticipationStatus.ACTIVE:
            raise ParticipationError("Вы уже работаете над этой заявкой")
        # Отказали или выходил — можно попроситься снова, дело могло измениться.
        existing.status = ParticipationStatus.REQUESTED
        existing.note = note.strip()[:500]
        existing.decided_at = None
        existing.decided_by_id = None
        await session.flush()
        return existing

    row = RequestParticipant(
        tenant_id=request.tenant_id,
        request_id=request.id,
        staff_id=staff.id,
        status=ParticipationStatus.REQUESTED,
        note=note.strip()[:500],
    )
    session.add(row)
    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=staff.id,
            actor_label=staff.full_name,
            comment=f"Просится в работу{': ' + note.strip() if note.strip() else ''}",
        )
    )
    await session.flush()
    return row


async def decide(
    session: AsyncSession,
    *,
    request: Request,
    participant_id: uuid.UUID,
    decided_by: Staff,
    accept: bool,
) -> RequestParticipant:
    """Ведущий или нотариус отвечает на просьбу."""
    row = await session.scalar(
        select(RequestParticipant).where(
            RequestParticipant.id == participant_id,
            RequestParticipant.request_id == request.id,
        )
    )
    if row is None:
        raise ParticipationError("Запрос не найден")

    row.status = ParticipationStatus.ACTIVE if accept else ParticipationStatus.DECLINED
    row.decided_at = datetime.now(UTC)
    row.decided_by_id = decided_by.id

    helper = await session.get(Staff, row.staff_id)
    name = helper.full_name if helper else "сотрудник"
    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=decided_by.id,
            actor_label=decided_by.full_name,
            comment=f"{'Подключил' if accept else 'Отказал'} {name}",
        )
    )
    await session.flush()
    return row


async def add_directly(
    session: AsyncSession, *, request: Request, staff_id: uuid.UUID, added_by: Staff
) -> RequestParticipant:
    """Нотариус подключает сотрудника без просьбы."""
    if not added_by.can_manage_catalog:
        raise ParticipationError("Подключать без спроса может только нотариус")

    helper = await session.get(Staff, staff_id)
    if helper is None or helper.tenant_id != request.tenant_id:
        raise ParticipationError("Сотрудник не найден")
    if helper.id == request.assigned_staff_id:
        raise ParticipationError("Этот сотрудник и так ведёт заявку")

    row = await _find(session, request.id, staff_id)
    if row is None:
        row = RequestParticipant(
            tenant_id=request.tenant_id, request_id=request.id, staff_id=staff_id
        )
        session.add(row)

    row.status = ParticipationStatus.ACTIVE
    row.decided_at = datetime.now(UTC)
    row.decided_by_id = added_by.id

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=added_by.id,
            actor_label=added_by.full_name,
            comment=f"Подключил {helper.full_name}",
        )
    )
    await session.flush()
    return row


async def remove(
    session: AsyncSession,
    *,
    request: Request,
    participant_id: uuid.UUID,
    removed_by: Staff,
) -> None:
    row = await session.scalar(
        select(RequestParticipant).where(
            RequestParticipant.id == participant_id,
            RequestParticipant.request_id == request.id,
        )
    )
    if row is None:
        raise ParticipationError("Участник не найден")

    helper = await session.get(Staff, row.staff_id)
    row.status = ParticipationStatus.LEFT
    row.decided_at = datetime.now(UTC)
    row.decided_by_id = removed_by.id

    session.add(
        RequestEvent(
            tenant_id=request.tenant_id,
            request_id=request.id,
            actor_staff_id=removed_by.id,
            actor_label=removed_by.full_name,
            comment=f"Отключил {helper.full_name if helper else 'сотрудника'}",
        )
    )
    await session.flush()
