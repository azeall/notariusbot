"""Тесты логики диалога в мессенджерах.

Она общая для Telegram и MAX, поэтому проверяется один раз и без всякого
транспорта — ни токен, ни сеть не нужны.
"""

import pytest
from sqlalchemy import select

from app.channels import flow
from app.models import Channel, Client, Request, RequestStatus


async def test_render_service_lists_documents(session, tenant, service):
    text = flow.render_service(service)
    assert service.title in text
    assert "Паспорт доверителя" in text
    assert "Свидетельство о регистрации ТС" in text
    # Необязательный документ помечен иначе.
    assert "(если есть)" in text
    assert "документы можно прислать онлайн" in text


async def test_render_service_marks_visit_only(session, tenant, visit_service):
    assert "нужен личный визит" in flow.render_service(visit_service)


async def test_render_service_has_disclaimer(session, tenant, service):
    assert "подтверждает сотрудник нотариуса" in flow.render_service(service)


async def test_find_services_by_free_text(session, tenant, service, visit_service):
    found = await flow.find_services(session, tenant, "доверенность на машину")
    assert found and found[0].id == service.id


async def test_find_services_empty_query_returns_catalog(session, tenant, service, visit_service):
    assert len(await flow.find_services(session, tenant, "")) == 2


async def test_submit_creates_request_and_upload_link(session, tenant, service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    request, upload_url = await flow.submit(
        session,
        tenant=tenant,
        channel=Channel.TELEGRAM,
        external_id="7000001",
        draft=draft,
    )
    await session.commit()

    assert request.public_number == 1
    assert request.channel is Channel.TELEGRAM
    assert request.status is RequestStatus.NEW
    assert upload_url and "/upload/" in upload_url
    assert len(request.checklist) == 3


async def test_submit_without_consent_refused(session, tenant, service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=False,
    )
    with pytest.raises(flow.FlowError):
        await flow.submit(
            session, tenant=tenant, channel=Channel.TELEGRAM, external_id="7000001", draft=draft
        )


async def test_submit_visit_requires_slot(session, tenant, visit_service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=visit_service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    with pytest.raises(flow.FlowError):
        await flow.submit(
            session, tenant=tenant, channel=Channel.MAX, external_id="9000001", draft=draft
        )


async def test_submit_visit_books_slot(session, tenant, visit_service):
    slots = await flow.offered_slots(session, tenant, visit_service)
    assert slots

    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=visit_service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
        slot=slots[0][0],
    )
    request, upload_url = await flow.submit(
        session, tenant=tenant, channel=Channel.MAX, external_id="9000001", draft=draft
    )
    await session.commit()

    assert upload_url is None, "для визита ссылка на загрузку не нужна"
    assert request.preferred_time_note

    remaining = await flow.offered_slots(session, tenant, visit_service)
    assert slots[0][0] not in [m for m, _ in remaining]


async def test_client_is_reused_between_conversations(session, tenant, service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    for _ in range(2):
        await flow.submit(
            session,
            tenant=tenant,
            channel=Channel.TELEGRAM,
            external_id="7000001",
            draft=draft,
        )
    await session.commit()

    clients = list(await session.scalars(select(Client)))
    requests = list(await session.scalars(select(Request)))
    assert len(clients) == 1, "один и тот же человек не должен плодить карточки"
    assert len(requests) == 2


async def test_consent_is_recorded_on_client(session, tenant, service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    await flow.submit(
        session, tenant=tenant, channel=Channel.TELEGRAM, external_id="7000001", draft=draft
    )
    await session.commit()

    client = await session.scalar(select(Client))
    assert client.has_consent
    assert client.consent_text_version == flow.CONSENT_VERSION


async def test_channels_do_not_mix_clients(session, tenant, service):
    base = dict(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    await flow.submit(
        session,
        tenant=tenant,
        channel=Channel.TELEGRAM,
        external_id="1",
        draft=flow.Draft(**base),
    )
    await flow.submit(
        session,
        tenant=tenant,
        channel=Channel.MAX,
        external_id="1",
        draft=flow.Draft(**base),
    )
    await session.commit()

    clients = list(await session.scalars(select(Client)))
    assert len(clients) == 2, "одинаковые id в разных мессенджерах — разные люди"


async def test_unknown_tenant_resolves_to_none(session):
    assert await flow.resolve_tenant(session, "нет-такого") is None


async def test_confirmation_text_mentions_upload_link(session, tenant, service):
    draft = flow.Draft(
        tenant_slug=tenant.slug,
        service_id=service.id,
        full_name="Смирнов Алексей",
        phone="+79990000001",
        consent=True,
    )
    request, upload_url = await flow.submit(
        session, tenant=tenant, channel=Channel.TELEGRAM, external_id="7000001", draft=draft
    )
    await session.commit()

    text = flow.render_confirmation(request, upload_url, tenant.timezone)
    assert f"№ {request.public_number}" in text
    assert upload_url in text
    assert "30 минут" in text
    assert "догрузить" in text
