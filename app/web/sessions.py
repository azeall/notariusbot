"""Сессии кабинета: как выдаются, читаются и продлеваются.

Раньше вход держался ровно 12 часов, а потом слетал молча — нотариус
приходил утром и снова видел форму входа. Теперь три вещи.

Срок живёт в подписи, а не только в куке. Раньше подпись не проверялась
на давность: браузер можно попросить хранить куку сколько угодно, и
украденная жила бы вечно. Теперь сервер сам отказывает старой.

Сессия продлевается, пока человеком пользуются. Работал весь день —
отсчёт начинается заново, и посреди дела вас не выкинет.

«Запомнить меня» выбирает между двумя сроками. Общий компьютер в конторе
и личный ноутбук — разные истории, и решать должен тот, кто сидит
за клавиатурой, а не мы за него.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from fastapi import Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import get_settings

# Короткая — на чужом или общем компьютере. Длинная — «запомнить меня».
SHORT_TTL = 12 * 60 * 60
LONG_TTL = 30 * 24 * 60 * 60

# Насколько сессия должна «состариться», чтобы её продлевать. Обновлять куку
# на каждый запрос — лишние заголовки и записи; раз в сутки достаточно.
RENEW_AFTER = 24 * 60 * 60


@dataclass(frozen=True)
class Session:
    subject_id: uuid.UUID
    issued_at: int
    ttl: int

    @property
    def expires_at(self) -> int:
        return self.issued_at + self.ttl

    def needs_renewal(self, now: int) -> bool:
        """Пора ли продлить: сессия длинная и прожила больше суток."""
        return self.ttl >= LONG_TTL and now - self.issued_at > RENEW_AFTER


def _serializer(salt: str) -> URLSafeSerializer:
    return URLSafeSerializer(get_settings().session_secret, salt=salt)


def issue(subject_id: uuid.UUID, *, remember: bool, key: str = "staff_id") -> tuple[str, int]:
    """Собрать значение куки. Возвращает саму строку и срок жизни в секундах."""
    ttl = LONG_TTL if remember else SHORT_TTL
    payload = {key: str(subject_id), "iat": int(time.time()), "ttl": ttl}
    return _serializer(_salt_for(key)).dumps(payload), ttl


def read(raw: str, *, key: str = "staff_id") -> Session | None:
    """Разобрать куку. None — если подпись не сходится или срок вышел."""
    try:
        payload = _serializer(_salt_for(key)).loads(raw)
    except BadSignature:
        return None
    try:
        subject_id = uuid.UUID(payload[key])
        issued_at = int(payload["iat"])
        ttl = int(payload["ttl"])
    except (KeyError, ValueError, TypeError):
        # Куки, выданные прежней версией, полей срока не имеют. Считаем их
        # недействительными: пусть человек войдёт заново один раз, зато
        # дальше срок будет проверяться по-настоящему.
        return None

    if ttl not in (SHORT_TTL, LONG_TTL):
        return None
    if int(time.time()) > issued_at + ttl:
        return None
    return Session(subject_id=subject_id, issued_at=issued_at, ttl=ttl)


def attach(response: Response, name: str, value: str, ttl: int) -> None:
    """Положить куку в ответ.

    secure выводится из адреса сервиса: боевой работает по https, разработка
    по http. Без флага кука уйдёт по открытому каналу, а за ней стоит доступ
    к паспортам клиентов.
    """
    response.set_cookie(
        name,
        value,
        httponly=True,
        samesite="lax",
        secure=get_settings().use_secure_cookies,
        max_age=ttl,
        path="/",
    )


def _salt_for(key: str) -> str:
    # Соль разная у кабинета сотрудника и кабинета владельца: кука одного
    # не должна подходить к другому даже теоретически.
    return "platform-session" if key == "admin_id" else "staff-session"
