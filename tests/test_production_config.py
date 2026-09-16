"""Production startup and embed message security; no DB fixtures or real .env."""
import importlib
import json
import shutil
import subprocess
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import Settings, get_settings

SESSION_KEY = "e61af47c82905bd306eec4519734adc86bf273a9516d780cea59fd47203c168b"
DOCUMENT_KEY = "bf31749d5e802ac674a8f01529cdbf37802e61d947e536b0ca59f87d14263eab"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    # main creates an app when imported. Disable dotenv before importing it,
    # and keep the real cached getter so importing cannot leak a test lambda.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("SESSION_SECRET", SESSION_KEY)
    monkeypatch.setenv("DOCUMENT_ENCRYPTION_KEY", DOCUMENT_KEY)
    monkeypatch.delenv("COOKIES_SECURE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def settings_for(tmp_path, **changes):
    values = {
        "public_base_url": "https://app.example.ru",
        "session_secret": SESSION_KEY,
        "document_encryption_key": DOCUMENT_KEY,
        "cookies_secure": None,
        "storage_dir": tmp_path / "uncreated-storage",
    }
    values.update(changes)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("field", ["session_secret", "document_encryption_key"])
@pytest.mark.parametrize("value", [
    "", "short-secret", "dev-only-session-secret", "dev-only-not-for-production",
    "a" * 64, "0123456789abcdef" * 4,
])
async def test_public_startup_rejects_weak_keys_before_any_side_effect(tmp_path, monkeypatch, field, value):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path, **{field: value})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    dispose = AsyncMock()
    monkeypatch.setattr(main, "dispose_engine", dispose)
    with pytest.raises(RuntimeError, match=field.upper()) as failure:
        async with main.lifespan(FastAPI()):
            pass
    if value:
        assert value not in str(failure.value), "Do not print secrets in startup errors"
    assert not settings.storage_dir.exists()
    dispose.assert_not_awaited()


@pytest.mark.parametrize("field", ["SESSION_SECRET", "DOCUMENT_ENCRYPTION_KEY"])
async def test_missing_production_key_does_not_use_development_default(tmp_path, monkeypatch, field):
    main = importlib.import_module("app.web.main")
    monkeypatch.delenv(field, raising=False)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.ru")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "missing-key-storage"))
    get_settings.cache_clear()
    monkeypatch.setattr(main, "get_settings", get_settings)
    monkeypatch.setattr(main, "dispose_engine", AsyncMock())
    with pytest.raises(RuntimeError, match=field):
        async with main.lifespan(FastAPI()):
            pass


@pytest.mark.parametrize("url,cookies", [
    ("https://app.example.ru", False), ("https://localhost:8000", False),
    ("http://app.example.ru", True), ("http://192.168.1.20:8000", True),
])
async def test_public_startup_rejects_insecure_transport(tmp_path, monkeypatch, url, cookies):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path, public_base_url=url, cookies_secure=cookies)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "dispose_engine", AsyncMock())
    with pytest.raises(RuntimeError, match="HTTPS|COOKIES_SECURE"):
        async with main.lifespan(FastAPI()):
            pass
    assert not settings.storage_dir.exists()


@pytest.mark.parametrize("url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000"])
async def test_loopback_http_keeps_local_development_working(tmp_path, monkeypatch, url):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path, public_base_url=url, session_secret="dev-only-session-secret",
                            document_encryption_key="dev-only-not-for-production", cookies_secure=False)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    dispose = AsyncMock()
    monkeypatch.setattr(main, "dispose_engine", dispose)
    async with main.lifespan(FastAPI()):
        assert settings.storage_dir.is_dir()
    dispose.assert_awaited_once()


async def test_strong_public_configuration_starts_and_health_endpoint_is_available(tmp_path, monkeypatch):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "dispose_engine", AsyncMock())
    app = main.create_app()
    async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app), base_url="https://app.example.ru") as client:
        assert (await client.get("/healthz")).json() == {"status": "ok"}
        assert (await client.get("/docs")).status_code == 404
    assert settings.use_secure_cookies is True


async def test_fastapi_lifespan_prevents_serving_with_insecure_configuration(tmp_path, monkeypatch):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path, session_secret="dev-only-session-secret")
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "dispose_engine", AsyncMock())
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        app = main.create_app()
        async with app.router.lifespan_context(app):
            pytest.fail("An insecure public application started serving")


@pytest.mark.parametrize("scenario", ["foreign-origin", "foreign-source", "missing-source", "valid", "fallback-path"])
def test_embed_ready_message_requires_iframe_source_and_base_origin(scenario):
    # Execute the actual shipped script, not assertions about source-code text.
    # Only DOM/timers are faked; no browser, network, or database is involved.
    from app.web.pages import _EMBED_TEMPLATE

    node = shutil.which("node")
    assert node, "Node.js is required to execute the embed security contract"
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const scenario = input.scenario;
let listener, frame, cleared = 0;
const timers = new Map();
const events = [];
function element(tag) {
  const el = {
    src: '', contentWindow: {}, appendChild() {}, setAttribute() {},
    removeAttribute() {}, addEventListener() {}
  };
  if (tag === 'iframe') frame = el;
  return el;
}
const current = {
  src: scenario === 'fallback-path' ? 'not a URL' : 'https://widget.example/embed.js',
  getAttribute: name => name === 'data-notary' ? 'demo' : null
};
const document = {
  currentScript: current, createElement: element,
  head: element('head'), body: element('body'), addEventListener() {},
  dispatchEvent: event => events.push(event.type)
};
const window = { addEventListener: (name, callback) => { if (name === 'message') listener = callback; } };
vm.runInNewContext(input.script, {
  window, document, URL, console,
  CustomEvent: function(type) { this.type = type; },
  setTimeout: fn => { timers.set(1, fn); return 1; },
  clearTimeout: id => { timers.delete(id); cleared++; }
});
window.notarybot.open();
listener({
  origin: scenario === 'foreign-origin' ? 'https://attacker.example' : 'https://widget.example',
  source: scenario === 'foreign-source' ? {} : scenario === 'missing-source' ? null : frame.contentWindow,
  data: { source: 'notarybot', type: 'ready' }
});
for (const callback of timers.values()) callback();
process.stdout.write(JSON.stringify({ cleared, unavailable: events.includes('notarybot:unavailable') }));
"""
    result = subprocess.run(
        [node, "-e", harness],
        input=json.dumps({"scenario": scenario, "script": _EMBED_TEMPLATE.replace("__BASE__", "https://widget.example/service/")}),
        text=True, capture_output=True, check=True, timeout=10,
    )
    accepted = scenario in {"valid", "fallback-path"}
    assert json.loads(result.stdout) == {"cleared": int(accepted), "unavailable": not accepted}


@pytest.mark.parametrize("url", [
    "http://localhost.attacker.example", "http://127.0.0.1.attacker.example",
    "http://0.0.0.0:8000", "https://localhost", "https://127.0.0.1",
])
def test_only_plain_http_loopback_is_development(tmp_path, url):
    assert settings_for(tmp_path, public_base_url=url).is_production is True


@pytest.mark.parametrize("url", ["", "app.example.ru", "ftp://app.example.ru", "http://localhost:invalid", "http://[broken"])
async def test_invalid_public_address_fails_closed_at_startup(tmp_path, monkeypatch, url):
    main = importlib.import_module("app.web.main")
    settings = settings_for(tmp_path, public_base_url=url)
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "dispose_engine", AsyncMock())
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        async with main.lifespan(FastAPI()):
            pass
    assert not settings.storage_dir.exists()
