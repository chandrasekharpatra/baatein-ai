import pytest

from baatein.text import search_terms, words


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hello, World!", ["hello", "world"]),
        ("sister's 'quoted' don\u2019t", ["sister", "quoted", "don"]),
        ("मेरी बहन की शादी", ["मेरी", "बहन", "की", "शादी"]),
        ("snake_case 42", ["snake", "case", "42"]),
        ("'' ?? !!", []),
    ],
)
def test_words(text: str, expected: list[str]) -> None:
    assert words(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("When is my sister's wedding in December?", ["sister", "wedding", "december"]),
        ("Friday ko interview kab hai?", ["friday", "interview"]),
        ("मेरी बहन की शादी कब है", ["बहन", "शादी", "कब"]),
        ("chai chai CHAI", ["chai"]),
        ("is it the", []),
    ],
)
def test_search_terms_drop_stopwords_and_duplicates(text: str, expected: list[str]) -> None:
    assert search_terms(text) == expected
