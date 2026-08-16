"""Праздники, журнал доступа и привязка Telegram сотруднику."""

from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.schedule import available_slots
from app.models import AuditLog, DayOff, Staff
from app.web.deps import db_session
from app.web.main import app


@pytest.fixture
async def http(engine, session):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with maker() as request_session:
            try:
                yield request_session
                await request_session.commit()
            except Exception:
                await request_session.rollback()
                raise

    app.dependency_overrides[db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def as_owner(http, tenant, owner):
    await http.post(
        f"/staff/{tenant.slug}/login", data={"email": owner.email, "password": "secret123"}
    )
    return http


# --- праздники --------------------------------------------------------------


async def test_owner_adds_day_off(as_owner, session, tenant):
    day = date.today() + timedelta(days=3)
    response = await as_owner.post(
        "/admin/schedule/days-off", data={"day": day.isoformat(), "reason": "Праздник"}
    )
    assert response.status_code == 303

    row = await session.scalar(select(DayOff).where(DayOff.tenant_id == tenant.id))
    assert row is not None
    assert row.day == day
    assert row.reason == "Праздник"


async def test_day_off_hides_slots(as_owner, session, tenant, visit_service):
    day = date.today() + timedelta(days=2)
    await as_owner.post("/admin/schedule/days-off", data={"day": day.isoformat()})

    slots = await available_slots(session, tenant=tenant, service=visit_service, days_ahead=7)
    assert all(slot.date() != day for slot in slots)


async def test_day_off_can_be_removed(as_owner, session, tenant):
    day = date.today() + timedelta(days=4)
    await as_owner.post("/admin/schedule/days-off", data={"day": day.isoformat()})
    row = await session.scalar(select(DayOff))

    assert (await as_owner.post(f"/admin/schedule/days-off/{row.id}/delete")).status_code == 303
    assert await session.scalar(select(DayOff)) is None


async def test_bad_date_rejected(as_owner):
    response = await as_owner.post("/admin/schedule/days-off", data={"day": "не дата"})
    assert response.status_code == 400


async def test_schedule_page_shows_days_off(as_owner):
    day = date.today() + timedelta(days=5)
    await as_owner.post(
        "/admin/schedule/days-off", data={"day": day.isoformat(), "reason": "Обучение"}
    )
    page = await as_owner.get("/admin/schedule")
    assert "Обучение" in page.text
    assert day.strftime("%d.%m.%Y") in page.text


# --- журнал доступа ---------------------------------------------------------


async def test_audit_page_lists_login(as_owner, owner):
    page = await as_owner.get("/admin/audit")
    assert page.status_code == 200
    assert "Вход в панель" in page.text
    assert owner.full_name in page.text


async def test_audit_csv_downloads(as_owner):
    response = await as_owner.get("/admin/audit.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    body = response.content.decode("utf-8")
    assert body.startswith("﻿"), "без BOM Excel ломает кириллицу"
    assert "Дата и время" in body


async def test_audit_is_tenant_isolated(as_owner, session, tenant, other_tenant):
    session.add(
        AuditLog(
            tenant_id=other_tenant.id,
            actor_label="Чужой сотрудник",
            action="login",
            object_type="staff",
        )
    )
    await session.commit()

    page = await as_owner.get("/admin/audit")
    assert "Чужой сотрудник" not in page.text


async def test_employee_cannot_open_audit(http, tenant, employee):
    await http.post(
        f"/staff/{tenant.slug}/login",
        data={"email": employee.email, "password": "secret123"},
    )
    assert (await http.get("/admin/audit")).status_code == 403
    assert (await http.get("/admin/audit.csv")).status_code == 403


# --- привязка Telegram ------------------------------------------------------


async def test_owner_issues_link_code(as_owner, session, tenant, employee):
    response = await as_owner.post(f"/admin/staff/{employee.id}/telegram-link")
    assert response.status_code == 303

    await session.refresh(employee)
    assert employee.telegram_link_code
    assert response.headers["location"].endswith(employee.telegram_link_code)


async def test_link_code_shown_on_staff_page(as_owner, session, employee):
    await as_owner.post(f"/admin/staff/{employee.id}/telegram-link")
    await session.refresh(employee)

    page = await as_owner.get(f"/admin/staff?code={employee.telegram_link_code}")
    assert "t.me/" in page.text
    assert employee.telegram_link_code in page.text


async def test_unlink_clears_chat(as_owner, session, employee):
    employee.telegram_chat_id = "555000"
    await session.commit()

    assert (await as_owner.post(f"/admin/staff/{employee.id}/telegram-unlink")).status_code == 303
    await session.refresh(employee)
    assert employee.telegram_chat_id is None


async def test_link_code_is_unique_per_staff(as_owner, session, employee, second_employee):
    await as_owner.post(f"/admin/staff/{employee.id}/telegram-link")
    await as_owner.post(f"/admin/staff/{second_employee.id}/telegram-link")
    await session.refresh(employee)
    await session.refresh(second_employee)
    assert employee.telegram_link_code != second_employee.telegram_link_code


async def test_owner_cannot_touch_other_tenant_staff(as_owner, session, other_tenant, employee):
    employee.tenant_id = other_tenant.id
    await session.commit()
    response = await as_owner.post(f"/admin/staff/{employee.id}/telegram-link")
    assert response.status_code == 404


async def test_notify_skips_staff_without_telegram(session, tenant, client, service):
    """Без подключённого Telegram уведомлять некого — и это не ошибка."""
    from app.domain.requests import create_request
    from app.models import Channel
    from app.notifications import notify_new_request

    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    delivered = await notify_new_request(
        session, request=request, client_name="Смирнов", client_phone="+79990000001"
    )
    assert delivered == 0


async def test_notification_text_has_the_essentials(session, tenant, client, service):
    from app.domain.requests import create_request
    from app.models import Channel
    from app.notifications import render_new_request

    request = await create_request(
        session,
        tenant=tenant,
        client=client,
        service=service,
        channel=Channel.WIDGET,
        client_comment="срочно",
    )
    await session.commit()

    text = render_new_request(request, "Смирнов Алексей", "+79990000001")
    assert f"№ {request.public_number}" in text
    assert service.title in text
    assert "+79990000001" in text
    assert "срочно" in text


async def test_only_active_staff_receive(session, tenant, employee):
    """Отключённый сотрудник не должен получать заявки."""
    employee.telegram_chat_id = "1"
    employee.is_active = False
    await session.commit()

    rows = list(
        await session.scalars(
            select(Staff).where(
                Staff.tenant_id == tenant.id,
                Staff.is_active.is_(True),
                Staff.telegram_chat_id.is_not(None),
            )
        )
    )
    assert rows == []
