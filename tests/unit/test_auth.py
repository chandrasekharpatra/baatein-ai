import json
from datetime import timedelta

import jwt
import pytest
from livekit import api

from baatein.auth.livekit import LiveKitCallTokens, room_name_for
from baatein.auth.passwords import Argon2PasswordHasher
from baatein.auth.service import AuthService, normalize_email
from baatein.auth.tokens import ALGORITHM, ISSUER, JwtAccessTokens
from baatein.domain.errors import (
    DuplicateEmailError,
    InvalidCredentialsError,
    InvalidTokenError,
    WeakPasswordError,
)
from baatein.voice.languages import UnknownLanguageError, default_language_registry
from baatein.voice.personas import UnknownPersonaError, default_persona_registry
from tests.factories import T0, make_user
from tests.fakes.clock import FrozenClock
from tests.fakes.repos import InMemoryState, InMemoryUserRepo

SECRET = "test-secret-that-is-long-enough-for-hs256"


def cheap_hasher() -> Argon2PasswordHasher:
    return Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class RecordingHasher:
    def __init__(self) -> None:
        self.verified: list[tuple[str, str]] = []

    def hash(self, password: str) -> str:
        return f"hashed:{password}"

    def verify(self, password_hash: str, password: str) -> bool:
        self.verified.append((password_hash, password))
        return password_hash == f"hashed:{password}"


def test_argon2_hash_verifies_only_matching_password() -> None:
    hasher = cheap_hasher()
    hashed = hasher.hash("correct horse")

    assert hashed.startswith("$argon2")
    assert hasher.verify(hashed, "correct horse")
    assert not hasher.verify(hashed, "wrong horse")
    assert not hasher.verify("not-a-hash", "correct horse")


def test_access_token_round_trip() -> None:
    tokens = JwtAccessTokens(SECRET, timedelta(hours=1), FrozenClock())

    assert tokens.verify(tokens.issue("user-1")) == "user-1"


def test_access_token_expires_by_injected_clock() -> None:
    clock = FrozenClock()
    tokens = JwtAccessTokens(SECRET, timedelta(hours=1), clock)
    token = tokens.issue("user-1")

    clock.advance(timedelta(hours=1))

    with pytest.raises(InvalidTokenError, match="expired"):
        tokens.verify(token)


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        jwt.encode(
            {"sub": "u", "iss": ISSUER, "exp": T0 + timedelta(hours=1)},
            "other-secret-of-sufficient-length",
            ALGORITHM,
        ),
        jwt.encode(
            {"sub": "u", "iss": "someone-else", "exp": T0 + timedelta(hours=1)}, SECRET, ALGORITHM
        ),
        jwt.encode({"sub": "u", "iss": ISSUER}, SECRET, ALGORITHM),
    ],
    ids=["garbage", "wrong-secret", "wrong-issuer", "no-expiry"],
)
def test_invalid_access_tokens_are_rejected(token: str) -> None:
    tokens = JwtAccessTokens(SECRET, timedelta(hours=1), FrozenClock())

    with pytest.raises(InvalidTokenError):
        tokens.verify(token)


def test_livekit_token_grants_room_and_dispatches_agent_with_user() -> None:
    issuer = LiveKitCallTokens("ws://lk.test", "devkey", SECRET, "baatein", timedelta(minutes=15))
    user = make_user(display_name="Asha")

    grant = issuer.issue(user)

    claims = api.TokenVerifier("devkey", SECRET).verify(grant.participant_token)
    assert grant.server_url == "ws://lk.test"
    assert (claims.identity, claims.name) == (user.id, "Asha")
    assert (claims.video.room, claims.video.room_join) == (grant.room_name, True)
    [dispatch] = claims.room_config.agents
    assert dispatch.agent_name == "baatein"
    assert json.loads(dispatch.metadata) == {"user_id": user.id}


def test_livekit_token_falls_back_to_email_and_uses_fresh_rooms() -> None:
    issuer = LiveKitCallTokens("ws://lk.test", "devkey", SECRET, "baatein", timedelta(minutes=5))
    user = make_user(display_name=None)

    first, second = issuer.issue(user), issuer.issue(user)

    claims = api.TokenVerifier("devkey", SECRET).verify(first.participant_token)
    assert claims.name == user.email
    assert first.room_name != second.room_name
    assert room_name_for("ABC").startswith("baatein-abc-")


@pytest.fixture
def service() -> tuple[AuthService, InMemoryUserRepo, RecordingHasher]:
    users = InMemoryUserRepo(InMemoryState())
    hasher = RecordingHasher()
    auth = AuthService(
        users,
        hasher,
        JwtAccessTokens(SECRET, timedelta(hours=1), FrozenClock()),
        default_language_registry(),
        default_persona_registry(),
        FrozenClock(),
        default_language="hinglish",
    )
    return auth, users, hasher


def test_normalize_email() -> None:
    assert normalize_email("  Asha@Example.COM ") == "asha@example.com"


async def test_signup_creates_user_with_normalized_fields(service) -> None:  # type: ignore[no-untyped-def]
    auth, users, _ = service

    user = await auth.signup(" Asha@Example.com ", "password123", "  Asha  ", "english")

    assert await users.get(user.id) == user
    assert (user.email, user.display_name, user.language) == ("asha@example.com", "Asha", "english")
    assert user.password_hash == "hashed:password123"
    assert user.created_at == T0


async def test_signup_defaults_language_and_blank_name(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, _ = service

    user = await auth.signup("a@example.com", "password123", "   ")

    assert (user.language, user.display_name, user.persona) == ("hinglish", None, "casual")


async def test_signup_with_a_persona(service) -> None:  # type: ignore[no-untyped-def]
    auth, users, _ = service

    user = await auth.signup("a@example.com", "password123", persona="elder")

    assert (await users.get(user.id)).persona == "elder"
    with pytest.raises(UnknownPersonaError):
        await auth.signup("b@example.com", "password123", persona="pirate")


async def test_signup_rejects_weak_password_unknown_language_and_duplicates(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, _ = service
    await auth.signup("a@example.com", "password123")

    with pytest.raises(WeakPasswordError, match="at least 8"):
        await auth.signup("b@example.com", "short")
    with pytest.raises(UnknownLanguageError):
        await auth.signup("b@example.com", "password123", language="klingon")
    with pytest.raises(DuplicateEmailError):
        await auth.signup("A@EXAMPLE.COM", "password123")


async def test_login_with_correct_password_any_email_case(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, _ = service
    user = await auth.signup("a@example.com", "password123")

    assert await auth.login("A@Example.com ", "password123") == user


async def test_login_failures_are_indistinguishable(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, hasher = service
    await auth.signup("a@example.com", "password123")

    with pytest.raises(InvalidCredentialsError) as wrong_password:
        await auth.login("a@example.com", "wrong-password")
    with pytest.raises(InvalidCredentialsError) as unknown_email:
        await auth.login("ghost@example.com", "password123")

    assert str(wrong_password.value) == str(unknown_email.value)
    assert len(hasher.verified) == 2


async def test_issue_and_authenticate_token(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, _ = service
    user = await auth.signup("a@example.com", "password123")

    assert await auth.authenticate(auth.issue_token(user)) == user


async def test_authenticate_rejects_token_for_deleted_user(service) -> None:  # type: ignore[no-untyped-def]
    auth, _, _ = service
    ghost_token = JwtAccessTokens(SECRET, timedelta(hours=1), FrozenClock()).issue("ghost")

    with pytest.raises(InvalidTokenError, match="unknown user"):
        await auth.authenticate(ghost_token)
