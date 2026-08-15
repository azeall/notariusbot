"""Запуск MAX-бота:  python -m app.channels.max"""

import asyncio

from app.channels.max.bot import run

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
