import uuid

import pytest
from pydantic import ValidationError

from app import legal
from app.domain.catalog import score_service
from app.models import Service
from app.web.schemas import RequestIn


def payload(**changes):
    return dict(service_id=uuid.uuid4(), full_name="Тестовый Клиент",
                phone="+79995550123", consent=True,
                consent_version=legal.CONSENT_VERSION,
                consent_fingerprint="a" * 64) | changes


@pytest.mark.parametrize("phone", ["314123412312", "+70000000000", "++79995550123",
                                  "+79995550123abc", "+7 123", "999999999999999999"])
def test_invalid_phone_is_rejected(phone):
    with pytest.raises(ValidationError):
        RequestIn(**payload(phone=phone))


@pytest.mark.parametrize(("phone", "expected"), [
    ("8 (999) 555-01-23", "+79995550123"),
    ("9995550123", "+79995550123"),
    ("+7 999 555-01-23", "+79995550123"),
    ("+49 30 12345678", "+493012345678"),
    ("+7 701 123-45-67", "+77011234567"),
])
def test_phone_is_normalized(phone, expected):
    assert RequestIn(**payload(phone=phone)).phone == expected


@pytest.mark.parametrize("name", ["   ", " A ", "12345", "Иван 123"])
def test_invalid_name_is_rejected(name):
    with pytest.raises(ValidationError):
        RequestIn(**payload(full_name=name))


def test_name_allows_hyphens_apostrophes_and_single_names():
    for name in ["Анна-Мария О'Коннор", "Ли", "  Иван   Иванов "]:
        assert RequestIn(**payload(full_name=name)).full_name == " ".join(name.split())


def test_certifying_a_copy_does_not_match_a_will():
    will = Service(title="Удостоверение завещания", description="",
                   keywords=["завещание", "наследство"])
    assert score_service(will, "заверить копию") == 0


@pytest.mark.parametrize(("query", "keyword"), [("машину", "машина"),
                                              ("копию", "копия"),
                                              ("доверенности", "доверенность")])
def test_search_keeps_inflected_words(query, keyword):
    service = Service(title="Услуга", description="", keywords=[keyword])
    assert score_service(service, query) > 0
