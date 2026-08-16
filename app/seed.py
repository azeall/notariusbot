"""Демонстрационные данные: нотариус, сотрудники, услуги, расписание.

Запуск:  python -m app.seed
Повторный запуск пересоздаёт демо-нотариуса целиком.
"""

import asyncio

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.domain.security import hash_password
from app.domain.starter import create_default_schedule, create_starter_catalog
from app.models import Staff, StaffRole, Tenant

DEMO_SLUG = "demo"

# Домены, которым разрешено встраивать виджет. Из них собирается frame-ancestors,
# поэтому демо-сайт продолжает работать и после пересоздания данных.
DEMO_ORIGINS = [
    "https://notarius-wn4h.vercel.app",
    "https://notarius-wn4h-azealls-projects.vercel.app",
    "https://notarius-wn4h-azeall-azealls-projects.vercel.app",
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
            allowed_origins=DEMO_ORIGINS,
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

        await create_default_schedule(session, tenant)

        # Тот же набор, что получает нотариус при регистрации, — чтобы демо
        # показывало ровно то, что увидит настоящий клиент.
        await create_starter_catalog(session, tenant)

        await session.commit()

    await dispose_engine()
    print(f"Демо-нотариус готов: /widget/{DEMO_SLUG}")
    print(f"Панель сотрудника:   /staff/{DEMO_SLUG}/login")
    print("  владелец:  owner@demo.ru / demo12345")
    print("  сотрудник: helper@demo.ru / demo12345")


if __name__ == "__main__":
    asyncio.run(seed())
