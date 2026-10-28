import pytest

from baatein.memory.ranking import RRF_K, reciprocal_rank_fusion


def test_items_in_both_rankings_outscore_single_list_items() -> None:
    scores = reciprocal_rank_fusion([["a", "b"], ["b", "c"]])

    assert scores["b"] > scores["a"] > scores["c"]


def test_scores_use_rank_positions() -> None:
    scores = reciprocal_rank_fusion([["a", "b"]], k=10)

    assert scores == pytest.approx({"a": 1 / 11, "b": 1 / 12})


def test_empty_rankings() -> None:
    assert reciprocal_rank_fusion([[], []]) == {}
    assert RRF_K == 60
