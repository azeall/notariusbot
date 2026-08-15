"""Удаление документов по истечении срока хранения.

Запуск вручную:  python -m app.retention
На сервере — по расписанию раз в сутки.

Содержимое файлов стирается, метаданные и журнал доступа остаются: по 152-ФЗ
оператор должен уметь показать, что данные удалены и кто их видел до этого.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.domain.storage import DocumentStorage
from app.models import Attachment, AuditLog, Request


async def purge_expired_documents(dry_run: bool = False) -> int:
    """Стереть файлы заявок, закрытых раньше срока хранения."""
    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.document_retention_days)
    storage = DocumentStorage()
    purged = 0

    maker = get_sessionmaker()
    async with maker() as session:
        attachments = list(
            await session.scalars(
                select(Attachment)
                .join(Request, Request.id == Attachment.request_id)
                .where(
                    Attachment.purged_at.is_(None),
                    Request.closed_at.is_not(None),
                    Request.closed_at < cutoff,
                )
            )
        )

        for attachment in attachments:
            if dry_run:
                purged += 1
                continue
            storage.delete(attachment.storage_path)
            attachment.purged_at = datetime.now(UTC)
            session.add(
                AuditLog(
                    tenant_id=attachment.tenant_id,
                    actor_label="Система",
                    action="document_purged",
                    object_type="attachment",
                    object_id=str(attachment.id),
                    details=f"срок хранения {settings.document_retention_days} дн.",
                )
            )
            purged += 1

        if not dry_run:
            await session.commit()

    return purged


async def main() -> None:
    count = await purge_expired_documents()
    print(f"Удалено документов: {count}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
