import uuid

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey


class AuditLog(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Журнал доступа к персональным данным.

    Отдельно от RequestEvent: тот описывает жизнь заявки и виден сотрудникам,
    а этот фиксирует, кто открывал и скачивал документы клиентов. По 152-ФЗ
    оператор обязан уметь ответить на вопрос «кто видел этот паспорт».
    """

    __tablename__ = "audit_log"

    actor_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True
    )
    actor_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    source_ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    details: Mapped[str] = mapped_column(Text, default="", nullable=False)

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} {self.object_type}:{self.object_id}>"
