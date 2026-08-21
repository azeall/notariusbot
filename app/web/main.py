from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import dispose_engine

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Нотариус: заявки",
        description="Приём заявок нотариуса: виджет на сайт, перечни документов, запись на приём",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Публичное API виджета читают со страниц нотариусов. Куки в нём не участвуют,
    # поэтому credentials выключены намеренно.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    from app.web import admin, pages, platform, staff, uploads, widget

    app.include_router(widget.router)
    app.include_router(pages.router)
    app.include_router(uploads.router)
    app.include_router(platform.router)
    app.include_router(staff.router)
    app.include_router(admin.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        # Корень сервиса открывают случайно: по ссылке из адресной строки или
        # после выхода. Показывать здесь схему адресов не нужно — клиент
        # приходит через виджет, сотрудник по своей ссылке.
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Приём заявок нотариуса</title>"
            "<body style='background:#fff;color:#0b0b0d;margin:0;"
            "font:16px/1.6 system-ui,-apple-system,\"Segoe UI\",sans-serif;"
            "display:flex;min-height:100vh;align-items:center;justify-content:center'>"
            "<div style='text-align:center;padding:24px'>"
            "<p style='font-size:20px;font-weight:600;letter-spacing:-.02em;margin:0'>"
            "Приём заявок нотариуса</p>"
            "<p style='color:#6b6f76;font-size:14px;margin:10px 0 0'>"
            "Чтобы оставить заявку, воспользуйтесь формой на сайте нотариуса "
            "или ссылкой, которую он вам дал.</p>"
            "</div></body>"
        )

    return app


app = create_app()
