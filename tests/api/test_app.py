import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from injector import Module, provider, singleton
from livekit import api

from baatein.api.app import create_app
from baatein.auth.passwords import Argon2PasswordHasher
from baatein.config import Settings
from baatein.container import build_root
from baatein.ports.auth import PasswordHasher
from baatein.ports.embedder import Embedder
from baatein.storage.sqlite.migrate import migrate
from tests.fakes.embedder import FakeEmbedder

LIVEKIT_SECRET = "livekit-test-secret-of-sufficient-length"


class CheapHashing(Module):
    @singleton
    @provider
    def hasher(self) -> PasswordHasher:
        return Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database={"url": f"sqlite:///{tmp_path}/api.db"},
        auth={"jwt_secret": "jwt-test-secret-of-sufficient-length"},
        livekit={"url": "ws://lk.test", "api_key": "devkey", "api_secret": LIVEKIT_SECRET},
    )
    migrate(settings.database.sqlite_path())
    with TestClient(create_app(build_root(CheapHashing(), settings=settings))) as test_client:
        yield test_client


def signup(client: TestClient, email: str = "asha@example.com", **extra: object) -> dict:  # type: ignore[type-arg]
    response = client.post(
        "/auth/signup", json={"email": email, "password": "password123", **extra}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_index_serves_web_client(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "<title>Baatein</title>" in response.text
    assert "livekit-client@2.22.3" in response.text


def test_signup_returns_user_and_working_token(client: TestClient) -> None:
    session = signup(client, "Asha@Example.com", display_name="Asha", language="english")

    assert session["token_type"] == "bearer"
    assert session["user"]["email"] == "asha@example.com"
    assert session["user"]["language"] == "english"
    assert session["user"]["persona"] == "casual"
    elder = signup(client, "mohan@example.com", persona="elder")
    assert elder["user"]["persona"] == "elder"
    me = client.get("/me", headers=bearer(session["access_token"]))
    assert me.json() == session["user"]


def test_duplicate_signup_conflicts(client: TestClient) -> None:
    signup(client)

    response = client.post(
        "/auth/signup", json={"email": "ASHA@example.com", "password": "password123"}
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"email": "not-an-email", "password": "password123"},
        {"email": "a@example.com", "password": "short"},
        {"email": "a@example.com", "password": "password123", "language": "klingon"},
        {"email": "a@example.com", "password": "password123", "persona": "pirate"},
        {"email": "a@example.com"},
    ],
    ids=["bad-email", "weak-password", "unknown-language", "unknown-persona", "missing-password"],
)
def test_signup_validation_errors(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/auth/signup", json=body).status_code == 422


def test_login_issues_token(client: TestClient) -> None:
    created = signup(client)

    response = client.post(
        "/auth/login", json={"email": "asha@example.com", "password": "password123"}
    )

    assert response.status_code == 200
    assert response.json()["user"] == created["user"]
    assert client.get("/me", headers=bearer(response.json()["access_token"])).status_code == 200


@pytest.mark.parametrize(
    ("email", "password"),
    [("asha@example.com", "wrong-password"), ("ghost@example.com", "password123")],
)
def test_login_failures_are_401(client: TestClient, email: str, password: str) -> None:
    signup(client)

    response = client.post("/auth/login", json={"email": email, "password": password})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid email or password"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
def test_protected_routes_require_valid_token(client: TestClient, headers: dict[str, str]) -> None:
    assert client.get("/me", headers=headers).status_code == 401
    assert client.post("/livekit/token", headers=headers).status_code == 401


def test_livekit_token_dispatches_agent_for_current_user(client: TestClient) -> None:
    session = signup(client, display_name="Asha")

    response = client.post("/livekit/token", headers=bearer(session["access_token"]))

    assert response.status_code == 200
    body = response.json()
    assert body["server_url"] == "ws://lk.test"
    claims = api.TokenVerifier("devkey", LIVEKIT_SECRET).verify(body["participant_token"])
    assert claims.identity == session["user"]["id"]
    assert claims.video.room == body["room_name"]
    [dispatch] = claims.room_config.agents
    assert json.loads(dispatch.metadata) == {"user_id": session["user"]["id"]}


def seeding_client(tmp_path: Path, enabled: bool) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database={"url": f"sqlite:///{tmp_path}/seed.db"},
        auth={"jwt_secret": "jwt-test-secret-of-sufficient-length"},
        dev={"seed_enabled": enabled},
    )
    migrate(settings.database.sqlite_path())
    return TestClient(create_app(build_root(CheapHashing(), FakeEmbedding(), settings=settings)))


class FakeEmbedding(Module):
    @singleton
    @provider
    def embedder(self) -> Embedder:
        return FakeEmbedder()


def test_seed_routes_exist_only_when_enabled(tmp_path: Path) -> None:
    with seeding_client(tmp_path, enabled=False) as off:
        token = signup(off)["access_token"]
        assert off.get("/dev/seed").status_code == 404
        response = off.post("/dev/seed", json={}, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code in (404, 405)


def test_seed_loads_into_the_current_user(tmp_path: Path) -> None:
    with seeding_client(tmp_path, enabled=True) as on:
        token = signup(on, display_name="Mohan")["access_token"]
        auth = {"Authorization": f"Bearer {token}"}
        assert on.get("/dev/seed").json() == {"enabled": True}

        response = on.post(
            "/dev/seed",
            json={
                "persona": "elder",
                "memories": [{"text": "Misses Ramesh", "about": "Ramesh"}],
                "reminders": [{"title": "Ravi's birthday", "date": "today+5"}],
            },
            headers=auth,
        )

        assert response.status_code == 200
        assert response.json() == {
            "people": 1,
            "calls": 0,
            "memories": 1,
            "threads": 0,
            "boundaries": 0,
            "declined_suggestions": 0,
            "reminders": 1,
        }
        assert on.get("/me", headers=auth).json()["persona"] == "elder"
        assert (
            on.post("/dev/seed", json={"memories": [{"txt": "x"}]}, headers=auth).status_code == 422
        )
        bad_call = {"memories": [{"text": "x", "call": "nope"}]}
        assert on.post("/dev/seed", json=bad_call, headers=auth).status_code == 422
        assert on.post("/dev/seed", json={}).status_code == 401
