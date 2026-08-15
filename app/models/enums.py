from enum import StrEnum


class StaffRole(StrEnum):
    OWNER = "owner"  # сам нотариус: видит всё, правит услуги и сотрудников
    EMPLOYEE = "employee"  # помощник: разбирает заявки


class RequestStatus(StrEnum):
    NEW = "new"  # создана клиентом, никто не взял
    CLAIMED = "claimed"  # сотрудник взял в работу
    AWAITING_DOCUMENTS = "awaiting_documents"  # ждём файлы от клиента
    AWAITING_VISIT = "awaiting_visit"  # нужен личный визит, время согласовано
    COMPLETED = "completed"
    REJECTED = "rejected"  # отклонена сотрудником
    CANCELLED = "cancelled"  # отменена клиентом


TERMINAL_STATUSES = frozenset(
    {RequestStatus.COMPLETED, RequestStatus.REJECTED, RequestStatus.CANCELLED}
)


class SubmissionMode(StrEnum):
    """Как услуга принимается: документами онлайн или личным визитом."""

    DOCUMENTS = "documents"
    VISIT = "visit"


class Channel(StrEnum):
    WIDGET = "widget"
    TELEGRAM = "telegram"
    MAX = "max"
