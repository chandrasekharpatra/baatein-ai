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


@pytest.mark.parametrize(
    "text", ["ठीक है।", "नहीं नहीं यार।", "Haan ji, theek hai", "Yeah, it's good.", "हम्म, अच्छा"]
)
def test_fillers_leave_nothing_to_search(text: str) -> None:
    assert search_terms(text) == []


def test_fillers_keep_the_words_that_matter() -> None:
    assert search_terms("अच्छा मेरे पिताजी का याद आ रहा है।") == ["पिताजी", "याद", "आ", "रहा"]
    assert search_terms("Haan, Ramesh ko phone kiya") == ["ramesh", "phone", "kiya"]
