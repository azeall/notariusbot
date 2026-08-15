"""Демонстрационные данные: нотариус, сотрудники, услуги, расписание.

Запуск:  python -m app.seed
Повторный запуск пересоздаёт демо-нотариуса целиком.
"""

import asyncio
from datetime import time

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.domain.security import hash_password
from app.models import (
    Service,
    ServiceDocument,
    Staff,
    StaffRole,
    SubmissionMode,
    Tenant,
    WorkingHours,
)

DEMO_SLUG = "demo"

# (код, название, режим, минуты, срок, цена, слова для поиска, [(документ, пояснение, обязателен)])
SERVICES: list[tuple] = [
    (
        "doverennost-avto",
        "Доверенность на распоряжение транспортным средством",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "от 2 400 ₽",
        ["машина", "авто", "автомобиль", "тачка", "транспорт", "доверенность"],
        [
            ("Паспорт доверителя", "Все заполненные страницы", True),
            ("Свидетельство о регистрации ТС", "СТС, обе стороны", True),
            ("Паспортные данные представителя", "Достаточно копии или фотографии", True),
            ("Паспорт транспортного средства", "ПТС, если есть на руках", False),
        ],
    ),
    (
        "doverennost-predstavitelstvo",
        "Доверенность на представление интересов",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "от 2 000 ₽",
        ["представитель", "интересы", "суд", "госорганы", "доверенность"],
        [
            ("Паспорт доверителя", "Все заполненные страницы", True),
            ("Паспортные данные представителя", "", True),
            ("Перечень полномочий", "Своими словами: что именно поручаете", False),
        ],
    ),
    (
        "soglasie-vyezd-rebenka",
        "Согласие на выезд ребёнка за границу",
        SubmissionMode.DOCUMENTS,
        30,
        "1 рабочий день",
        "от 1 800 ₽",
        ["ребенок", "выезд", "заграница", "согласие", "поездка", "дети"],
        [
            ("Паспорт родителя", "", True),
            ("Свидетельство о рождении ребёнка", "", True),
            ("Загранпаспорт ребёнка", "", True),
            ("Данные сопровождающего", "ФИО и паспортные данные", True),
            ("Сведения о странах и сроках поездки", "", True),
        ],
    ),
    (
        "kopii-dokumentov",
        "Свидетельствование верности копий документов",
        SubmissionMode.DOCUMENTS,
        15,
        "в день обращения",
        "от 100 ₽ за страницу",
        ["копия", "копии", "заверить", "верность"],
        [
            ("Оригиналы документов", "Копии нотариус изготовит сам", True),
            ("Паспорт заявителя", "", True),
        ],
    ),
    (
        "zaveshchanie",
        "Удостоверение завещания",
        SubmissionMode.VISIT,
        60,
        "в день обращения",
        "от 2 700 ₽",
        ["завещание", "наследство", "наследники", "завещать"],
        [
            ("Паспорт завещателя", "", True),
            ("Сведения о наследниках", "ФИО, даты рождения, родство", True),
            ("Документы на имущество", "Если завещаете конкретные объекты", False),
        ],
    ),
    (
        "brachnyy-dogovor",
        "Брачный договор",
        SubmissionMode.VISIT,
        60,
        "в день обращения",
        "от 10 000 ₽",
        ["брачный", "брак", "супруги", "договор", "развод"],
        [
            ("Паспорта обоих супругов", "Требуется присутствие обоих", True),
            ("Свидетельство о заключении брака", "Если брак уже зарегистрирован", False),
            ("Документы на имущество", "Выписки ЕГРН, ПТС и подобное", False),
        ],
    ),
    (
        "sdelka-nedvizhimost",
        "Договор купли-продажи недвижимости",
        SubmissionMode.VISIT,
        90,
        "от 3 рабочих дней",
        "по тарифу, зависит от суммы сделки",
        ["квартира", "недвижимость", "продажа", "купля", "дом", "сделка"],
        [
            ("Паспорта всех сторон", "Требуется присутствие всех участников", True),
            ("Выписка из ЕГРН", "Не старше 30 дней", True),
            ("Правоустанавливающие документы", "Договор, по которому объект был получен", True),
            ("Согласие супруга на сделку", "Если объект приобретён в браке", False),
        ],
    ),
    (
        "nasledstvennoe-delo",
        "Открытие наследственного дела",
        SubmissionMode.VISIT,
        45,
        "6 месяцев по закону",
        "по тарифу",
        ["наследство", "наследственное", "умер", "вступить", "принять наследство"],
        [
            ("Паспорт заявителя", "", True),
            ("Свидетельство о смерти", "", True),
            ("Документы о родстве", "Свидетельство о рождении, о браке", True),
            ("Документы на имущество наследодателя", "", False),
        ],
    ),
]


async def seed() -> None:
    maker = get_sessionmaker()
    async with maker() as session:
        existing = await session.scalar(select(Tenant).where(Tenant.slug == DEMO_SLUG))
        if existing is not None:
            await session.delete(existing)
            await session.commit()

        tenant = Tenant(
            slug=DEMO_SLUG,
            display_name="Нотариус Иванов И. И.",
            city="Москва",
            address="ул. Тверская, д. 1, офис 5",
            phone="+7 495 000-00-00",
            timezone="Europe/Moscow",
            allowed_origins=[],
        )
        session.add(tenant)
        await session.flush()

        session.add_all(
            [
                Staff(
                    tenant_id=tenant.id,
                    email="owner@demo.ru",
                    password_hash=hash_password("demo12345"),
                    full_name="Иванов Иван Иванович",
                    role=StaffRole.OWNER,
                ),
                Staff(
                    tenant_id=tenant.id,
                    email="helper@demo.ru",
                    password_hash=hash_password("demo12345"),
                    full_name="Сидорова Анна Петровна",
                    role=StaffRole.EMPLOYEE,
                ),
            ]
        )

        for weekday in range(7):
            session.add(
                WorkingHours(
                    tenant_id=tenant.id,
                    weekday=weekday,
                    is_working=weekday < 5,
                    opens_at=time(9, 0),
                    closes_at=time(18, 0) if weekday < 4 else time(17, 0),
                    break_starts_at=time(13, 0),
                    break_ends_at=time(14, 0),
                )
            )

        for order, (
            slug,
            title,
            mode,
            duration,
            lead_time,
            price,
            keywords,
            documents,
        ) in enumerate(SERVICES):
            service = Service(
                tenant_id=tenant.id,
                slug=slug,
                title=title,
                submission_mode=mode,
                visit_duration_minutes=duration,
                lead_time_note=lead_time,
                price_note=price,
                keywords=keywords,
                sort_order=order,
            )
            session.add(service)
            await session.flush()
            for index, (doc_title, doc_note, required) in enumerate(documents):
                session.add(
                    ServiceDocument(
                        tenant_id=tenant.id,
                        service_id=service.id,
                        title=doc_title,
                        description=doc_note,
                        is_required=required,
                        sort_order=index,
                    )
                )

        await session.commit()

    await dispose_engine()
    print(f"Демо-нотариус готов: /widget/{DEMO_SLUG}")
    print(f"Панель сотрудника:   /staff/{DEMO_SLUG}/login")
    print("  владелец:  owner@demo.ru / demo12345")
    print("  сотрудник: helper@demo.ru / demo12345")


if __name__ == "__main__":
    asyncio.run(seed())
