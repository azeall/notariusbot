import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TenantScoped, Timestamps, UUIDPrimaryKey
from app.models.enums import SubmissionMode


class Service(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Услуга нотариуса. Полностью редактируется владельцем через админку."""

    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_service_tenant_slug"),)

    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    submission_mode: Mapped[SubmissionMode] = mapped_column(
        Enum(SubmissionMode, name="submission_mode", native_enum=False, length=32),
        default=SubmissionMode.DOCUMENTS,
        nullable=False,
    )

    # Сколько занимает сам приём — из этого нарезаются слоты записи.
    visit_duration_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)

    # Срок оформления, который показываем клиенту.
    lead_time_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    price_note: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    # Синонимы для поиска: клиент пишет «на машину», а услуга называется
    # «Доверенность на распоряжение транспортным средством».
    keywords: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="services")  # noqa: F821
    documents: Mapped[list["ServiceDocument"]] = relationship(
        back_populates="service",
        cascade="all, delete-orphan",
        order_by="ServiceDocument.sort_order",
    )

    def checklist_snapshot(self) -> list[dict]:
        """Слепок перечня документов на текущий момент.

        Кладётся в заявку при создании: если нотариус потом отредактирует услугу,
        у клиента должен остаться тот список, который ему показали.
        """
        return [
            {
                "title": doc.title,
                "description": doc.description,
                "is_required": doc.is_required,
            }
            for doc in sorted(self.documents, key=lambda d: d.sort_order)
        ]

    def __repr__(self) -> str:
        return f"<Service {self.slug}>"


class ServiceDocument(Base, UUIDPrimaryKey, TenantScoped, Timestamps):
    """Один пункт перечня документов для услуги."""

    __tablename__ = "service_documents"

    service_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("services.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    service: Mapped[Service] = relationship(back_populates="documents")

    def __repr__(self) -> str:
        return f"<ServiceDocument {self.title!r}>"
