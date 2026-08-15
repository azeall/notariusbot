import os
import tempfile
from collections.abc import AsyncIterator
from datetime import time
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Тесты работают на отдельной базе, чтобы не затирать данные разработки.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://notary:notarybot_dev@127.0.0.1:5432/notarybot_test",
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# Документы тестов не должны попадать в рабочее хранилище проекта.
os.environ["STORAGE_DIR"] = str(Path(tempfile.gettempdir()) / "notarybot-test-storage")
os.environ["PUBLIC_BASE_URL"] = "http://test"

from app.domain.security import hash_password  # noqa: E402
from app.models import (  # noqa: E402
    Base,
    Client,
    Service,
    ServiceDocument,
    Staff,
    StaffRole,
    SubmissionMode,
    Tenant,
    WorkingHours,
)
from app.models.enums import Channel  # noqa: E402


_ALL_TABLES = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)


@pytest.fixture
async def engine():
    """Движок на каждый тест.

    Именно на каждый, а не на сессию: pytest-asyncio 0.25 заводит отдельный
    event loop под тест, а соединение asyncpg нельзя делить между разными loop'ами.
    Схема создаётся один раз (checkfirst), таблицы чистятся перед тестом.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(f"TRUNCATE {_ALL_TABLES} RESTART IDENTITY CASCADE"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncIterator[AsyncSession]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture
async def tenant(session) -> Tenant:
    tenant = Tenant(
        slug="ivanov",
        display_name="Нотариус Иванов И. И.",
        city="Москва",
        timezone="Europe/Moscow",
        allowed_origins=[],
    )
    session.add(tenant)
    await session.flush()
    for weekday in range(5):
        session.add(
            WorkingHours(
                tenant_id=tenant.id,
                weekday=weekday,
                is_working=True,
                opens_at=time(9, 0),
                closes_at=time(18, 0),
                break_starts_at=time(13, 0),
                break_ends_at=time(14, 0),
            )
        )
    await session.commit()
    return tenant


@pytest.fixture
async def other_tenant(session) -> Tenant:
    tenant = Tenant(slug="petrov", display_name="Нотариус Петров П. П.", allowed_origins=[])
    session.add(tenant)
    await session.commit()
    return tenant


@pytest.fixture
async def owner(session, tenant) -> Staff:
    staff = Staff(
        tenant_id=tenant.id,
        email="owner@example.com",
        password_hash=hash_password("secret123"),
        full_name="Иванов Иван Иванович",
        role=StaffRole.OWNER,
    )
    session.add(staff)
    await session.commit()
    return staff


@pytest.fixture
async def employee(session, tenant) -> Staff:
    staff = Staff(
        tenant_id=tenant.id,
        email="helper@example.com",
        password_hash=hash_password("secret123"),
        full_name="Сидорова Анна",
        role=StaffRole.EMPLOYEE,
    )
    session.add(staff)
    await session.commit()
    return staff


@pytest.fixture
async def second_employee(session, tenant) -> Staff:
    staff = Staff(
        tenant_id=tenant.id,
        email="helper2@example.com",
        password_hash=hash_password("secret123"),
        full_name="Кузнецов Пётр",
        role=StaffRole.EMPLOYEE,
    )
    session.add(staff)
    await session.commit()
    return staff


@pytest.fixture
async def service(session, tenant) -> Service:
    service = Service(
        tenant_id=tenant.id,
        slug="doverennost-avto",
        title="Доверенность на распоряжение транспортным средством",
        description="Оформление доверенности на автомобиль",
        submission_mode=SubmissionMode.DOCUMENTS,
        visit_duration_minutes=30,
        lead_time_note="1 рабочий день",
        keywords=["машина", "авто", "автомобиль", "доверенность"],
        sort_order=1,
    )
    session.add(service)
    await session.flush()
    session.add_all(
        [
            ServiceDocument(
                tenant_id=tenant.id,
                service_id=service.id,
                title="Паспорт доверителя",
                is_required=True,
                sort_order=1,
            ),
            ServiceDocument(
                tenant_id=tenant.id,
                service_id=service.id,
                title="Свидетельство о регистрации ТС",
                is_required=True,
                sort_order=2,
            ),
            ServiceDocument(
                tenant_id=tenant.id,
                service_id=service.id,
                title="Паспортные данные представителя",
                is_required=False,
                sort_order=3,
            ),
        ]
    )
    await session.commit()
    await session.refresh(service, ["documents"])
    return service


@pytest.fixture
async def visit_service(session, tenant) -> Service:
    service = Service(
        tenant_id=tenant.id,
        slug="zaveshchanie",
        title="Удостоверение завещания",
        submission_mode=SubmissionMode.VISIT,
        visit_duration_minutes=60,
        keywords=["завещание", "наследство"],
        sort_order=2,
    )
    session.add(service)
    await session.flush()
    session.add(
        ServiceDocument(
            tenant_id=tenant.id,
            service_id=service.id,
            title="Паспорт",
            is_required=True,
            sort_order=1,
        )
    )
    await session.commit()
    await session.refresh(service, ["documents"])
    return service


@pytest.fixture
async def client(session, tenant) -> Client:
    client = Client(
        tenant_id=tenant.id,
        channel=Channel.WIDGET,
        external_id="+79990000001",
        full_name="Смирнов Алексей",
        phone="+79990000001",
    )
    session.add(client)
    await session.commit()
    return client
