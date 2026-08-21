"""Кто что может делать с заявкой.

Раньше правил не было вовсе: любой сотрудник мог сменить статус чужой заявки.
В конторе с тремя помощниками это означает, что двое незаметно правят одно
и то же дело, а по журналу потом не разобрать, кто что решил.

Теперь так: ведёт заявку один человек, остальные видят её, но не трогают.
Понадобилась помощь — просятся в работу, ведущий соглашается. Нотариус
не спрашивает никого: контора его.
"""

from dataclasses import dataclass

from app.models import ParticipationStatus, Request, Staff


@dataclass(frozen=True)
class Access:
    """Что этот сотрудник может с этой заявкой."""

    can_view: bool
    can_edit: bool
    can_manage_participants: bool
    is_lead: bool
    is_participant: bool
    has_pending_request: bool

    @property
    def can_ask_to_join(self) -> bool:
        """Просить смысла нет, если и так можно работать или уже попросил."""
        return self.can_view and not self.can_edit and not self.has_pending_request


def evaluate(request: Request, staff: Staff, participants: list | None = None) -> Access:
    """Разобрать права. participants — уже загруженные записи об участии."""
    if request.tenant_id != staff.tenant_id:
        return Access(False, False, False, False, False, False)

    rows = participants if participants is not None else list(request.participants)
    mine = next((p for p in rows if p.staff_id == staff.id), None)

    is_lead = request.assigned_staff_id == staff.id
    is_participant = mine is not None and mine.status is ParticipationStatus.ACTIVE
    has_pending = mine is not None and mine.status is ParticipationStatus.REQUESTED

    # Нотариус видит и делает всё: он отвечает за нотариальное действие,
    # а сотрудники — его помощники.
    if staff.can_manage_catalog:
        return Access(
            can_view=True,
            can_edit=True,
            can_manage_participants=True,
            is_lead=is_lead,
            is_participant=is_participant,
            has_pending_request=has_pending,
        )

    # Ничья заявка: взять может любой, поэтому она открыта на изменение —
    # иначе кнопка «Взять» не сработает.
    unclaimed = request.assigned_staff_id is None

    return Access(
        can_view=True,
        can_edit=is_lead or is_participant or unclaimed,
        can_manage_participants=is_lead,
        is_lead=is_lead,
        is_participant=is_participant,
        has_pending_request=has_pending,
    )
