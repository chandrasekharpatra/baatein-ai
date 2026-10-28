import pytest

from baatein.domain.errors import DuplicateEmailError
from tests.contract.conftest import Repos
from tests.factories import make_user


async def test_add_then_get_round_trips(repos: Repos) -> None:
    user = make_user(display_name="Asha", language="english")

    await repos.users.add(user)

    assert await repos.users.get(user.id) == user


async def test_get_missing_returns_none(repos: Repos) -> None:
    assert await repos.users.get("missing") is None


async def test_get_by_email_is_case_insensitive(repos: Repos) -> None:
    user = make_user(email="asha@example.com")
    await repos.users.add(user)

    assert await repos.users.get_by_email("ASHA@Example.com") == user
    assert await repos.users.get_by_email("other@example.com") is None


async def test_duplicate_email_rejected_regardless_of_case(repos: Repos) -> None:
    await repos.users.add(make_user(email="asha@example.com"))

    with pytest.raises(DuplicateEmailError):
        await repos.users.add(make_user(email="Asha@Example.com"))
