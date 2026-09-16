"""Проверки текстов о персональных данных.

Смотрят не на вёрстку, а на то, из-за чего согласие перестаёт быть согласием:
не назван оператор, не перечислены данные, разошлись версии, политика закрыта
от того, кто как раз решает, присылать ли паспорт.
"""

import httpx
import pytest
from hashlib import sha256
from app.models import Channel, Client, Tenant
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import legal
from app.channels import flow
from app.domain.consent import record_consent
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


def test_recorded_consent_keeps_text_version_and_operator_snapshot():
    tenant = Tenant(slug="ivanov", display_name="Нотариус Иванов", city="Москва",
                    address="Тестовая, 1", phone="+79990000000")
    client = Client(channel=Channel.WIDGET)
    expected = legal.consent_text(tenant)
    record_consent(client, tenant)
    tenant.display_name = "Другой нотариус"
    tenant.address = "Другой адрес"

    assert client.consent_text_version == legal.CONSENT_VERSION
    assert client.consent_receipt["version"] == legal.CONSENT_VERSION
    assert client.consent_receipt["text"] == expected
    assert client.consent_receipt["operator"]["name"] == "Нотариус Иванов"
    assert client.consent_receipt["operator"]["address"] == "Москва, Тестовая, 1"
    assert client.consent_receipt["accepted_at"] == client.consent_given_at.isoformat()
    assert client.consent_receipt["channel"] == Channel.WIDGET.value


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

    Часть 3.1 статьи 21 152-ФЗ (введена 266-ФЗ от 14.07.2022) даёт оператору
    сутки на уведомление Роскомнадзора и трое — на результаты расследования.
    Обязанность нотариуса, а обнаруживает утечку сервис: если он не обязался
    известить немедленно, нотариус пропустит срок не по своей вине.
    """
    body = (await http.get(f"/{tenant.slug}/privacy")).text

    assert "частью 3.1 статьи 21" in body
    assert "266-ФЗ" in body
    assert "420-ФЗ" not in body
    assert "24 часов" in body
    assert "72 часов" in body
    assert "Роскомнадзор" in body
    assert "немедленно" in body


async def test_privacy_page_says_where_the_data_lies(http, tenant):
    """Нет неподтверждённого обещания отсутствия внешней передачи."""
    body = (await http.get(f"/{tenant.slug}/privacy")).text

    assert "в Российской Федерации" in body
    assert "трансграничной передачи нет" not in body.lower()
    assert "Telegram" in body
    assert "Vercel" in body


def test_changed_consent_has_new_version_and_legacy_template_is_unchanged(monkeypatch):
    tenant = Tenant(slug="archive-check", display_name="Нотариус Тест", city="Москва",
                    address="Тестовая, 1", phone="+79990000000")
    assert legal.CONSENT_VERSION == "2026-09-16"
    old = legal.consent_text(tenant, version="2026-08-26")
    assert sha256(old.encode()).hexdigest() == "696142d19d4b5d9a451a786334fd7f82729dc1abb50747de1f073dc3dd90cb6e"
    monkeypatch.setattr(legal, "PERSONAL_DATA_COLLECTED", ("changed",))
    monkeypatch.setattr(legal, "PROCESSING_PURPOSES", ("changed",))
    monkeypatch.setattr(legal, "PROCESSING_ACTIONS", ("changed",))
    monkeypatch.setattr(legal.Operator, "of", lambda tenant: None)
    assert legal.consent_text(tenant, version="2026-08-26") == old


def test_demo_names_real_operator_and_does_not_use_fictional_notary():
    tenant = Tenant(slug="demo", display_name="Вымышленный нотариус", city="Москва",
                    address="Вымышленный адрес", phone="000000")
    text = legal.consent_text(tenant)
    assert "Штыков Егор Дмитриевич" in text
    assert "772594573137" in text
    assert "negay2020@gmail.com" in text
    assert "Вымышленный нотариус" not in text
    assert "Вымышленный адрес" not in text


def test_current_consent_does_not_promise_total_automatic_deletion():
    tenant = Tenant(slug="ivanov", display_name="Нотариус Иванов", city="", address="", phone="")
    text = legal.consent_text(tenant)
    assert "карточки клиентов и заявок" in text
    assert "не удаляет" in text
    assert "никому" not in text
    assert "третьим лицам не передаёт" not in text


async def test_privacy_without_database_covers_demo_retention_and_incidents():
    from app.web.deps import resolve_tenant

    tenant = Tenant(slug="demo", display_name="Вымышленный нотариус", city="",
                    address="", phone="", widget_mode="dark", widget_accent="#b89a5a",
                    widget_font="sans")
    app.dependency_overrides[resolve_tenant] = lambda: tenant
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/demo/privacy")
        assert response.status_code == 200
        body = response.text
        assert "Штыков Егор Дмитриевич" in body
        assert "772594573137" in body
        assert "частью 3.1 статьи 21" in body
        assert "266-ФЗ" in body
        assert "24 часов" in body and "72 часов" in body
        assert "21.1" not in body and "420-ФЗ" not in body
        assert "Очистка файлов не удаляет карточки клиентов и заявок" in body
        assert "не очищает резервные" in body
        assert "Telegram" in body and "Vercel" in body
        assert "трансграничной передачи нет" not in body
    finally:
        app.dependency_overrides.pop(resolve_tenant, None)


async def test_privacy_page_speaks_about_cookies(http, tenant):
    """Сказано, что форма заявки не ставит cookie.

    Проверяющие ищут этот раздел в первую очередь, а клиент по молчанию
    вправе предположить худшее. Утверждение проверяемое: на страницах виджета
    и политики сервер не отдаёт ни одного Set-Cookie, они появляются только
    после входа сотрудника.
    """
    body = (await http.get(f"/{tenant.slug}/privacy")).text

    assert "cookie" in body.lower()
    assert "не использует" in body


async def test_widget_sets_no_cookies(http, tenant):
    """Страница виджета не ставит cookie — то, что обещано в политике."""
    response = await http.get(f"/widget/{tenant.slug}")

    assert response.status_code == 200
    assert "set-cookie" not in {k.lower() for k in response.headers}
