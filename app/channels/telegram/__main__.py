"""Запуск Telegram-бота:  python -m app.channels.telegram"""

import asyncio

from app.channels.telegram.bot import run

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
