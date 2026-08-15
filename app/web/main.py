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

    from app.web import admin, pages, staff, uploads, widget

    app.include_router(widget.router)
    app.include_router(pages.router)
    app.include_router(uploads.router)
    app.include_router(staff.router)
    app.include_router(admin.router)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    async def index() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<title>Приём заявок нотариуса</title>"
            "<body style='background:#06101f;color:#f0ece4;font:16px system-ui;"
            "display:flex;min-height:100vh;align-items:center;justify-content:center'>"
            "<div style='text-align:center'>"
            "<p style='font-family:Georgia,serif;font-size:20px'>Приём заявок нотариуса</p>"
            "<p style='color:#8a9ab5;font-size:14px'>"
            "Виджет: <code>/widget/&lt;код нотариуса&gt;</code> · "
            "Панель: <code>/staff/&lt;код нотариуса&gt;/login</code></p>"
            "</div></body>"
        )

    return app


app = create_app()
