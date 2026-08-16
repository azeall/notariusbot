import pytest

from app.domain.filenames import check_filename


@pytest.mark.parametrize(
    "filename",
    [
        "паспорт.pdf",
        "Паспорт доверителя.pdf",
        "свидетельство о браке.jpg",
        "СТС.pdf",
        "свидетельство_о_рождении_2019.pdf",
        "passport.jpg",
        "доверенность (копия).pdf",
        "паспорт стр 2-3.png",
    ],
)
def test_meaningful_names_pass(filename):
    assert check_filename(filename) is None


@pytest.mark.parametrize(
    "filename",
    [
        "IMG_2481.jpg",
        "img20260816.jpg",
        "IMG-0001.HEIC",
        "DOC001.pdf",
        "scan.pdf",
        "Scan_0007.pdf",
        "screenshot 2026-08-16.png",
        "photo_2026-08-16_12-13-14.jpg",
        "документ.pdf",
        "Новый документ.pdf",
        "файл1.pdf",
        "20260816.pdf",
        "1234.jpg",
        "____.pdf",
        "..pdf",
    ],
)
def test_generic_names_rejected(filename):
    assert check_filename(filename) is not None


def test_error_names_the_file_and_gives_example():
    problem = check_filename("IMG_2481.jpg")
    assert "IMG_2481.jpg" in problem
    assert "паспорт.pdf" in problem


def test_empty_name_rejected():
    assert check_filename("") is not None
    assert check_filename(".pdf") is not None


def test_meaningful_word_saves_generic_prefix():
    """«scan паспорта» — осмысленно, хотя и начинается со служебного слова."""
    assert check_filename("scan паспорта.pdf") is None
    assert check_filename("photo СТС.jpg") is None
