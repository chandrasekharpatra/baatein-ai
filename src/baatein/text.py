import unicodedata

STOPWORDS = frozenset(
    """
    a about am an and are as at be been but by can could did do does for from had has have
    he her him his how i if in into is it its me my no not of on or our she so than that
    the their them then there they this to too us was we were what when where which who
    why will with would you your
    aap aur bhi hai hain ho hoga hum is ka kab kaha kahan kaise ke ki kis kya ko koi kuch
    main mein mera meri mere na ne nahi par pe se tha thi the to tu tum tera teri woh ye yeh
    और का की के को क्या था थी थे में मेरा मेरी मेरे से है हैं हो
    """.split()
)

APOSTROPHES = "'\u2019"


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch in APOSTROPHES or unicodedata.category(ch).startswith("M")


def words(text: str) -> list[str]:
    found: list[str] = []
    current: list[str] = []
    for ch in text.lower() + " ":
        if _is_word_char(ch):
            current.append(ch)
        elif current:
            found.append("".join(current))
            current = []
    stems = (word.replace("\u2019", "'").strip("'").split("'")[0] for word in found)
    return [stem for stem in stems if stem]


def search_terms(text: str) -> list[str]:
    terms: list[str] = []
    for term in words(text):
        if term not in STOPWORDS and term not in terms:
            terms.append(term)
    return terms
