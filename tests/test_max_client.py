"""Проверка транспорта MAX без обращения к их серверам.

Ловим ровно то, на чём ломались старые примеры: токен обязан идти заголовком,
а не query-параметром, и разбор событий должен понимать оба типа.
"""

import httpx
import pytest

from app.channels.max.client import MaxClient


def _client_with(handler) -> MaxClient:
    client = MaxClient(token="test-token", base_url="https://platform-api.example")
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "test-token"},
    )
    return client


async def test_token_goes_in_header_not_query():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"updates": [], "marker": 5})

    client = _client_with(handler)
    await client.updates()
    await client.close()

    assert seen["auth"] == "test-token"
    assert "access_token" not in seen["url"], "передача токена через query отключена в MAX"


async def test_marker_is_passed_through():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"updates": [], "marker": 9})

    client = _client_with(handler)
    _, marker = await client.updates(marker=7)
    await client.close()

    assert "marker=7" in seen["url"]
    assert marker == 9


async def test_message_created_is_parsed():
    payload = {
        "updates": [
            {
                "update_type": "message_created",
                "message": {
                    "recipient": {"chat_id": 1001},
                    "sender": {"user_id": 2002},
                    "body": {"text": "нужна доверенность"},
                },
            }
        ],
        "marker": 1,
    }
    client = _client_with(lambda r: httpx.Response(200, json=payload))
    updates, _ = await client.updates()
    await client.close()

    assert len(updates) == 1
    assert updates[0].chat_id == "1001"
    assert updates[0].user_id == "2002"
    assert updates[0].text == "нужна доверенность"
    assert not updates[0].is_callback


async def test_message_callback_is_parsed():
    payload = {
        "updates": [
            {
                "update_type": "message_callback",
                "callback": {"payload": "svc:123", "user": {"user_id": 2002}},
                "message": {"recipient": {"chat_id": 1001}},
            }
        ],
        "marker": 2,
    }
    client = _client_with(lambda r: httpx.Response(200, json=payload))
    updates, _ = await client.updates()
    await client.close()

    assert updates[0].is_callback
    assert updates[0].payload == "svc:123"


async def test_unknown_update_types_are_skipped():
    payload = {"updates": [{"update_type": "bot_started"}], "marker": 3}
    client = _client_with(lambda r: httpx.Response(200, json=payload))
    updates, _ = await client.updates()
    await client.close()
    assert updates == []


async def test_send_builds_inline_keyboard():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"message": {}})

    client = _client_with(handler)
    await client.send("1001", "Выберите услугу", buttons=[[("Доверенность", "svc:1")]])
    await client.close()

    assert "chat_id=1001" in seen["url"]
    assert seen["body"]["text"] == "Выберите услугу"
    attachment = seen["body"]["attachments"][0]
    assert attachment["type"] == "inline_keyboard"
    button = attachment["payload"]["buttons"][0][0]
    assert button == {"type": "callback", "text": "Доверенность", "payload": "svc:1"}


async def test_send_without_buttons_has_no_attachments():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {}})

    client = _client_with(handler)
    await client.send("1001", "Заявка принята")
    await client.close()

    assert "attachments" not in seen["body"]


async def test_server_error_is_raised():
    client = _client_with(lambda r: httpx.Response(500, text="oops"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.updates()
    await client.close()


def test_client_does_not_take_proxy_from_the_machine():
    """Клиент не подхватывает прокси из окружения машины.

    На домашней машине системным прокси стоял socks4, и клиент не создавался
    вовсе: httpx такой схемы не знает без отдельного пакета. Восемь тестов
    падали, не дойдя до кода. Но опаснее обратное — если бы схема была
    поддержана: трафик бота вместе с токеном и данными клиентов нотариуса
    молча пошёл бы через чужой прокси.
    """
    client = MaxClient(token="test-token", base_url="https://platform-api.example")
    assert client._client.trust_env is False
