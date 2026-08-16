"""HTTP-клиент MAX Bot API.

Здесь собрано всё, что специфично для MAX: адреса методов и формат ответов.
Логика диалога лежит в app.channels.flow и от этого файла не зависит.

Сверено с dev.max.ru (август 2026):
  * база — platform-api.max.ru, у документации встречается и platform-api2,
    поэтому адрес вынесен в настройку MAX_API_BASE;
  * токен идёт заголовком Authorization, передача через query больше
    не поддерживается — на этом ломались старые примеры;
  * GET /updates — long polling, POST /messages — отправка;
  * события: message_created и message_callback.

Сами MAX предупреждают, что long polling не годится для продакшна (события
живут ограниченное время) и советуют вебхук. Для запуска этого достаточно,
но при боевой нагрузке канал стоит перевести на POST /subscriptions.
"""

from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings


@dataclass
class MaxUpdate:
    """Приведённое к общему виду входящее событие."""

    chat_id: str
    user_id: str
    text: str = ""
    payload: str = ""  # данные нажатой кнопки
    raw: dict[str, Any] | None = None

    @property
    def is_callback(self) -> bool:
        return bool(self.payload)


class MaxClient:
    def __init__(self, token: str | None = None, base_url: str | None = None) -> None:
        settings = get_settings()
        self.token = token or settings.max_bot_token
        self.base_url = (base_url or settings.max_api_base).rstrip("/")
        # Токен только заголовком: передача через query в MAX отключена.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(70.0),
            headers={"Authorization": self.token},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def updates(
        self, marker: int | None = None, timeout: int = 60
    ) -> tuple[list[MaxUpdate], int | None]:
        """Long polling. Возвращает события и маркер для следующего запроса."""
        params: dict[str, Any] = {"timeout": timeout}
        if marker is not None:
            params["marker"] = marker

        response = await self._client.get(f"{self.base_url}/updates", params=params)
        response.raise_for_status()
        body = response.json()

        parsed: list[MaxUpdate] = []
        for item in body.get("updates", []):
            parsed.append(self._parse_update(item))
        return [u for u in parsed if u is not None], body.get("marker")

    @staticmethod
    def _parse_update(item: dict[str, Any]) -> MaxUpdate | None:
        """Разбор события. Первое место, которое надо сверить с документацией."""
        kind = item.get("update_type")

        if kind == "message_created":
            message = item.get("message", {})
            recipient = message.get("recipient", {})
            sender = message.get("sender", {})
            return MaxUpdate(
                chat_id=str(recipient.get("chat_id", "")),
                user_id=str(sender.get("user_id", "")),
                text=(message.get("body", {}) or {}).get("text", "") or "",
                raw=item,
            )

        if kind == "message_callback":
            callback = item.get("callback", {})
            message = item.get("message", {})
            recipient = message.get("recipient", {})
            return MaxUpdate(
                chat_id=str(recipient.get("chat_id", "")),
                user_id=str((callback.get("user", {}) or {}).get("user_id", "")),
                payload=callback.get("payload", "") or "",
                raw=item,
            )

        return None

    async def send(
        self, chat_id: str, text: str, buttons: list[list[tuple[str, str]]] | None = None
    ) -> None:
        """Отправить сообщение. Кнопки — список рядов из пар (подпись, payload)."""
        body: dict[str, Any] = {"text": text}

        if buttons:
            body["attachments"] = [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": [
                            [
                                {"type": "callback", "text": caption, "payload": payload}
                                for caption, payload in row
                            ]
                            for row in buttons
                        ]
                    },
                }
            ]

        response = await self._client.post(
            f"{self.base_url}/messages",
            params={"chat_id": chat_id},
            json=body,
        )
        response.raise_for_status()
