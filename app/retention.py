"""Удаление документов по истечении срока хранения.

Запуск вручную:      python -m app.retention
Проверка вхолостую:  python -m app.retention --dry-run
На сервере — по расписанию раз в сутки.

Правил два, и второе важнее первого.

Первое: заявка закрыта — файлы стираются через document_retention_days.
Это обычный ход дела.

Второе: файл старше document_max_age_days стирается независимо от того,
что стало с заявкой. Без него хранение висело на том, что сотрудник нажмёт
«выполнено»: заявку забыли закрыть — и паспорта лежали вечно, причём молча,
потому что ничего не ломалось. Оператор обязан не хранить данные дольше
нужного, и «сотрудник забыл» не оправдание.

Содержимое файлов стирается, метаданные и журнал доступа остаются: по
152-ФЗ оператор должен уметь показать, что именно удалено и кто это видел
до удаления. Пустой журнал — это не «мы чисты», а «мы не знаем».
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.domain.storage import DocumentStorage
from app.models import Attachment, AuditLog, Request


async def purge_expired_documents(
    dry_run: bool = False,
    session: AsyncSession | None = None,
) -> int:
    """Стереть файлы, у которых вышел срок хранения. Вернуть число стёртых.

    session можно передать снаружи. Это нужно тестам: заводя собственное
    подключение, функция переживает цикл событий теста и роняет соседние.
    Из командной строки session не передают — тогда подключение своё.
    """
    if session is not None:
        return await _purge(session, dry_run)

    maker = get_sessionmaker()
    async with maker() as own:
        return await _purge(own, dry_run)


async def _purge(session: AsyncSession, dry_run: bool) -> int:
    settings = get_settings()
    now = datetime.now(UTC)
    closed_cutoff = now - timedelta(days=settings.document_retention_days)
    age_cutoff = now - timedelta(days=settings.document_max_age_days)

    storage = DocumentStorage()
    purged = 0

    attachments = list(
        await session.scalars(
            select(Attachment)
            .join(Request, Request.id == Attachment.request_id)
            .where(
                Attachment.purged_at.is_(None),
                or_(
                    # заявка закрыта, и срок после закрытия вышел
                    Request.closed_at.is_not(None) & (Request.closed_at < closed_cutoff),
                    # либо файл просто слишком старый — что бы ни было с заявкой
                    Attachment.created_at < age_cutoff,
                ),
            )
        )
    )

    for attachment in attachments:
        if dry_run:
            purged += 1
            continue

        overdue = attachment.created_at < age_cutoff
        reason = (
            f"предельный срок {settings.document_max_age_days} дн. с загрузки"
            if overdue
            else f"срок хранения {settings.document_retention_days} дн. после закрытия"
        )

        storage.delete(attachment.storage_path)
        attachment.purged_at = now
        session.add(
            AuditLog(
                tenant_id=attachment.tenant_id,
                actor_label="Система",
                action="document_purged",
                object_type="attachment",
                object_id=str(attachment.id),
                details=reason,
            )
        )
        purged += 1

    if not dry_run:
        await session.commit()

    return purged


async def main() -> None:
    parser = argparse.ArgumentParser(description="Удаление документов по сроку хранения")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только посчитать, ничего не удалять",
    )
    args = parser.parse_args()

    count = await purge_expired_documents(dry_run=args.dry_run)
    if args.dry_run:
        print(f"Под удаление попадает документов: {count} (ничего не удалено)")
    else:
        print(f"Удалено документов: {count}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
