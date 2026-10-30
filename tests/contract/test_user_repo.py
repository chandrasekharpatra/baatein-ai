import pytest

from baatein.domain.errors import DuplicateEmailError, NotFoundError
from baatein.domain.models import Pace, Preferences, Verbosity
from tests.contract.conftest import Repos
from tests.factories import make_user


async def test_add_then_get_round_trips(repos: Repos) -> None:
    user = make_user(
        display_name="Asha",
        language="english",
        timezone="Europe/London",
        persona="elder",
        preferences=Preferences("Asha ji", Pace.SLOW, Verbosity.SHORT, True),
    )

    await repos.users.add(user)

    assert await repos.users.get(user.id) == user


async def test_timezone_defaults_to_india(repos: Repos) -> None:
    user = make_user()

    await repos.users.add(user)

    stored = await repos.users.get(user.id)
    assert stored is not None
    assert stored.timezone == "Asia/Kolkata"


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


async def test_set_preferences_round_trips(repos: Repos) -> None:
    user = make_user()
    await repos.users.add(user)
    preferences = Preferences(pace=Pace.FAST, verbosity=Verbosity.DETAILED)

    await repos.users.set_preferences(user.id, preferences)

    stored = await repos.users.get(user.id)
    assert stored is not None
    assert (stored.preferences, stored.persona) == (preferences, "casual")
    with pytest.raises(NotFoundError):
        await repos.users.set_preferences("missing", preferences)


async def test_set_persona(repos: Repos) -> None:
    user = make_user()
    await repos.users.add(user)

    await repos.users.set_persona(user.id, "elder")

    stored = await repos.users.get(user.id)
    assert stored is not None
    assert stored.persona == "elder"
    with pytest.raises(NotFoundError):
        await repos.users.set_persona("missing", "elder")
