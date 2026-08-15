import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

PHONE_RE = re.compile(r"[^\d+]")


class DocumentOut(BaseModel):
    title: str
    description: str = ""
    is_required: bool = True


class ServiceOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str = ""
    submission_mode: str
    lead_time_note: str = ""
    price_note: str = ""
    visit_duration_minutes: int
    documents: list[DocumentOut] = Field(default_factory=list)


class SlotOut(BaseModel):
    starts_at: datetime
    label: str


class RequestIn(BaseModel):
    service_id: uuid.UUID
    full_name: str = Field(min_length=2, max_length=255)
    phone: str = Field(min_length=5, max_length=32)
    comment: str = Field(default="", max_length=2000)
    consent: bool
    slot: datetime | None = None
    # Скрытое поле-ловушка: люди его не видят и не заполняют, боты заполняют.
    website: str = ""

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        cleaned = PHONE_RE.sub("", value)
        digits = cleaned.lstrip("+")
        if len(digits) < 10:
            raise ValueError("Телефон выглядит неполным")
        return cleaned

    @field_validator("full_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not stripped:
            raise ValueError("Укажите имя")
        return stripped

    @field_validator("consent")
    @classmethod
    def consent_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Без согласия на обработку персональных данных заявку принять нельзя")
        return value


class RequestOut(BaseModel):
    id: uuid.UUID
    public_number: int
    status: str
    service_title: str
    submission_mode: str
    checklist: list[DocumentOut]
    upload_url: str | None = None
    appointment_at: datetime | None = None
