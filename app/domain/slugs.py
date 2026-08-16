"""Короткий код нотариуса: попадает в адрес виджета и в ссылку бота."""

import re

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")

TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def suggest_slug(display_name: str) -> str:
    """Код из названия: «Нотариус Иванов И. И.» → ivanov."""
    lowered = display_name.lower().replace("нотариус", " ")
    latin = "".join(TRANSLIT.get(ch, ch) for ch in lowered)
    words = [w for w in re.split(r"[^a-z0-9]+", latin) if len(w) > 1]
    return "-".join(words[:2])[:64] or "notary"
