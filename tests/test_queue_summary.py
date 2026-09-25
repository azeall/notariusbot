"""Work summary totals and linked selections share tenant/participation rules."""

from datetime import UTC, datetime, timedelta
from html import unescape
import re
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo

import pytest
from starlette.requests import Request as HttpRequest

from app.models import (
    Appointment, Channel, Client, ParticipationStatus, Request, RequestParticipant,
    RequestStatus, StaffRole, SubmissionMode,
)
from app.web import staff as staff_web


@pytest.fixture
def frozen_now(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 25, 1, 8, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(staff_web, "datetime", FrozenDateTime)


async def page(session, employee, **params):
    request = HttpRequest({"type": "http", "method": "GET", "path": "/staff",
                           "query_string": urlencode(params).encode(), "headers": [],
                           "scheme": "http", "server": ("test", 80)})
    return await staff_web.queue(request, staff=employee, session=session)


def visible(response):
    return {row.id for key in ("unclaimed", "mine", "others")
            for row in response.context[key]}


@pytest.fixture
async def summary_rows(session, tenant, other_tenant, employee, second_employee, frozen_now):
    rows = {}

    async def add(key, state, lead=None, tenant_id=None, appointments=(), participant=None):
        tenant_id = tenant_id or tenant.id
        client = Client(tenant_id=tenant_id, channel=Channel.WIDGET,
                        external_id=f"summary-{key}", full_name=f"Клиент {key}", phone="")
        session.add(client)
        await session.flush()
        row = Request(tenant_id=tenant_id, client_id=client.id, public_number=len(rows) + 1,
                      service_title=key, submission_mode=SubmissionMode.DOCUMENTS,
                      channel=Channel.WIDGET, status=state, assigned_staff_id=lead,
                      created_at=datetime(2026, 9, 1, tzinfo=UTC), checklist=[])
        session.add(row)
        await session.flush()
        rows[key] = row
        for starts_at, cancelled, appointment_tenant in appointments:
            session.add(Appointment(tenant_id=appointment_tenant or tenant_id,
                                    request_id=row.id, starts_at=starts_at,
                                    ends_at=starts_at + timedelta(minutes=15),
                                    is_cancelled=cancelled))
        if participant:
            state, participant_tenant = participant
            session.add(RequestParticipant(tenant_id=participant_tenant or tenant_id,
                                           request_id=row.id, staff_id=employee.id,
                                           status=state))
        return row

    # Midnight Moscow is 21:00 UTC on the preceding date.
    start = datetime(2026, 9, 24, 21, tzinfo=UTC)
    await add("free", RequestStatus.NEW)
    await add("docs", RequestStatus.AWAITING_DOCUMENTS, employee.id)
    await add("helper_docs", RequestStatus.AWAITING_DOCUMENTS, second_employee.id,
              participant=(ParticipationStatus.ACTIVE, None))
    await add("other_docs", RequestStatus.AWAITING_DOCUMENTS, second_employee.id)
    for state in (ParticipationStatus.REQUESTED, ParticipationStatus.DECLINED, ParticipationStatus.LEFT):
        await add(state.value, RequestStatus.AWAITING_DOCUMENTS, second_employee.id,
                  participant=(state, None))
    await add("foreign_participation", RequestStatus.AWAITING_DOCUMENTS, second_employee.id,
              participant=(ParticipationStatus.ACTIVE, other_tenant.id))
    await add("midnight", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start, False, None)])
    await add("helper_visit", RequestStatus.CLAIMED, second_employee.id,
              appointments=[(start + timedelta(hours=12), False, None)],
              participant=(ParticipationStatus.ACTIVE, None))
    # Two bookings still represent one actionable request.
    await add("two_bookings", RequestStatus.AWAITING_DOCUMENTS, employee.id,
              appointments=[(start + timedelta(hours=13), False, None),
                            (start + timedelta(hours=14), False, None)],
              participant=(ParticipationStatus.ACTIVE, None))
    await add("last_in_day", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start + timedelta(days=1, microseconds=-1), False, None)])
    await add("tomorrow", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start + timedelta(days=1), False, None)])
    await add("yesterday", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start - timedelta(microseconds=1), False, None)])
    await add("cancelled_booking", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start + timedelta(hours=15), True, None)])
    await add("other_visit", RequestStatus.AWAITING_VISIT, second_employee.id,
              appointments=[(start + timedelta(hours=16), False, None)])
    await add("foreign_booking", RequestStatus.AWAITING_VISIT, employee.id,
              appointments=[(start + timedelta(hours=17), False, other_tenant.id)])
    await add("no_booking", RequestStatus.AWAITING_VISIT, employee.id)
    for i, state in enumerate((RequestStatus.COMPLETED, RequestStatus.REJECTED, RequestStatus.CANCELLED)):
        await add(state.value, state, employee.id,
                  appointments=[(start + timedelta(hours=18, minutes=i), False, None)])
    await add("foreign_free", RequestStatus.NEW, tenant_id=other_tenant.id)
    # Deliberately inconsistent links must not cross tenant boundaries.
    await add("foreign_docs", RequestStatus.AWAITING_DOCUMENTS, employee.id, other_tenant.id)
    await add("foreign_visit", RequestStatus.AWAITING_VISIT, employee.id, other_tenant.id,
              appointments=[(start + timedelta(hours=19), False, tenant.id)])
    await session.commit()
    return rows


@pytest.mark.parametrize("role", [StaffRole.EMPLOYEE, StaffRole.OWNER])
async def test_summary_counts_active_tenant_work_including_helpers(
    session, employee, summary_rows, role,
):
    employee.role = role
    response = await page(session, employee)
    assert response.context["summary"] == {"free": 1, "mine_documents": 3, "mine_visits_today": 4}
    assert response.context["new_count"] == 1
    summary_html = response.body.decode().split('<section class="queue-summary"', 1)[1].split("</section>", 1)[0]
    assert "Независимо от фильтров" in summary_html
    assert "веду или помогаю" in summary_html
    assert "Europe/Moscow" in summary_html
    assert "Клиент" not in summary_html
    asset_version = staff_web._templates().env.globals["asset_version"]
    assert f'/static/queue-summary.css?v={asset_version}' in response.body.decode()


@pytest.mark.parametrize("params", [
    {"q": "no match"}, {"status": "claimed"}, {"assignee": "unassigned"},
    {"date_from": "2026-09-26"}, {"work": "mine_documents"}, {"work": "invalid"},
])
async def test_summary_is_independent_of_current_filters(session, employee, summary_rows, params):
    response = await page(session, employee, **params)
    assert response.context["summary"] == {"free": 1, "mine_documents": 3, "mine_visits_today": 4}


async def test_summary_links_open_exact_counted_requests(session, employee, summary_rows):
    response = await page(session, employee, q="no match", assignee="unassigned")
    summary_html = response.body.decode().split('<section class="queue-summary"', 1)[1].split("</section>", 1)[0]
    links = [unescape(link) for link in re.findall(r'href="([^"]+)"', summary_html)]
    expected = [
        {summary_rows["free"].id},
        {summary_rows[key].id for key in ("docs", "helper_docs", "two_bookings")},
        {summary_rows[key].id for key in ("midnight", "helper_visit", "two_bookings", "last_in_day")},
    ]
    assert len(links) == 3
    for link, ids in zip(links, expected, strict=True):
        assert urlsplit(link).path == "/staff"
        selected = await page(session, employee, **dict(parse_qsl(urlsplit(link).query)))
        assert selected.status_code == 200
        assert visible(selected) == ids


async def test_work_filter_combines_with_search_and_can_be_changed(session, employee, summary_rows):
    response = await page(session, employee, work="mine_documents", q="helper_docs")
    assert visible(response) == {summary_rows["helper_docs"].id}
    assert 'value="mine_documents" selected' in response.body.decode()
    assert response.context["filters_active"]
    invalid = await page(session, employee, work="unknown")
    assert invalid.status_code == 400
    assert not visible(invalid)
    assert invalid.context["filter_error"]


async def test_today_is_tenant_date_not_utc_or_server_date(session, tenant, employee, summary_rows):
    tenant.timezone = "America/Los_Angeles"  # Still September 24 at frozen_now.
    await session.commit()
    response = await page(session, employee, work="mine_visits_today")
    assert visible(response) == {summary_rows[key].id for key in ("yesterday", "midnight")}
    assert response.context["summary"]["mine_visits_today"] == 2


@pytest.mark.parametrize("day, next_day", [("2026-03-08", "2026-03-09"), ("2026-11-01", "2026-11-02")])
async def test_today_handles_dst_day_boundaries(session, tenant, employee, summary_rows, monkeypatch, day, next_day):
    tenant.timezone = "America/New_York"
    tz = ZoneInfo(tenant.timezone)
    start = datetime.fromisoformat(day).replace(tzinfo=tz).astimezone(UTC)
    end = datetime.fromisoformat(next_day).replace(tzinfo=tz).astimezone(UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (start + timedelta(hours=12)).astimezone(tz)

    monkeypatch.setattr(staff_web, "datetime", FrozenDateTime)
    for key, when in (("docs", start), ("helper_docs", end - timedelta(microseconds=1)),
                      ("no_booking", end), ("cancelled_booking", start - timedelta(microseconds=1))):
        session.add(Appointment(tenant_id=tenant.id, request_id=summary_rows[key].id,
                                starts_at=when, ends_at=when + timedelta(minutes=15)))
    await session.commit()
    response = await page(session, employee, work="mine_visits_today")
    assert visible(response) == {summary_rows[key].id for key in ("docs", "helper_docs")}
    assert response.context["summary"]["mine_visits_today"] == 2


async def test_empty_summary(session, employee, frozen_now):
    response = await page(session, employee)
    assert response.context["summary"] == {"free": 0, "mine_documents": 0, "mine_visits_today": 0}
    assert response.context["new_count"] == 0
