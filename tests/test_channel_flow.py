"""Тесты логики диалога в мессенджерах.

Она общая для Telegram и MAX, поэтому проверяется один раз и без всякого
транспорта — ни токен, ни сеть не нужны.
"""

import pytest
from sqlalchemy import select

from app import legal
from app.channels import flow
from app.models import Channel, Client, Request, RequestStatus


def _accepted_draft(tenant, **kwargs):
    draft = flow.Draft(**kwargs)
    if draft.consent:
        flow.prepare_consent(tenant, draft)
        flow.accept_consent(draft, draft.extra["consent_fingerprint"][:32])
    return draft


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
    draft = _accepted_draft(tenant,
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
    draft = _accepted_draft(tenant,
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
    draft = _accepted_draft(tenant,
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

    draft = _accepted_draft(tenant,
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
    draft = _accepted_draft(tenant,
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
    draft = _accepted_draft(tenant,
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
    assert client.consent_text_version == legal.CONSENT_VERSION


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
        draft=_accepted_draft(tenant, **base),
    )
    await flow.submit(
        session,
        tenant=tenant,
        channel=Channel.MAX,
        external_id="1",
        draft=_accepted_draft(tenant, **base),
    )
    await session.commit()

    clients = list(await session.scalars(select(Client)))
    assert len(clients) == 2, "одинаковые id в разных мессенджерах — разные люди"


async def test_unknown_tenant_resolves_to_none(session):
    assert await flow.resolve_tenant(session, "нет-такого") is None


async def test_confirmation_text_mentions_upload_link(session, tenant, service):
    draft = _accepted_draft(tenant,
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


async def test_unbound_bot_consent_is_rejected_before_database_access():
    from unittest.mock import AsyncMock
    from app.models import Tenant

    tenant = Tenant(slug="ivanov", display_name="Ivanov", city="Moscow", address="1", phone="123")
    draft = flow.Draft(tenant_slug=tenant.slug, consent=True)
    db = AsyncMock()
    with pytest.raises(flow.FlowError, match="соглас"):
        await flow.submit(db, tenant=tenant, channel=Channel.TELEGRAM, external_id="1", draft=draft)
    db.scalar.assert_not_awaited()


def test_bot_prompt_binds_full_text_and_rejects_previous_button():
    from app.models import Tenant

    tenant = Tenant(slug="ivanov", display_name="Ivanov", city="Moscow", address="1", phone="123")
    draft = flow.Draft(tenant_slug=tenant.slug, consent=True)
    prompt = flow.prepare_consent(tenant, draft)
    assert legal.consent_text(tenant) in prompt
    assert draft.consent is False
    old_token = draft.extra["consent_fingerprint"][:32]
    tenant.address = "2"
    flow.prepare_consent(tenant, draft)
    with pytest.raises(flow.FlowError, match="соглас"):
        flow.accept_consent(draft, old_token)
    assert draft.consent is False
    flow.accept_consent(draft, draft.extra["consent_fingerprint"][:32])
    assert draft.consent is True


@pytest.mark.parametrize("channel", [Channel.TELEGRAM, Channel.MAX])
@pytest.mark.parametrize("change", ["operator", "template", "version", "missing"])
async def test_bot_rejects_stale_consent_before_writes(session, tenant, service, channel, change, monkeypatch):
    draft = _accepted_draft(tenant, tenant_slug=tenant.slug, service_id=service.id,
                            full_name="Test Client", phone="+79990000001", consent=True)
    if change == "operator":
        tenant.address = "New address"
        await session.commit()
    elif change == "template":
        original_text = legal.consent_text(tenant)
        monkeypatch.setattr(legal, "consent_text", lambda tenant: original_text + " updated")
    elif change == "version":
        draft.extra["consent_version"] = "old"
    else:
        draft.extra.clear()
    with pytest.raises(flow.FlowError, match="соглас"):
        await flow.submit(session, tenant=tenant, channel=channel, external_id="1", draft=draft)
    assert await session.scalar(select(Client)) is None
    assert await session.scalar(select(Request)) is None


@pytest.mark.parametrize("channel", [Channel.TELEGRAM, Channel.MAX])
async def test_bot_keeps_shown_receipt_and_existing_client_on_stale_retry(session, tenant, service, channel):
    draft = flow.Draft(tenant_slug=tenant.slug, service_id=service.id,
                       full_name="Original Client", phone="+79990000001")
    shown_text = legal.consent_text(tenant)
    assert shown_text in flow.prepare_consent(tenant, draft)
    flow.accept_consent(draft, draft.extra["consent_fingerprint"][:32])
    request, _ = await flow.submit(session, tenant=tenant, channel=channel, external_id="1", draft=draft)
    await session.commit()
    assert request.consent_receipt["text"] == shown_text
    tenant.display_name = "New operator"
    await session.commit()
    draft.full_name = "Changed client"
    with pytest.raises(flow.FlowError, match="соглас"):
        await flow.submit(session, tenant=tenant, channel=channel, external_id="1", draft=draft)
    client = await session.scalar(select(Client))
    assert client.full_name == "Original Client"
    assert client.consent_receipt["text"] == shown_text
    await session.refresh(request)
    assert request.consent_receipt["text"] == shown_text
    assert len(list(await session.scalars(select(Request)))) == 1


async def test_telegram_prompt_sends_full_text_and_persists_binding(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.channels.telegram import bot
    from app.models import Tenant

    tenant = Tenant(slug="ivanov", display_name="Ivanov", city="Moscow", address="A" * 4000, phone="123")
    draft = flow.Draft(tenant_slug=tenant.slug)
    state = SimpleNamespace(get_data=AsyncMock(return_value={"draft": draft.__dict__}),
                            update_data=AsyncMock(), set_state=AsyncMock())
    message = SimpleNamespace(answer=AsyncMock(return_value=SimpleNamespace(message_id=7)))
    db = AsyncMock()
    monkeypatch.setattr(bot, "get_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(bot, "_tenant", AsyncMock(return_value=tenant))
    monkeypatch.setattr(bot, "_drop_reply_keyboard", AsyncMock())
    await bot._accept_phone(message, state, "+79990000001")
    sent = message.answer.await_args_list
    assert legal.consent_text(tenant) in "".join(call.args[0] for call in sent)
    assert all(len(call.args[0]) <= 2000 for call in sent)
    saved = next(call.kwargs["draft"] for call in state.update_data.await_args_list if "draft" in call.kwargs)
    assert saved["extra"]["consent_version"] == legal.CONSENT_VERSION
    button = sent[-1].kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.callback_data == "consent:yes:" + saved["extra"]["consent_fingerprint"][:32]
    assert len(button.callback_data.encode()) <= 64
    state.set_state.assert_awaited_once_with(bot.Talk.giving_consent)
    callback = SimpleNamespace(data="consent:yes:" + "0" * 32, answer=AsyncMock())
    monkeypatch.setattr(bot, "_finish", AsyncMock())
    await bot.consent_given(callback, state)
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs["show_alert"] is True
    bot._finish.assert_not_awaited()
    callback.data = button.callback_data
    monkeypatch.setattr(flow, "get_service", AsyncMock(return_value=SimpleNamespace(submission_mode=bot.SubmissionMode.DOCUMENTS)))
    await bot.consent_given(callback, state)
    bot._finish.assert_awaited_once()


async def test_max_prompt_sends_full_text_and_rejects_old_button(monkeypatch):
    from unittest.mock import AsyncMock
    from app.channels.max import bot
    from app.models import Tenant

    tenant = Tenant(slug="ivanov", display_name="Ivanov", city="Moscow", address="A" * 4000, phone="123")
    client = AsyncMock()
    adapter = bot.MaxBot(client)
    state = bot.Session(step=bot.ENTERING_PHONE, draft=flow.Draft(tenant_slug=tenant.slug))
    db = AsyncMock()
    monkeypatch.setattr(bot, "get_sessionmaker", lambda: lambda: db)
    monkeypatch.setattr(flow, "resolve_tenant", AsyncMock(return_value=tenant))
    await adapter._on_text("1", state, "+79990000001")
    sent = client.send.await_args_list
    assert legal.consent_text(tenant) in "".join(call.args[1] for call in sent)
    assert all(len(call.args[1]) <= 2000 for call in sent)
    payload = sent[-1].kwargs["buttons"][0][0][1]
    assert payload == "consent:yes:" + state.draft.extra["consent_fingerprint"][:32]
    adapter._after_consent = AsyncMock()
    await adapter._on_button("1", state, "consent:yes:" + "0" * 32)
    adapter._after_consent.assert_not_awaited()
    assert state.draft.consent is False
    await adapter._on_button("1", state, payload)
    adapter._after_consent.assert_awaited_once()
    assert state.draft.consent is True
