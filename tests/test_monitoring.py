"""Присмотр за сервисом.

Главное здесь не сами проверки, а то, когда сервис молчит. Оповещение,
которое приходит каждые пять минут, перестают читать на второй час —
и тогда его нет вовсе, хотя формально оно работает.
"""

import json

import pytest

from app import monitoring
from app.monitoring import Check


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """Своё место для памяти о прошлом состоянии, чтобы не трогать боевое."""
    path = tmp_path / "state.json"
    monkeypatch.setattr(monitoring, "STATE_FILE", path)
    return path


@pytest.fixture
def sent(monkeypatch):
    box: list[str] = []

    async def fake_notify(text: str) -> bool:
        box.append(text)
        return True

    monkeypatch.setattr(monitoring, "notify", fake_notify)
    return box


def _checks(monkeypatch, *results: tuple[str, bool]):
    async def fake_run():
        return [Check(name, ok, "подробность") for name, ok in results]

    monkeypatch.setattr(monitoring, "run_checks", fake_run)


async def test_silent_while_everything_works(monkeypatch, sent):
    _checks(monkeypatch, ("Сайт", True), ("База", True))
    assert await monitoring.watch() == 0
    assert sent == []


async def test_writes_once_when_something_breaks(monkeypatch, sent):
    _checks(monkeypatch, ("Сайт", False), ("База", True))
    assert await monitoring.watch() == 1
    assert len(sent) == 1
    assert "Сайт" in sent[0]


async def test_does_not_repeat_itself(monkeypatch, sent):
    """Поломка та же — второй раз не пишем.

    Иначе за ночь накопится сотня одинаковых сообщений, и утром их
    пролистают не читая — вместе с новыми.
    """
    _checks(monkeypatch, ("Сайт", False))
    await monitoring.watch()
    await monitoring.watch()
    await monitoring.watch()
    assert len(sent) == 1


async def test_reports_recovery(monkeypatch, sent):
    _checks(monkeypatch, ("Сайт", False))
    await monitoring.watch()
    _checks(monkeypatch, ("Сайт", True))
    await monitoring.watch()

    assert len(sent) == 2
    assert "Восстановилось" in sent[1]


async def test_new_failure_is_reported_even_if_another_persists(monkeypatch, sent):
    """Молчание про старую поломку не должно прятать новую."""
    _checks(monkeypatch, ("Сайт", False), ("База", True))
    await monitoring.watch()
    _checks(monkeypatch, ("Сайт", False), ("База", False))
    await monitoring.watch()

    assert len(sent) == 2
    assert "База" in sent[1]
    assert "Сайт" not in sent[1]


async def test_state_survives_between_runs(monkeypatch, sent, state_file):
    _checks(monkeypatch, ("Сайт", False))
    await monitoring.watch()
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"Сайт": False}


async def test_unwritable_state_does_not_silence_alerts(monkeypatch, sent, tmp_path):
    """Не смогли запомнить — не повод молчать о поломке."""
    monkeypatch.setattr(monitoring, "STATE_FILE", tmp_path / "нет" / "такого" / "s.json")
    monkeypatch.setattr(monitoring, "_save_state", lambda state: None)
    _checks(monkeypatch, ("Сайт", False))
    assert await monitoring.watch() == 1
    assert len(sent) == 1


def test_disk_check_reads_real_free_space():
    result = monitoring.check_disk()
    assert result.name == "Диск"
    assert "ГБ" in result.detail


async def test_notify_is_quiet_without_a_recipient(monkeypatch):
    """Не настроен получатель — молчим, а не падаем."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "alert_telegram_chat_id", "", raising=False)
    assert await monitoring.notify("проверка") is False
