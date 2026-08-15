from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://notary:notarybot_dev@127.0.0.1:5432/notarybot"

    # Публичный адрес — из него собираются одноразовые ссылки на загрузку документов.
    public_base_url: str = "http://127.0.0.1:8000"

    # Ключ шифрования файлов в хранилище. В продакшне задаётся через окружение
    # и никогда не попадает в репозиторий.
    document_encryption_key: str = "dev-only-not-for-production"

    storage_dir: Path = PROJECT_ROOT / "storage"

    # Сколько живёт одноразовая ссылка на загрузку документов.
    upload_token_ttl_minutes: int = 30

    # Сколько документы хранятся после закрытия заявки, потом удаляются.
    document_retention_days: int = 90

    # Антиспам: сколько заявок можно создать с одного IP за час.
    requests_per_ip_per_hour: int = 5

    # Максимальный размер одного файла.
    max_upload_bytes: int = Field(default=20 * 1024 * 1024)

    allowed_upload_types: tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/heic",
    )

    session_secret: str = "dev-only-session-secret"


@lru_cache
def get_settings() -> Settings:
    return Settings()
