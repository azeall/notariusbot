"""Проверка осмысленности имени файла.

Сотрудник разбирает заявку по списку вложений. Если клиент прислал
IMG_20260816_121314.jpg, DOC001.pdf и scan.pdf — приходится открывать каждый,
чтобы понять, где паспорт, а где свидетельство. Поэтому имя требуем понятное.

Правило намеренно мягкое: не заставляем угадывать формат, а лишь отсекаем
автоматические имена камеры и сканера.
"""

import re

# Имена, которые телефоны и сканеры выдают сами. Цифры и разделители при
# сравнении отбрасываются, поэтому IMG_2481 и img-2481 ловятся одинаково.
GENERIC_STEMS = {
    "img",
    "image",
    "photo",
    "foto",
    "picture",
    "pic",
    "scan",
    "scanned",
    "doc",
    "document",
    "file",
    "download",
    "screenshot",
    "снимок",
    "фото",
    "изображение",
    "скан",
    "документ",
    "файл",
    "безымянный",
    "новый",
}

LETTERS_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ]")
SEPARATORS_RE = re.compile(r"[\s_\-.()\[\]#№]+")
MIN_LETTERS = 3

HINT = (
    "Назовите файл так, чтобы было понятно, что внутри: "
    "паспорт.pdf, свидетельство о браке.jpg, СТС.pdf."
)


def _stem(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def check_filename(filename: str) -> str | None:
    """Вернуть текст ошибки или None, если имя годится."""
    stem = _stem(filename)

    if not stem:
        return f"У файла нет имени. {HINT}"

    letters = LETTERS_RE.findall(stem)
    if len(letters) < MIN_LETTERS:
        return (
            f"«{filename}» — по такому имени непонятно, что внутри. {HINT}"
        )

    # Убираем цифры и разделители: остаётся смысловая часть имени.
    words = [w for w in SEPARATORS_RE.split(stem.lower()) if w]
    meaningful = [w for w in words if not w.isdigit()]

    if not meaningful:
        return f"«{filename}» — в имени только цифры. {HINT}"

    # Имя целиком состоит из служебных слов вроде img / scan / документ.
    stripped = [re.sub(r"\d+", "", w) for w in meaningful]
    stripped = [w for w in stripped if w]
    if stripped and all(w in GENERIC_STEMS for w in stripped):
        return (
            f"«{filename}» — это имя по умолчанию от камеры или сканера. {HINT}"
        )

    return None
