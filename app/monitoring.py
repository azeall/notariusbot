"""Присмотр за сервисом: что проверяем и когда будим.

Запуск по расписанию, раз в несколько минут:  python -m app.monitoring
Разовая проверка с выводом:                    python -m app.monitoring --show

За сервисом не смотрел никто. Упади он ночью — узнали бы утром от нотариуса,
у которого клиент не смог отправить заявку. Это худший способ узнать: первое
впечатление второй раз не производят.

Проверка живёт на том же сервере намеренно. Внешний присмотр надёжнее —
он заметит и смерть самой машины, — но требует стороннего сервиса и денег.
А отказывает на практике не железо, а приложение: упал процесс, кончилось
место, база перестала отвечать. Это ловится и изнутри, бесплатно и сегодня.

Пишем только о смене состояния. Сообщение каждые пять минут о том, что всё
плохо, читать перестают на второй час, и тогда оповещение не работает вовсе.
"""

import argparse
import asyncio
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import text

from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker

# Где помним прошлое состояние, чтобы не повторяться.
STATE_FILE = Path("/var/lib/notarybot/monitoring-state.json")

# Ниже этого места на диске — тревога. Хранилище документов растёт молча,
# а кончившееся место кладёт и базу, и приём файлов разом.
MIN_FREE_GB = 2.0

# Резервная копия снимается раз в сутки. Двое суток без неё — что-то сломалось
# в самом снятии, и об этом лучше узнать до того, как копия понадобится.
BACKUP_MAX_AGE_HOURS = 48

BACKUP_DIR = Path("/var/backups/notarybot")


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


async def check_web() -> Check:
    url = get_settings().public_base_url.rstrip("/") + "/healthz"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
        if response.status_code == 200:
            return Check("Сайт", True, "отвечает")
        return Check("Сайт", False, f"отвечает {response.status_code}")
    except Exception as exc:
        return Check("Сайт", False, f"не отвечает: {type(exc).__name__}")


async def check_database() -> Check:
    try:
        maker = get_sessionmaker()
        async with maker() as session:
            await session.execute(text("SELECT 1"))
        return Check("База", True, "отвечает")
    except Exception as exc:
        return Check("База", False, f"не отвечает: {type(exc).__name__}")


def check_disk() -> Check:
    try:
        free_gb = shutil.disk_usage("/").free / (1024**3)
    except OSError as exc:
        return Check("Диск", False, f"не прочитать: {exc}")
    if free_gb < MIN_FREE_GB:
        return Check("Диск", False, f"осталось {free_gb:.1f} ГБ")
    return Check("Диск", True, f"свободно {free_gb:.0f} ГБ")


def check_backup() -> Check:
    """Свежесть последней копии.

    Копия, которой нет, обнаруживается ровно в тот момент, когда она нужна, —
    то есть в худший из возможных.
    """
    if not BACKUP_DIR.exists():
        return Check("Копии", False, "каталог не найден")
    dumps = sorted(BACKUP_DIR.glob("db_*.dump"), key=lambda p: p.stat().st_mtime)
    if not dumps:
        return Check("Копии", False, "ни одной копии")
    newest = datetime.fromtimestamp(dumps[-1].stat().st_mtime, UTC)
    hours = (datetime.now(UTC) - newest).total_seconds() / 3600
    if hours > BACKUP_MAX_AGE_HOURS:
        return Check("Копии", False, f"последней {hours:.0f} ч назад")
    return Check("Копии", True, f"последняя {hours:.0f} ч назад")


async def run_checks() -> list[Check]:
    return [
        await check_web(),
        await check_database(),
        check_disk(),
        check_backup(),
    ]


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        # Не смогли запомнить — не повод молчать о поломке. В худшем случае
        # напишем о ней ещё раз.
        pass


def compose(broken: list[Check], recovered: list[Check]) -> str:
    lines: list[str] = []
    if broken:
        lines.append("Сервис заявок: неполадка")
        lines += [f"— {c.name}: {c.detail}" for c in broken]
    if recovered:
        if lines:
            lines.append("")
        lines.append("Восстановилось:")
        lines += [f"— {c.name}: {c.detail}" for c in recovered]
    return "\n".join(lines)


async def notify(text_message: str) -> bool:
    """Написать владельцу. Без настроенного получателя молча ничего не делаем."""
    settings = get_settings()
    chat_id = settings.alert_telegram_chat_id
    if not chat_id or not settings.telegram_bot_token:
        return False
    from app.notifications import _send

    return await _send(chat_id, text_message)


async def watch(show: bool = False) -> int:
    """Проверить и написать, если состояние изменилось. Вернуть число поломок."""
    checks = await run_checks()
    previous = _load_state()

    broken = [c for c in checks if not c.ok and previous.get(c.name, True)]
    recovered = [c for c in checks if c.ok and previous.get(c.name, True) is False]

    if show:
        for c in checks:
            print(f"{'ок ' if c.ok else 'ПЛОХО'} {c.name}: {c.detail}")

    if broken or recovered:
        await notify(compose(broken, recovered))

    _save_state({c.name: c.ok for c in checks})
    return sum(1 for c in checks if not c.ok)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Присмотр за сервисом")
    parser.add_argument("--show", action="store_true", help="напечатать состояние проверок")
    args = parser.parse_args()

    failed = await watch(show=args.show)
    await dispose_engine()
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
