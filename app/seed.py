"""Демонстрационные данные: нотариус, сотрудники, услуги, расписание.

Запуск:  python -m app.seed
Повторный запуск пересоздаёт демо-нотариуса целиком.
"""

import asyncio
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.domain.security import hash_password
from app.domain.starter import create_default_schedule, create_starter_catalog
from app.models import PlatformAdmin, Staff, StaffRole, Tenant

DEMO_SLUG = "demo"

# Домены, которым разрешено встраивать виджет. Из них собирается frame-ancestors,
# поэтому демо-сайт продолжает работать и после пересоздания данных.
DEMO_ORIGINS = [
    "https://notarius-wn4h.vercel.app",
    "https://notarius-wn4h-azealls-projects.vercel.app",
    "https://notarius-wn4h-azeall-azealls-projects.vercel.app",
]


async def seed() -> None:
    settings = get_settings()
    platform_email = settings.platform_admin_email
    # Пароля в коде нет: репозиторий публичный. Не задан в окружении — придумаем
    # случайный и покажем один раз, чтобы у кабинета не было известного входа.
    platform_password = settings.platform_admin_password or secrets.token_urlsafe(12)
    generated = not settings.platform_admin_password

    maker = get_sessionmaker()
    async with maker() as session:
        # Владелец сервиса — тот, кто подключает нотариусов.
        admin = await session.scalar(
            select(PlatformAdmin).where(PlatformAdmin.email == platform_email)
        )
        if admin is None:
            session.add(
                PlatformAdmin(
                    email=platform_email,
                    password_hash=hash_password(platform_password),
                    full_name="Владелец сервиса",
                )
            )
            await session.commit()
        else:
            platform_password = None  # уже заведён, пароль прежний

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
        # Демо-нотариус считается уже принявшим приглашение: у него есть вход.
        tenant.invite_accepted_at = datetime.now(UTC)
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
    print()
    print("Кабинет владельца сервиса: /platform/login")
    if platform_password is None:
        print(f"  {platform_email} / пароль прежний")
    elif generated:
        print(f"  {platform_email} / {platform_password}")
        print("  Пароль сгенерирован и больше нигде не сохранён — запишите его.")
        print("  Чтобы задать свой, впишите PLATFORM_ADMIN_PASSWORD в .env.")
    else:
        print(f"  {platform_email} / из PLATFORM_ADMIN_PASSWORD")


if __name__ == "__main__":
    asyncio.run(seed())
