"""Стартовый набор для нового нотариуса.

Пустая админка — плохая первая встреча: нотариус заходит и не понимает,
с чего начать. Поэтому при регистрации заводятся типовые услуги с перечнями,
которые он потом правит под себя.
"""

from dataclasses import dataclass
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Service, ServiceDocument, SubmissionMode, Tenant, WorkingHours


@dataclass(frozen=True)
class StarterDocument:
    title: str
    description: str = ""
    is_required: bool = True


@dataclass(frozen=True)
class StarterService:
    slug: str
    title: str
    mode: SubmissionMode
    minutes: int
    lead_time: str
    price: str
    keywords: tuple[str, ...]
    documents: tuple[StarterDocument, ...]


STARTER_SERVICES: tuple[StarterService, ...] = (
    StarterService(
        "doverennost-avto",
        "Доверенность на распоряжение транспортным средством",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "",
        ("машина", "авто", "автомобиль", "тачка", "транспорт", "доверенность"),
        (
            StarterDocument("Паспорт доверителя", "Все заполненные страницы"),
            StarterDocument("Свидетельство о регистрации ТС", "СТС, обе стороны"),
            StarterDocument("Паспортные данные представителя", "Достаточно копии"),
            StarterDocument("Паспорт транспортного средства", "ПТС, если есть", False),
        ),
    ),
    StarterService(
        "doverennost-predstavitelstvo",
        "Доверенность на представление интересов",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "",
        ("представитель", "интересы", "суд", "госорганы", "доверенность"),
        (
            StarterDocument("Паспорт доверителя", "Все заполненные страницы"),
            StarterDocument("Паспортные данные представителя"),
            StarterDocument("Перечень полномочий", "Своими словами", False),
        ),
    ),
    StarterService(
        "soglasie-vyezd-rebenka",
        "Согласие на выезд ребёнка за границу",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "",
        ("ребенок", "выезд", "заграница", "согласие", "поездка", "дети"),
        (
            StarterDocument("Паспорт родителя"),
            StarterDocument("Свидетельство о рождении ребёнка"),
            StarterDocument("Загранпаспорт ребёнка"),
            StarterDocument("Данные сопровождающего", "ФИО и паспортные данные"),
            StarterDocument("Сведения о странах и сроках поездки"),
        ),
    ),
    StarterService(
        "kopii-dokumentov",
        "Свидетельствование верности копий документов",
        SubmissionMode.DOCUMENTS,
        15,
        "в день обращения",
        "",
        ("копия", "копии", "заверить", "верность"),
        (
            StarterDocument("Оригиналы документов", "Копии нотариус изготовит сам"),
            StarterDocument("Паспорт заявителя"),
        ),
    ),
    StarterService(
        "zaveshchanie",
        "Удостоверение завещания",
        SubmissionMode.VISIT,
        60,
        "в день обращения",
        "",
        ("завещание", "наследство", "наследники", "завещать"),
        (
            StarterDocument("Паспорт завещателя"),
            StarterDocument("Сведения о наследниках", "ФИО, даты рождения, родство"),
            StarterDocument("Документы на имущество", "Если завещаете конкретное", False),
        ),
    ),
    StarterService(
        "sdelka-nedvizhimost",
        "Договор купли-продажи недвижимости",
        SubmissionMode.VISIT,
        90,
        "от 3 рабочих дней",
        "",
        ("квартира", "недвижимость", "продажа", "купля", "дом", "сделка"),
        (
            StarterDocument("Паспорта всех сторон", "Нужно присутствие всех участников"),
            StarterDocument("Выписка из ЕГРН", "Не старше 30 дней"),
            StarterDocument("Правоустанавливающие документы"),
            StarterDocument("Согласие супруга на сделку", "Если объект куплен в браке", False),
        ),
    ),
    StarterService(
        "nasledstvennoe-delo",
        "Открытие наследственного дела",
        SubmissionMode.VISIT,
        45,
        "6 месяцев по закону",
        "",
        ("наследство", "наследственное", "умер", "вступить", "принять наследство"),
        (
            StarterDocument("Паспорт заявителя"),
            StarterDocument("Свидетельство о смерти"),
            StarterDocument("Документы о родстве", "Свидетельство о рождении, о браке"),
            StarterDocument("Документы на имущество наследодателя", "", False),
        ),
    ),
)


async def create_starter_catalog(session: AsyncSession, tenant: Tenant) -> int:
    """Завести типовые услуги. Возвращает их количество."""
    for order, item in enumerate(STARTER_SERVICES):
        service = Service(
            tenant_id=tenant.id,
            slug=item.slug,
            title=item.title,
            submission_mode=item.mode,
            visit_duration_minutes=item.minutes,
            lead_time_note=item.lead_time,
            price_note=item.price,
            keywords=list(item.keywords),
            sort_order=order,
        )
        session.add(service)
        await session.flush()
        for index, doc in enumerate(item.documents):
            session.add(
                ServiceDocument(
                    tenant_id=tenant.id,
                    service_id=service.id,
                    title=doc.title,
                    description=doc.description,
                    is_required=doc.is_required,
                    sort_order=index,
                )
            )
    return len(STARTER_SERVICES)


async def create_default_schedule(session: AsyncSession, tenant: Tenant) -> None:
    """Будни 9:00–18:00 с перерывом. Нотариус поправит под себя."""
    for weekday in range(7):
        session.add(
            WorkingHours(
                tenant_id=tenant.id,
                weekday=weekday,
                is_working=weekday < 5,
                opens_at=time(9, 0),
                closes_at=time(18, 0),
                break_starts_at=time(13, 0),
                break_ends_at=time(14, 0),
            )
        )
