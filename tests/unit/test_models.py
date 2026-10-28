import pytest

from tests.factories import at, make_fact


@pytest.mark.parametrize("importance", [0, 6])
def test_fact_importance_must_be_1_to_5(importance: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 5"):
        make_fact("u", importance=importance)


def test_fact_is_active_until_retired() -> None:
    assert make_fact("u").is_active
    assert not make_fact("u", retired_at=at(1)).is_active
