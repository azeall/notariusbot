from fastapi import APIRouter, Depends, Request as HttpRequest
from fastapi.responses import HTMLResponse, Response

from app.config import get_settings
from app.domain.theme import build_palette, palette_css
from app.models import Tenant
from app.web.deps import resolve_tenant

router = APIRouter(tags=["pages"])


@router.get("/widget/{slug}", response_class=HTMLResponse)
async def widget_page(http_request: HttpRequest, tenant: Tenant = Depends(resolve_tenant)):
    """Страница виджета. Открывается внутри iframe на сайте нотариуса."""
    from app.web.main import TEMPLATES

    palette = build_palette(tenant.widget_mode, tenant.widget_accent, tenant.widget_font)
    response = TEMPLATES.TemplateResponse(
        http_request,
        "widget.html",
        {
            "title": f"Заявка нотариусу — {tenant.display_name}",
            "tenant": tenant,
            "api_base": f"/api/v1/{tenant.slug}",
            "palette_css": palette_css(palette),
        },
    )
    # Кто имеет право встраивать страницу. Пустой список доменов означает
    # разработку — тогда ограничение не выставляем.
    if tenant.allowed_origins:
        allowed = " ".join(tenant.allowed_origins)
        response.headers["Content-Security-Policy"] = f"frame-ancestors 'self' {allowed}"
    return response


@router.get("/embed.js")
async def embed_script() -> Response:
    """Скрипт для вставки на сайт нотариуса.

    Ничего не знает о внутренностях: рисует кнопку и открывает iframe.
    Персональные данные вводятся уже внутри нашего домена, а не на чужой странице.
    """
    settings = get_settings()
    script = _EMBED_TEMPLATE.replace("__BASE__", settings.public_base_url)
    return Response(
        content=script,
        media_type="application/javascript; charset=utf-8",
        # Короткий кеш намеренно: скрипт содержит адрес сервиса, и при переезде
        # (или смене адреса туннеля) браузеры не должны держать старую копию.
        headers={"Cache-Control": "public, max-age=60"},
    )


_EMBED_TEMPLATE = """
(function () {
  // currentScript пуст, если тег вставили динамически — так делает next/script,
  // поэтому подстраховываемся поиском по атрибуту.
  var current = document.currentScript || document.querySelector("script[data-notary]");
  if (!current) return;

  // Адрес берём из собственного src: скрипт уже загрузился с нужного домена,
  // а настройка на сервере может устареть — например, если сменился адрес туннеля.
  var BASE = "__BASE__";
  try {
    BASE = new URL(current.src).origin;
  } catch (e) {
    /* остаётся значение из настроек */
  }
  var slug = current.getAttribute("data-notary");
  if (!slug) {
    console.error("[notarybot] не задан data-notary");
    return;
  }
  var label = current.getAttribute("data-label") || "Записаться к нотариусу";
  var accent = current.getAttribute("data-accent") || "#b89a5a";
  // data-launcher="none" — не рисовать свою плавающую кнопку. Нужно сайтам,
  // у которых уже есть собственные кнопки записи: они вызовут notarybot.open().
  var withLauncher = current.getAttribute("data-launcher") !== "none";

  var style = document.createElement("style");
  style.textContent = [
    ".nb-launcher{position:fixed;right:24px;bottom:24px;z-index:2147483000;",
    "background:" + accent + ";color:#0a1628;border:0;border-radius:999px;",
    "padding:14px 22px;font:600 15px/1.2 system-ui,-apple-system,sans-serif;",
    "cursor:pointer;box-shadow:0 10px 30px rgba(10,22,40,.35);}",
    ".nb-launcher:hover{filter:brightness(1.06);}",
    ".nb-overlay{position:fixed;inset:0;z-index:2147483001;background:rgba(4,13,24,.72);",
    "display:none;align-items:center;justify-content:center;padding:16px;}",
    ".nb-overlay[data-open='1']{display:flex;}",
    ".nb-frame{width:min(460px,100%);height:min(680px,92vh);border:0;border-radius:16px;",
    "background:#0a1628;box-shadow:0 24px 70px rgba(0,0,0,.5);}",
    ".nb-close{position:absolute;top:18px;right:22px;background:transparent;border:0;",
    "color:#f0ece4;font-size:30px;line-height:1;cursor:pointer;}"
  ].join("");
  document.head.appendChild(style);

  var button = document.createElement("button");
  button.className = "nb-launcher";
  button.type = "button";
  button.textContent = label;

  var overlay = document.createElement("div");
  overlay.className = "nb-overlay";

  var close = document.createElement("button");
  close.className = "nb-close";
  close.type = "button";
  close.setAttribute("aria-label", "Закрыть");
  close.innerHTML = "&times;";

  var frame = document.createElement("iframe");
  frame.className = "nb-frame";
  frame.title = "Заявка нотариусу";

  overlay.appendChild(close);
  overlay.appendChild(frame);

  function open() {
    if (!frame.src) frame.src = BASE + "/widget/" + encodeURIComponent(slug);
    overlay.setAttribute("data-open", "1");
  }
  function hide() { overlay.removeAttribute("data-open"); }

  button.addEventListener("click", open);
  close.addEventListener("click", hide);
  overlay.addEventListener("click", function (event) {
    if (event.target === overlay) hide();
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") hide();
  });

  if (withLauncher) document.body.appendChild(button);
  document.body.appendChild(overlay);

  // Публичный интерфейс для сайта: собственные кнопки записи вызывают его сами.
  window.notarybot = { open: open, close: hide };
  document.dispatchEvent(new CustomEvent("notarybot:ready"));
})();
"""
