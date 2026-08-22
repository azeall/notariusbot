# Картинки для объявления на Авито.
#
# Размер 1200x900 — соотношение 4:3, которое Авито показывает без обрезки,
# и вдвое выше минимума в 640x480.
#
# Всё крупное и плоское намеренно: первая картинка уходит в выдачу размером
# с ноготь, и там читается только очень большой текст на однородном фоне.
# Тонкие линии и мелкие подписи на этом размере превращаются в грязь.
import io
import os

W, H = 1200, 900
INK = "#0b0b0d"
PAPER = "#ffffff"
ACCENT = "#2f5bea"
MUTE = "#9aa0a8"
FONT = "Inter, 'Segoe UI', system-ui, -apple-system, sans-serif"

OUT = os.path.dirname(os.path.abspath(__file__))


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def head(bg):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}">'
        f'<rect width="{W}" height="{H}" fill="{bg}"/>'
    )


def mark(color=MUTE, y=810):
    """Подпись-марка внизу. Без телефона и ссылок — за них снимают с публикации."""
    return (
        f'<text x="80" y="{y}" font-size="26" font-weight="600" fill="{color}" '
        f'letter-spacing="-0.5">нотариус<tspan fill="{ACCENT}">·</tspan>бот</text>'
    )


def lines(items, x, y, step, size, color, weight="500"):
    out = ""
    for i, t in enumerate(items):
        out += (
            f'<text x="{x}" y="{y + i * step}" font-size="{size}" font-weight="{weight}" '
            f'fill="{color}">{esc(t)}</text>'
        )
    return out


def bullets(items, x, y, step, size, color, dot=ACCENT):
    out = ""
    for i, t in enumerate(items):
        cy = y + i * step
        out += f'<circle cx="{x + 9}" cy="{cy - 11}" r="9" fill="{dot}"/>'
        out += (
            f'<text x="{x + 42}" y="{cy}" font-size="{size}" font-weight="500" '
            f'fill="{color}">{esc(t)}</text>'
        )
    return out


slides = {}

# ── 1. Обложка ───────────────────────────────────────────────────────────
# Уходит в выдачу размером с ноготь: три строки и больше ничего.
slides["01-oblozhka-sait"] = (
    head(INK)
    + f'<rect x="0" y="0" width="{W}" height="10" fill="{ACCENT}"/>'
    + '<text x="80" y="330" font-size="100" font-weight="700" fill="#ffffff" letter-spacing="-3">Сайт нотариуса</text>'
    + f'<text x="80" y="446" font-size="100" font-weight="700" fill="{ACCENT}" letter-spacing="-3">+ приём заявок</text>'
    + '<text x="80" y="566" font-size="44" font-weight="500" fill="#c9cdd4">Клиент пишет, что ему нужно, —</text>'
    + '<text x="80" y="626" font-size="44" font-weight="500" fill="#c9cdd4">получает список документов и запись</text>'
    + f'<rect x="80" y="700" width="440" height="78" rx="39" fill="{ACCENT}"/>'
    + '<text x="300" y="751" font-size="34" font-weight="600" fill="#ffffff" text-anchor="middle">Под ключ за 2–5 дней</text>'
    + mark("#6b7280", 848)
    + "</svg>"
)

# Вторая обложка — для отдельного объявления тем, у кого сайт уже есть.
# Цена здесь честная: 20 000 — это подключение, а не сайт.
slides["01b-oblozhka-zayavki"] = (
    head(INK)
    + f'<rect x="0" y="0" width="{W}" height="10" fill="{ACCENT}"/>'
    + '<text x="80" y="330" font-size="100" font-weight="700" fill="#ffffff" letter-spacing="-3">Приём заявок</text>'
    + f'<text x="80" y="446" font-size="100" font-weight="700" fill="{ACCENT}" letter-spacing="-3">на ваш сайт</text>'
    + '<text x="80" y="566" font-size="44" font-weight="500" fill="#c9cdd4">Список документов, запись на приём,</text>'
    + '<text x="80" y="626" font-size="44" font-weight="500" fill="#c9cdd4">уведомления в Telegram</text>'
    + f'<rect x="80" y="700" width="520" height="78" rx="39" fill="{ACCENT}"/>'
    + '<text x="340" y="751" font-size="34" font-weight="600" fill="#ffffff" text-anchor="middle">1 день · 20 000 ₽ + 3 000/мес</text>'
    + mark("#6b7280", 848)
    + "</svg>"
)

# ── 2. Что входит ────────────────────────────────────────────────────────
slides["02-chto-vhodit"] = (
    head(PAPER)
    + f'<text x="80" y="160" font-size="64" font-weight="700" fill="{INK}" letter-spacing="-2">Что входит</text>'
    + bullets(
        [
            "Сайт: услуги, цены, блог, карта",
            "Виджет заявок на сайте",
            "Кабинет для вас и сотрудников",
            "Уведомления в Telegram",
            "Приём документов от клиента",
        ],
        80, 300, 108, 44, INK,
    )
    + mark()
    + "</svg>"
)

# ── 3. Меняете сами ──────────────────────────────────────────────────────
slides["03-menyaete-sami"] = (
    head(INK)
    + '<text x="80" y="150" font-size="60" font-weight="700" fill="#ffffff" letter-spacing="-2">Меняете сами,</text>'
    + f'<text x="80" y="228" font-size="60" font-weight="700" fill="{ACCENT}" letter-spacing="-2">без разработчика</text>'
    + bullets(
        [
            "Услуги, цены и сроки",
            "Списки документов",
            "Часы приёма и выходные",
            "Сотрудники и их доступы",
            "Вид виджета под ваш сайт",
        ],
        80, 360, 96, 42, "#e6e8ec", "#ffffff",
    )
    + mark("#6b7280")
    + "</svg>"
)

# ── 4. Данные и закон ────────────────────────────────────────────────────
slides["04-dannye-i-zakon"] = (
    head(PAPER)
    + f'<text x="80" y="150" font-size="60" font-weight="700" fill="{INK}" letter-spacing="-2">Паспорта клиентов —</text>'
    + f'<text x="80" y="228" font-size="60" font-weight="700" fill="{ACCENT}" letter-spacing="-2">под 152-ФЗ</text>'
    + bullets(
        [
            "Согласие — до создания заявки",
            "Файлы хранятся зашифрованными",
            "Ссылка одноразовая, живёт 30 минут",
            "Каждое открытие пишется в журнал",
            "Сервер в России",
        ],
        80, 360, 96, 42, INK,
    )
    + mark()
    + "</svg>"
)

# ── 5. Цены ──────────────────────────────────────────────────────────────
def price_row(y, title, big, small):
    return (
        f'<line x1="80" y1="{y - 74}" x2="{W - 80}" y2="{y - 74}" stroke="#e6e7ea" stroke-width="2"/>'
        + f'<text x="80" y="{y}" font-size="40" font-weight="600" fill="{INK}">{esc(title)}</text>'
        + f'<text x="{W - 80}" y="{y - 6}" font-size="52" font-weight="700" fill="{INK}" '
          f'text-anchor="end" letter-spacing="-1">{esc(big)}</text>'
        + f'<text x="{W - 80}" y="{y + 38}" font-size="30" font-weight="500" fill="{MUTE}" '
          f'text-anchor="end">{esc(small)}</text>'
    )


slides["05-tseny"] = (
    head(PAPER)
    + f'<text x="80" y="150" font-size="64" font-weight="700" fill="{INK}" letter-spacing="-2">Сколько стоит</text>'
    + price_row(330, "Сайт вместе с заявками", "70 000 ₽", "+ 3 000 ₽ в месяц")
    + price_row(500, "Заявки на готовый сайт", "20 000 ₽", "+ 3 000 ₽ в месяц")
    + price_row(670, "Только сайт", "55 000 ₽", "без ежемесячных платежей")
    + f'<line x1="80" y1="596" x2="{W - 80}" y2="596" stroke="#e6e7ea" stroke-width="2"/>'
    + f'<text x="80" y="770" font-size="28" font-weight="500" fill="{MUTE}">Домен и хостинг — напрямую, около 1 500 ₽ в год</text>'
    + mark(MUTE, 850)
    + "</svg>"
)

# ── 6. Четыре оформления ────────────────────────────────────────────────
# Схематичные окошки в палитрах вариантов: настоящие снимки нотариус
# увидит по ссылке, здесь важно показать, что выбор есть.
def mini(x, y, bg, bar, accent, label, label_color):
    w, h = 480, 268
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="{bg}" stroke="#dfe1e5" stroke-width="2"/>'
    s += f'<rect x="{x}" y="{y}" width="{w}" height="52" rx="16" fill="{bar}"/>'
    s += f'<rect x="{x}" y="{y + 36}" width="{w}" height="16" fill="{bar}"/>'
    s += f'<rect x="{x + 28}" y="{y + 92}" width="250" height="26" rx="13" fill="{accent}"/>'
    s += f'<rect x="{x + 28}" y="{y + 136}" width="380" height="14" rx="7" fill="{accent}" opacity=".35"/>'
    s += f'<rect x="{x + 28}" y="{y + 166}" width="330" height="14" rx="7" fill="{accent}" opacity=".35"/>'
    s += f'<rect x="{x + 28}" y="{y + 214}" width="150" height="44" rx="22" fill="{accent}"/>'
    s += (f'<text x="{x + 28}" y="{y + 34}" font-size="22" font-weight="600" '
          f'fill="{label_color}">{esc(label)}</text>')
    return s


slides["06-chetyre-oformleniya"] = (
    head(PAPER)
    + f'<text x="80" y="130" font-size="60" font-weight="700" fill="{INK}" letter-spacing="-2">4 оформления на выбор</text>'
    + mini(80, 190, "#0a1628", "#0f1e35", "#b89a5a", "Тёмно-синий", "#f0ece4")
    + mini(640, 190, "#17143a", "#211d4e", "#7d73e0", "Лавандовый", "#eceafb")
    + mini(80, 496, "#fdf6ef", "#f0e2d2", "#c05c2e", "Тёплый", "#3d2010")
    + mini(640, 496, "#0d1a15", "#132720", "#1d9e75", "Зелёный", "#eaf3ee")
    + f'<text x="80" y="836" font-size="40" font-weight="600" fill="{INK}">'
      '…или в ваших цветах — подберём под вывеску конторы</text>'
    + "</svg>"
)

for name, svg in slides.items():
    io.open(os.path.join(OUT, name + ".svg"), "w", encoding="utf-8").write(svg)

print("готово:", len(slides), "картинок")
for n in slides:
    print("  ", n + ".svg")
