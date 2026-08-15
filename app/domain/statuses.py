from app.models.enums import RequestStatus

# Разрешённые переходы. Всё, чего здесь нет, доменный слой отклоняет —
# статусы не должны меняться произвольным UPDATE из любой части кода.
ALLOWED_TRANSITIONS: dict[RequestStatus, frozenset[RequestStatus]] = {
    RequestStatus.NEW: frozenset(
        {
            RequestStatus.CLAIMED,
            RequestStatus.CANCELLED,
            RequestStatus.REJECTED,
        }
    ),
    RequestStatus.CLAIMED: frozenset(
        {
            RequestStatus.AWAITING_DOCUMENTS,
            RequestStatus.AWAITING_VISIT,
            RequestStatus.COMPLETED,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
            # Сотрудник может вернуть заявку в общую очередь.
            RequestStatus.NEW,
        }
    ),
    RequestStatus.AWAITING_DOCUMENTS: frozenset(
        {
            RequestStatus.CLAIMED,
            RequestStatus.AWAITING_VISIT,
            RequestStatus.COMPLETED,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.AWAITING_VISIT: frozenset(
        {
            RequestStatus.CLAIMED,
            RequestStatus.AWAITING_DOCUMENTS,
            RequestStatus.COMPLETED,
            RequestStatus.REJECTED,
            RequestStatus.CANCELLED,
        }
    ),
    RequestStatus.COMPLETED: frozenset(),
    RequestStatus.REJECTED: frozenset(),
    RequestStatus.CANCELLED: frozenset(),
}

STATUS_LABELS: dict[RequestStatus, str] = {
    RequestStatus.NEW: "Новая",
    RequestStatus.CLAIMED: "В работе",
    RequestStatus.AWAITING_DOCUMENTS: "Ждём документы",
    RequestStatus.AWAITING_VISIT: "Записан на приём",
    RequestStatus.COMPLETED: "Завершена",
    RequestStatus.REJECTED: "Отклонена",
    RequestStatus.CANCELLED: "Отменена клиентом",
}


class TransitionError(Exception):
    """Попытка перевести заявку в статус, недопустимый из текущего."""


def ensure_transition_allowed(current: RequestStatus, target: RequestStatus) -> None:
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise TransitionError(
            f"Нельзя перевести заявку из «{STATUS_LABELS[current]}» "
            f"в «{STATUS_LABELS[target]}»"
        )
