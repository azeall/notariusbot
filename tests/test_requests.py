import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.requests import (
    RequestError,
    claim_request,
    count_recent_requests_from_ip,
    create_request,
    issue_upload_token,
    resolve_upload_token,
    transition_request,
)
from app.domain.statuses import TransitionError
from app.models import Channel, RequestEvent, RequestStatus, ServiceDocument


async def test_create_request_snapshots_checklist(session, tenant, client, service):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    assert request.public_number == 1
    assert request.status is RequestStatus.NEW
    assert [item["title"] for item in request.checklist] == [
        "Паспорт доверителя",
        "Свидетельство о регистрации ТС",
        "Паспортные данные представителя",
    ]
    assert request.checklist[2]["is_required"] is False


async def test_checklist_survives_service_edit(session, tenant, client, service):
    """Нотариус правит услугу — у уже созданной заявки перечень не меняется."""
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    original = list(request.checklist)

    doc = await session.scalar(
        select(ServiceDocument).where(ServiceDocument.title == "Паспорт доверителя")
    )
    doc.title = "Паспорт РФ доверителя (все страницы)"
    session.add(
        ServiceDocument(
            tenant_id=tenant.id,
            service_id=service.id,
            title="Согласие супруга",
            sort_order=4,
        )
    )
    await session.commit()

    await session.refresh(request)
    assert request.checklist == original


async def test_numbering_is_per_tenant(session, tenant, client, service):
    first = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    second = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    assert (first.public_number, second.public_number) == (1, 2)


async def test_cannot_use_service_of_another_tenant(
    session, tenant, other_tenant, client, service
):
    service.tenant_id = other_tenant.id
    await session.flush()
    with pytest.raises(RequestError):
        await create_request(
            session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
        )


async def test_claim_marks_staff_and_status(session, tenant, client, service, employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    claimed = await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()

    assert claimed is not None
    assert claimed.status is RequestStatus.CLAIMED
    assert claimed.assigned_staff_id == employee.id
    assert claimed.claimed_at is not None


async def test_second_claim_returns_none(
    session, tenant, client, service, employee, second_employee
):
    """Второй сотрудник не может перехватить уже взятую заявку."""
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    first = await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    second = await claim_request(session, request_id=request.id, staff=second_employee)
    await session.commit()

    assert first is not None
    assert second is None
    assert first.assigned_staff_id == employee.id


async def test_concurrent_claims_give_exactly_one_winner(
    engine, session, tenant, client, service, employee, second_employee
):
    """Два одновременных захвата в разных соединениях — победитель ровно один."""
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def try_claim(staff_id):
        async with maker() as s:
            staff = await s.get(type(employee), staff_id)
            result = await claim_request(s, request_id=request.id, staff=staff)
            await s.commit()
            return result

    results = await asyncio.gather(
        try_claim(employee.id), try_claim(second_employee.id), return_exceptions=True
    )
    winners = [r for r in results if r is not None and not isinstance(r, Exception)]
    assert len(winners) == 1


async def test_claim_from_another_tenant_fails(
    session, tenant, other_tenant, client, service, employee
):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    employee.tenant_id = other_tenant.id
    await session.flush()

    assert await claim_request(session, request_id=request.id, staff=employee) is None


async def test_illegal_transition_rejected(session, tenant, client, service, employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    # Из «Новой» нельзя сразу в «Завершена», минуя работу сотрудника.
    with pytest.raises(TransitionError):
        await transition_request(
            session, request=request, target=RequestStatus.COMPLETED, staff=employee
        )


async def test_terminal_status_is_final(session, tenant, client, service, employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    await session.refresh(request)

    await transition_request(
        session, request=request, target=RequestStatus.COMPLETED, staff=employee
    )
    await session.commit()

    assert request.closed_at is not None
    with pytest.raises(TransitionError):
        await transition_request(
            session, request=request, target=RequestStatus.CLAIMED, staff=employee
        )


async def test_return_to_queue_clears_assignee(session, tenant, client, service, employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()
    await session.refresh(request)

    await transition_request(
        session, request=request, target=RequestStatus.NEW, staff=employee, comment="не моё"
    )
    await session.commit()

    assert request.assigned_staff_id is None
    assert request.claimed_at is None


async def test_events_record_history(session, tenant, client, service, employee):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await claim_request(session, request_id=request.id, staff=employee)
    await session.commit()

    events = list(
        await session.scalars(
            select(RequestEvent)
            .where(RequestEvent.request_id == request.id)
            .order_by(RequestEvent.created_at)
        )
    )
    assert [e.to_status for e in events] == [
        RequestStatus.NEW.value,
        RequestStatus.CLAIMED.value,
    ]
    assert events[-1].actor_label == employee.full_name


async def test_upload_token_is_single_use_and_hashed(session, tenant, client, service):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()

    record, token = await issue_upload_token(session, request=request)
    await session.commit()

    assert token not in record.token_hash
    assert await resolve_upload_token(session, token) is not None

    record.used_at = record.expires_at
    await session.commit()
    assert await resolve_upload_token(session, token) is None


async def test_upload_token_refused_for_visit_only_service(
    session, tenant, client, visit_service
):
    request = await create_request(
        session, tenant=tenant, client=client, service=visit_service, channel=Channel.WIDGET
    )
    await session.commit()
    with pytest.raises(RequestError):
        await issue_upload_token(session, request=request)


async def test_ip_rate_counter(session, tenant, client, service):
    for _ in range(3):
        await create_request(
            session,
            tenant=tenant,
            client=client,
            service=service,
            channel=Channel.WIDGET,
            source_ip="10.0.0.1",
        )
    await session.commit()

    assert await count_recent_requests_from_ip(
        session, tenant_id=tenant.id, source_ip="10.0.0.1"
    ) == 3
    assert await count_recent_requests_from_ip(
        session, tenant_id=tenant.id, source_ip="10.0.0.2"
    ) == 0
