"""Палитра виджета.

Виджет открывается поверх сайта нотариуса, и если он выглядит чужой вставкой,
доверие к нему падает вместе с доверием к самой записи. Поэтому цвета берутся
из настроек нотариуса, а не зашиты в стили.

Задаются три вещи: светлая тема или тёмная, цвет акцента и шрифт заголовков.
Остальное считается от них — так нотариусу не приходится подбирать восемь
оттенков, чтобы получилось непротиворечиво.
"""

from dataclasses import dataclass

DARK = "dark"
LIGHT = "light"

MODES = (DARK, LIGHT)
FONTS = ("sans", "serif")

DEFAULT_ACCENT = "#b89a5a"


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface_2: str
    text: str
    muted: str
    border: str
    accent: str
    accent_text: str
    heading_font: str


def _readable_on(accent: str) -> str:
    """Чёрный или белый поверх акцента — по яркости самого акцента."""
    value = accent.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return "#ffffff"
    # Стандартная формула воспринимаемой яркости.
    brightness = (r * 299 + g * 587 + b * 114) / 1000
    return "#10151f" if brightness > 150 else "#ffffff"


def normalize_accent(accent: str) -> str:
    value = (accent or "").strip()
    if not value.startswith("#"):
        value = "#" + value
    body = value[1:]
    if len(body) in (3, 6) and all(ch in "0123456789abcdefABCDEF" for ch in body):
        return value.lower()
    return DEFAULT_ACCENT


def build_palette(mode: str, accent: str, font: str) -> Palette:
    accent = normalize_accent(accent)
    heading = (
        'Georgia, "Times New Roman", serif'
        if font == "serif"
        else 'system-ui, -apple-system, "Segoe UI", sans-serif'
    )

    if mode == LIGHT:
        return Palette(
            bg="#ffffff",
            surface="#f5f6f8",
            surface_2="#eceef2",
            text="#10151f",
            muted="#6b7280",
            border="rgba(16,21,31,0.12)",
            accent=accent,
            accent_text=_readable_on(accent),
            heading_font=heading,
        )

    return Palette(
        bg="#0a1628",
        surface="#0f1e35",
        surface_2="#112240",
        text="#f0ece4",
        muted="#8a9ab5",
        border="rgba(255,255,255,0.12)",
        accent=accent,
        accent_text=_readable_on(accent),
        heading_font=heading,
    )


def palette_css(palette: Palette) -> str:
    """Переменные, которые перекрывают значения по умолчанию в widget.css."""
    return (
        ":root{"
        f"--navy:{palette.bg};"
        f"--navy-card:{palette.surface};"
        f"--navy-card-2:{palette.surface_2};"
        f"--cream:{palette.text};"
        f"--slate:{palette.muted};"
        f"--gold:{palette.accent};"
        f"--gold-light:{palette.accent};"
        f"--accent-text:{palette.accent_text};"
        f"--border:{palette.border};"
        f"--heading:{palette.heading_font};"
        "}"
    )
