"""Палитра виджета под сайт нотариуса."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.domain.security import hash_password
from app.domain.theme import DEFAULT_ACCENT, build_palette, normalize_accent, palette_css
from app.models import PlatformAdmin, Tenant
from app.web.deps import db_session
from app.web.main import app


@pytest.fixture
async def http(engine, session):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with maker() as request_session:
            try:
                yield request_session
                await request_session.commit()
            except Exception:
                await request_session.rollback()
                raise

    app.dependency_overrides[db_session] = override_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def as_vendor(http, session):
    admin = PlatformAdmin(
        email="vendor@example.ru",
        password_hash=hash_password("vendor12345"),
        full_name="Владелец",
    )
    session.add(admin)
    await session.commit()
    await http.post(
        "/platform/login", data={"email": admin.email, "password": "vendor12345"}
    )
    return http


# --- сама палитра -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("#ff0000", "#ff0000"),
        ("ff0000", "#ff0000"),
        ("#FFF", "#fff"),
        ("", DEFAULT_ACCENT),
        ("не цвет", DEFAULT_ACCENT),
        ("#12345", DEFAULT_ACCENT),
    ],
)
def test_normalize_accent(raw, expected):
    assert normalize_accent(raw) == expected


def test_light_and_dark_differ():
    light = build_palette("light", "#ff0000", "sans")
    dark = build_palette("dark", "#ff0000", "sans")
    assert light.bg != dark.bg
    assert light.text != dark.text
    assert light.accent == dark.accent


def test_text_on_accent_is_readable():
    """На светлом акценте текст тёмный, на тёмном — светлый."""
    bright = build_palette("dark", "#ffe08a", "sans")
    deep = build_palette("dark", "#1a1a6e", "sans")
    assert bright.accent_text == "#10151f"
    assert deep.accent_text == "#ffffff"


def test_serif_font_applied():
    assert "Georgia" in build_palette("dark", "#000000", "serif").heading_font
    assert "Georgia" not in build_palette("dark", "#000000", "sans").heading_font


def test_palette_css_overrides_variables():
    css = palette_css(build_palette("light", "#ff0000", "sans"))
    assert "--gold:#ff0000" in css
    assert "--navy:#ffffff" in css
    assert css.startswith(":root{")


# --- через приложение -------------------------------------------------------


async def test_widget_page_carries_tenant_palette(http, tenant, session):
    tenant.widget_mode = "light"
    tenant.widget_accent = "#2f6fed"
    await session.commit()

    page = await http.get(f"/widget/{tenant.slug}")
    assert "--gold:#2f6fed" in page.text
    assert "--navy:#ffffff" in page.text


async def test_default_palette_is_dark(http, tenant):
    page = await http.get(f"/widget/{tenant.slug}")
    assert "--navy:#0a1628" in page.text


async def test_vendor_sets_palette(as_vendor, session, tenant):
    response = await as_vendor.post(
        "/platform/tenants",
        data={
            "tenant_id": str(tenant.id),
            "display_name": tenant.display_name,
            "slug": tenant.slug,
            "widget_mode": "light",
            "widget_accent": "2f6fed",
            "widget_font": "serif",
            "is_active": "1",
        },
    )
    assert response.status_code == 303

    await session.refresh(tenant)
    assert tenant.widget_mode == "light"
    assert tenant.widget_accent == "#2f6fed"
    assert tenant.widget_font == "serif"


async def test_bad_palette_values_fall_back(as_vendor, session, tenant):
    await as_vendor.post(
        "/platform/tenants",
        data={
            "tenant_id": str(tenant.id),
            "display_name": tenant.display_name,
            "slug": tenant.slug,
            "widget_mode": "радуга",
            "widget_accent": "мой любимый",
            "widget_font": "рукописный",
            "is_active": "1",
        },
    )
    await session.refresh(tenant)
    assert tenant.widget_mode == "dark"
    assert tenant.widget_accent == DEFAULT_ACCENT
    assert tenant.widget_font == "sans"


async def test_new_tenant_gets_defaults(as_vendor, session):
    await as_vendor.post(
        "/platform/tenants",
        data={"display_name": "Нотариус Новиков", "slug": "novikov"},
    )
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == "novikov"))
    assert tenant.widget_mode == "dark"
    assert tenant.widget_accent == DEFAULT_ACCENT
