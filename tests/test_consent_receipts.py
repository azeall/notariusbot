"""Согласие по заявке сохраняется даже после изменения карточки нотариуса."""

from datetime import UTC, datetime

from app.domain.requests import create_request
from app.models import Channel


def test_widget_requires_current_consent_version():
    import uuid

    import pytest
    from pydantic import ValidationError

    from app import legal
    from app.web.schemas import RequestIn

    payload = dict(service_id=uuid.uuid4(), full_name="Тестовый клиент",
                   phone="+79990000000", consent=True, consent_fingerprint="a" * 64)
    for version in (None, "2026-08-26"):
        candidate = {**payload, **({"consent_version": version} if version else {})}
        with pytest.raises(ValidationError):
            RequestIn(**candidate)
    assert RequestIn(**payload, consent_version=legal.CONSENT_VERSION).consent


async def test_request_keeps_original_consent_when_client_accepts_new_text(
    session, tenant, client, service
):
    receipt = {
        "version": "2026-08-26",
        "text": "Согласие оператору Иванову по адресу Москва, Тверская, 1",
        "operator": {"name": "Иванов", "address": "Москва, Тверская, 1"},
        "accepted_at": datetime(2026, 8, 26, tzinfo=UTC).isoformat(),
        "channel": "widget",
    }
    client.consent_receipt = receipt
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    client.consent_receipt = {**receipt, "text": "Новое согласие"}
    tenant.display_name = "Новый нотариус"
    await session.commit()
    await session.refresh(request)
    assert request.consent_receipt == receipt


async def test_legacy_client_does_not_receive_fabricated_consent(
    session, tenant, client, service
):
    request = await create_request(
        session, tenant=tenant, client=client, service=service, channel=Channel.WIDGET
    )
    await session.commit()
    await session.refresh(request)
    assert request.consent_receipt == {}


def test_widget_requires_valid_consent_fingerprint():
    import uuid

    import pytest
    from pydantic import ValidationError

    from app import legal
    from app.web.schemas import RequestIn

    payload = dict(service_id=uuid.uuid4(), full_name="Test Client",
                   phone="+79990000000", consent=True, consent_version=legal.CONSENT_VERSION)
    for fingerprint in (None, "", "a" * 63, "g" * 64):
        candidate = {**payload, **({"consent_fingerprint": fingerprint} if fingerprint is not None else {})}
        with pytest.raises(ValidationError):
            RequestIn(**candidate)


def test_fingerprint_covers_template_and_operator(monkeypatch):
    from app import legal
    from app.domain.consent import consent_fingerprint
    from app.models import Tenant

    tenant = Tenant(slug="ivanov", display_name="Ivanov", city="Moscow", address="1", phone="123")
    original = consent_fingerprint(tenant)
    for field in ("display_name", "city", "address", "phone"):
        previous = getattr(tenant, field)
        setattr(tenant, field, previous + " changed")
        assert consent_fingerprint(tenant) != original
        setattr(tenant, field, previous)
    original_text = legal.consent_text(tenant)
    monkeypatch.setattr(legal, "consent_text", lambda tenant: original_text + " updated")
    assert consent_fingerprint(tenant) != original
