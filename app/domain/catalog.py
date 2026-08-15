import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Service

_WORD_RE = re.compile(r"[^\wёЁ]+", re.UNICODE)


def _normalize(text: str) -> list[str]:
    return [w for w in _WORD_RE.split(text.lower().replace("ё", "е")) if len(w) > 2]


async def list_services(session: AsyncSession, tenant_id: uuid.UUID) -> list[Service]:
    result = await session.scalars(
        select(Service)
        .where(Service.tenant_id == tenant_id, Service.is_active.is_(True))
        .options(selectinload(Service.documents))
        .order_by(Service.sort_order, Service.title)
    )
    return list(result)


async def get_service(
    session: AsyncSession, tenant_id: uuid.UUID, service_id: uuid.UUID
) -> Service | None:
    return await session.scalar(
        select(Service)
        .where(Service.id == service_id, Service.tenant_id == tenant_id)
        .options(selectinload(Service.documents))
    )


def score_service(service: Service, query: str) -> int:
    """Насколько услуга подходит под запрос клиента.

    Намеренно примитивный подсчёт совпадений по словам, без всякой модели:
    клиент всё равно подтверждает выбор кнопкой, а ошибка распознавания здесь
    означает, что человек приедет не с теми документами.
    """
    words = _normalize(query)
    if not words:
        return 0

    haystack = " ".join([service.title, service.description, *service.keywords])
    hay_words = set(_normalize(haystack))
    title_words = set(_normalize(service.title))

    score = 0
    for word in words:
        if word in title_words:
            score += 3
        elif word in hay_words:
            score += 2
        elif any(h.startswith(word[:4]) for h in hay_words):
            # Грубая замена морфологии: «доверенност(ь/и/ью)» → общий корень.
            score += 1
    return score


async def search_services(
    session: AsyncSession, tenant_id: uuid.UUID, query: str, limit: int = 5
) -> list[Service]:
    """Подобрать услуги под свободный текст клиента."""
    services = await list_services(session, tenant_id)
    scored = [(score_service(s, query), s) for s in services]
    matched = [(score, s) for score, s in scored if score > 0]
    matched.sort(key=lambda pair: (-pair[0], pair[1].sort_order))
    return [s for _, s in matched[:limit]]
