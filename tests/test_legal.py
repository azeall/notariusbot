"""Проверки текстов о персональных данных.

Смотрят не на вёрстку, а на то, из-за чего согласие перестаёт быть согласием:
не назван оператор, не перечислены данные, разошлись версии, политика закрыта
от того, кто как раз решает, присылать ли паспорт.
"""

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import legal
from app.channels import flow
from app.web import widget as widget_api
from app.web.deps import db_session
from app.web.main import app


@pytest.fixture
async def http(engine, session):
    """Клиент к приложению поверх тестового движка.

    Свой движок приложение завело бы в другом event loop, и соединения
    развалились бы посреди теста. Страница политики базу трогает: она ищет
    нотариуса по slug, чтобы назвать оператора.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with maker() as request_session:
            yield request_session

    app.dependency_overrides[db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def test_consent_version_is_declared_once():
    """Версия согласия одна на все каналы.

    Строка была объявлена дважды — в виджете и в разговоре мессенджеров. Правка
    одного места молча расходилась со вторым, и запись в базе начинала ссылаться
    на версию, которой человек не видел. Доказать, на что он согласился, после
    этого нечем.
    """
    assert widget_api.CONSENT_VERSION is legal.CONSENT_VERSION
    assert flow.CONSENT_VERSION is legal.CONSENT_VERSION


def test_consent_names_the_notary_as_operator(tenant):
    """Оператор — нотариус, а не сервис.

    Сервис обрабатывает данные по поручению (ч. 3 ст. 6 152-ФЗ). Назвать
    оператором себя — значит принять на себя его обязанности и вдобавок сказать
    клиенту неправду о том, кому он доверяет паспорт.
    """
    text = legal.consent_text(tenant)
    assert tenant.display_name in text
    assert "по поручению" in text


def test_consent_lists_data_purposes_and_withdrawal(tenant):
    """В тексте есть то, без чего согласие недействительно."""
    text = legal.consent_text(tenant)

    for item in legal.PERSONAL_DATA_COLLECTED:
        assert item in text, f"в согласии не назван перечень: {item}"
    for purpose in legal.PROCESSING_PURPOSES:
        assert purpose in text, f"в согласии не названа цель: {purpose}"

    assert "отозвать" in text, "не сказано, что согласие можно отозвать"
    assert "Срок действия согласия" in text


def test_consent_mentions_documents_because_that_is_what_hurts(tenant):
    """Перечень обязан упоминать сами документы, а не только имя и телефон.

    Через сервис ходят паспорта. Согласие, где перечислены «имя и телефон»,
    получено не на то, что происходит на самом деле.
    """
    assert "паспортные данные" in legal.consent_text(tenant)


def test_unknown_consent_version_is_refused(tenant):
    """Неизвестную версию нельзя молча подменить текущей.

    Иначе старое согласие показывалось бы сегодняшним текстом — то есть
    доказательство подменялось бы задним числом.
    """
    with pytest.raises(KeyError):
        legal.consent_text(tenant, version="1999-01-01")


def test_consent_prompt_carries_a_link_to_the_full_text():
    """В мессенджере выжимка, но со ссылкой на полный текст.

    Выжимка без ссылки — согласие вслепую: оператор не назван, перечень данных
    не приведён, и подтверждать нечего.
    """
    prompt = flow.ask_consent("ivanov")
    assert "/ivanov/privacy" in prompt


async def test_privacy_page_is_open_without_login(http, tenant):
    """Политика доступна без входа: ч. 2 ст. 18.1 требует свободного доступа.

    Человек читает её до того, как что-либо отправит, — значит и до всякого входа.
    """
    response = await http.get(f"/{tenant.slug}/privacy")

    assert response.status_code == 200
    body = response.text
    assert tenant.display_name in body
    assert "Ваши права" in body
    assert "отозвать согласие" in body


async def test_privacy_page_states_retention_in_days(http, tenant):
    """Срок хранения на странице — из настроек, а не из головы.

    Написать в политике один срок, а стирать по другому — это обещание,
    которого никто не выполняет, и первый же вопрос проверяющего его вскроет.
    """
    from app.config import get_settings

    response = await http.get(f"/{tenant.slug}/privacy")
    assert f"через {get_settings().document_retention_days} дней после закрытия" in response.text


async def test_api_schema_is_closed_in_production(monkeypatch):
    """На боевом /docs, /redoc и /openapi.json не отдаются.

    Они показывали все 57 эндпоинтов вместе со схемами данных — включая
    удаление конторы, выдачу приглашений и работу с заявками. Маршруты
    защищены входом, но карта раздавалась без него: нападающему оставалось
    подбирать пароль, а не изучать сервис.

    Признак боевого — https в публичном адресе, тот же, по которому куки
    получают флаг secure. Второй переключатель однажды забыли бы переставить.
    """
    from app.config import get_settings
    from app.web.main import create_app

    settings = get_settings()

    monkeypatch.setattr(settings, "public_base_url", "https://app.example.ru", raising=False)
    production = create_app()
    assert production.docs_url is None
    assert production.redoc_url is None
    assert production.openapi_url is None

    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8000", raising=False)
    local = create_app()
    assert local.docs_url == "/docs", "на разработке схема нужна"


async def test_privacy_page_explains_what_happens_after_a_leak(http, tenant):
    """На странице есть порядок действий при утечке и сроки 24/72 часа.

    Статья 21.1 152-ФЗ (внесена 420-ФЗ, действует с 30.05.2025) даёт оператору
    сутки на уведомление Роскомнадзора и трое — на результаты расследования.
    Обязанность нотариуса, а обнаруживает утечку сервис: если он не обязался
    известить немедленно, нотариус пропустит срок не по своей вине.
    """
    body = (await http.get(f"/{tenant.slug}/privacy")).text

    assert "21.1" in body
    assert "24 часов" in body
    assert "72 часов" in body
    assert "Роскомнадзор" in body
    assert "немедленно" in body


async def test_privacy_page_says_where_the_data_lies(http, tenant):
    """Сказано, что данные не уходят за границу.

    Трансграничная передача (ст. 12) требует отдельного согласия и уведомления
    Роскомнадзора. Молчание об этом читается проверяющим не в пользу оператора,
    а клиент по молчанию не может понять, где его паспорт.
    """
    body = (await http.get(f"/{tenant.slug}/privacy")).text

    assert "в Российской Федерации" in body
    assert "трансграничной передачи нет" in body.lower()


def test_policy_version_moves_without_dragging_consent_along():
    """Правка политики не поднимает версию согласия.

    В базе хранится версия текста согласия. Поднять её без изменения текста —
    значит сослать старые записи на редакцию, которой человек не видел.
    """
    assert legal.POLICY_VERSION != legal.CONSENT_VERSION
    assert legal.CONSENT_VERSION == "2026-08-26"
