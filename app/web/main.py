from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import dispose_engine
from app.web import sessions

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    # Схема API открыта только вне боевого. На боевом /docs, /redoc и
    # /openapi.json отдавали 200 без входа и показывали все 57 эндпоинтов
    # вместе со схемами данных — включая /platform/tenants/{id}/delete,
    # выдачу приглашений и работу с заявками. Сами маршруты закрыты (401),
    # но карта раздавалась желающим бесплатно.
    #
    # Признак боевого — https в публичном адресе: там же, откуда берётся
    # флаг secure у кук, чтобы не заводить второй переключатель, который
    # однажды забудут переставить.
    is_production = get_settings().public_base_url.startswith("https://")

    app = FastAPI(
        title="Нотариус: заявки",
        description="Приём заявок нотариуса: виджет на сайт, перечни документов, запись на приём",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if is_production else "/docs",
        redoc_url=None if is_production else "/redoc",
        openapi_url=None if is_production else "/openapi.json",
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

    @app.middleware("http")
    async def renew_session(request: Request, call_next):
        """Продлить сессию, пока кабинетом пользуются.

        Иначе даже длинный срок однажды кончается — посреди рабочего дня
        и без предупреждения. Проверка «пора ли» лежит в самой сессии,
        поэтому кука переписывается не чаще раза в сутки.
        """
        response = await call_next(request)
        renewal = getattr(request.state, "renew_session", None)
        if renewal is not None:
            cookie_name, key, subject_id = renewal
            value, ttl = sessions.issue(subject_id, remember=True, key=key)
            sessions.attach(response, cookie_name, value, ttl)
        return response

    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    from app.web import admin, pages, platform, staff, uploads, visits, widget

    app.include_router(widget.router)
    app.include_router(pages.router)
    app.include_router(uploads.router)
    app.include_router(visits.router)
    app.include_router(platform.router)
    app.include_router(staff.router)
    app.include_router(admin.router)

    @app.exception_handler(HTTPException)
    async def show_login_page(request: Request, exc: HTTPException):
        """Человеку в браузере — страница, машине — json.

        Раньше на /staff без входа браузер получал строку {"detail":"Требуется
        вход"} и человек оставался наедине с ней: ни объяснения, ни ссылки.
        Виджет и бот ходят сюда же, поэтому ответ выбирается по тому, что
        клиент готов принять, а не по адресу.
        """
        wants_html = "text/html" in request.headers.get("accept", "")
        if wants_html and exc.status_code in (401, 403) and not request.url.path.startswith("/api/"):
            return TEMPLATES.TemplateResponse(
                request,
                "needs_login.html",
                {
                    "title": "Нужно войти",
                    "heading": "Нужно войти" if exc.status_code == 401 else "Недостаточно прав",
                    "explain": (
                        "Эта страница доступна после входа."
                        if exc.status_code == 401
                        else "Вы вошли, но у этой учётной записи нет доступа к странице."
                    ),
                    "stylesheet": "/static/platform.css",
                },
                status_code=exc.status_code,
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

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
