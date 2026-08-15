import uuid
from pathlib import Path

from app.config import get_settings
from app.domain.security import decrypt_bytes, encrypt_bytes


class DocumentStorage:
    """Файловое хранилище документов клиентов.

    На диск ничего не попадает в открытом виде: шифруем до записи и расшифровываем
    только в момент выдачи сотруднику. Имя файла — случайный UUID, по нему нельзя
    понять ни клиента, ни содержимое.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_dir

    def _absolute(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        root = self.root.resolve()
        if not path.is_relative_to(root):
            raise ValueError("Путь выходит за пределы хранилища")
        return path

    def save(self, tenant_id: uuid.UUID, request_id: uuid.UUID, data: bytes) -> str:
        relative = f"{tenant_id}/{request_id}/{uuid.uuid4().hex}.enc"
        path = self._absolute(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encrypt_bytes(data))
        return relative

    def load(self, relative_path: str) -> bytes:
        return decrypt_bytes(self._absolute(relative_path).read_bytes())

    def delete(self, relative_path: str) -> bool:
        """Удаление по истечении срока хранения. Возвращает False, если файла уже нет."""
        path = self._absolute(relative_path)
        if not path.exists():
            return False
        path.unlink()
        return True

    def exists(self, relative_path: str) -> bool:
        return self._absolute(relative_path).exists()
