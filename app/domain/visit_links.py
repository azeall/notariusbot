"""Ссылка, по которой клиент сам переносит или отменяет запись.

Перенос сейчас — звонок в контору и правка руками. А это самое частое, что
происходит с записью после её создания: сдвинулась работа, заболел ребёнок,
не успел собрать документы. Если перенести нельзя, человек просто не придёт
и не позвонит — час приёма сгорает молча, и никто даже не узнает, что клиент
хотел прийти.

Ссылка подписанная, а не хранимая: в базе для неё ничего не заводится.
Одноразовой она быть не должна — переносить могут не один раз, — но и вечной
тоже: после приёма она ни к чему, и пусть перестаёт работать сама.
"""

import uuid

from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings

# Сколько ссылка живёт после выдачи. Записываются максимум за две недели
# вперёд, месяц покрывает это с запасом на перенос.
TTL_DAYS = 45
_MAX_AGE = TTL_DAYS * 24 * 60 * 60


def _serializer() -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt="visit-link")


def issue(request_id: uuid.UUID) -> str:
    return _serializer().dumps({"request_id": str(request_id)})


def read(token: str) -> uuid.UUID | None:
    """Разобрать ссылку. None — если подпись не сходится."""
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        return None
    try:
        return uuid.UUID(payload["request_id"])
    except (KeyError, ValueError, TypeError):
        return None


def url_for(base: str, request_id: uuid.UUID) -> str:
    return f"{base.rstrip('/')}/visit/{issue(request_id)}"
