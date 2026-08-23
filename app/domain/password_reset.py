"""Сброс пароля по одноразовой ссылке.

Забытый пароль случается на первой же неделе: человек заводит его один раз
и не вспоминает к следующему входу. Пока сброса нет, единственный выход —
звонок разработчику, и каждый такой звонок показывает, что сервис держится
на одном человеке. Нотариус покупает как раз обратное.

Почты у сервиса нет, и заводить её ради одной задачи — лишний узел, который
однажды отвалится молча, причём заметят это в худший момент. Поэтому ссылку
выдаёт тот, кто и так отвечает за доступ: нотариус — своим сотрудникам,
владелец сервиса — нотариусу. Это ещё и надёжнее почты: канал уже
установлен и доверен.

В базе лежит только отпечаток ссылки. Открытый вид существует один раз —
на экране у того, кто её выдал.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.security import generate_token, hash_token
from app.models import Staff

# Час — намеренно мало. Ссылку передают из рук в руки или в мессенджере
# и используют сразу; долгоживущая просто болтается в переписке.
TTL = timedelta(hours=1)


async def issue(session: AsyncSession, staff: Staff) -> str:
    """Выдать ссылку. Прежняя, если была, перестаёт работать."""
    token = generate_token()
    staff.reset_token_hash = hash_token(token)
    staff.reset_expires_at = datetime.now(UTC) + TTL
    await session.flush()
    return token


async def resolve(session: AsyncSession, token: str) -> Staff | None:
    """Найти, кому принадлежит ссылка. None — если её нет или срок вышел."""
    if not token:
        return None
    staff = await session.scalar(
        select(Staff).where(Staff.reset_token_hash == hash_token(token))
    )
    if staff is None or not staff.is_active:
        return None
    if staff.reset_expires_at is None or staff.reset_expires_at < datetime.now(UTC):
        return None
    return staff


def consume(staff: Staff) -> None:
    """Погасить ссылку после смены пароля: она одноразовая."""
    staff.reset_token_hash = None
    staff.reset_expires_at = None
