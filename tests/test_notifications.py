"""Новые заявки не раскрывают персональные данные в Telegram."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import notifications
from app.config import get_settings
from app.models import Request, SubmissionMode


@pytest.fixture
def request_record():
    return Request(
        public_number=42,
        service_title="Тайное завещание",
        submission_mode=SubmissionMode.VISIT,
        preferred_time_note="Позвоните супруге +79995554433",
        client_comment="Паспорт 1234 567890",
    )


def test_notification_contains_only_number_and_authenticated_cabinet(monkeypatch, request_record):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://app.example.ru/")
    assert notifications.render_new_request(request_record, "Иван Секретов", "+79991112233") == (
        "Новая заявка № 42\nКабинет (требуется вход): https://app.example.ru/staff"
    )


@pytest.mark.parametrize("base", [
    "https://user:bearer-secret@app.example.ru",
    "https://app.example.ru?token=bearer-secret",
    "https://app.example.ru#bearer-secret",
])
def test_notification_does_not_forward_credentials_in_configured_url(monkeypatch, request_record, base):
    monkeypatch.setattr(get_settings(), "public_base_url", base)
    text = notifications.render_new_request(request_record, "Имя", "Телефон")
    assert "bearer-secret" not in text
    assert "Новая заявка № 42" in text


async def test_delivery_uses_minimal_message(monkeypatch, request_record):
    monkeypatch.setattr(get_settings(), "public_base_url", "https://app.example.ru")
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "test-token")
    session = SimpleNamespace(scalars=AsyncMock(return_value=[
        SimpleNamespace(telegram_chat_id="staff-1"),
        SimpleNamespace(telegram_chat_id="staff-2"),
    ]))
    send = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(notifications, "_send", send)

    assert await notifications.notify_new_request(
        session, request=request_record, client_name="Иван Секретов", client_phone="+79991112233"
    ) == 1
    assert send.await_count == 2
    for call in send.await_args_list:
        assert call.args[1] == (
            "Новая заявка № 42\nКабинет (требуется вход): https://app.example.ru/staff"
        )


async def test_disabled_notification_does_not_query_database(monkeypatch, request_record):
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "")
    session = SimpleNamespace(scalars=AsyncMock())
    assert await notifications.notify_new_request(
        session, request=request_record, client_name="Имя", client_phone="Телефон"
    ) == 0
    session.scalars.assert_not_awaited()
