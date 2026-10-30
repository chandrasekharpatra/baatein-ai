import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from injector import Module, provider, singleton
from livekit import api

from baatein.api.app import create_app
from baatein.auth.passwords import Argon2PasswordHasher
from baatein.config import Settings
from baatein.container import build_root
from baatein.domain.models import Session
from baatein.ports.analyzer import CallAnalyzer
from baatein.ports.auth import PasswordHasher
from baatein.ports.embedder import Embedder
from baatein.ports.repos import SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.migrate import migrate
from tests.factories import at, make_session, make_turn, make_user
from tests.fakes.analyzer import FakeAnalyzer
from tests.fakes.embedder import FakeEmbedder

LIVEKIT_SECRET = "livekit-test-secret-of-sufficient-length"


class CheapHashing(Module):
    @singleton
    @provider
    def hasher(self) -> PasswordHasher:
        return Argon2PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


class FakeEmbedding(Module):
    @singleton
    @provider
    def embedder(self) -> Embedder:
        return FakeEmbedder()


ADMIN = "admin@example.com"


def make_client(tmp_path: Path, signup_enabled: bool = True) -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database={"url": f"sqlite:///{tmp_path}/api.db"},
        auth={
            "jwt_secret": "jwt-test-secret-of-sufficient-length",
            "signup_enabled": signup_enabled,
            "admin_emails": f"{ADMIN.upper()}, boss@example.com",
        },
        livekit={"url": "ws://lk.test", "api_key": "devkey", "api_secret": LIVEKIT_SECRET},
        finalize={"interval_seconds": 0},
    )
    migrate(settings.database.sqlite_path())
    return TestClient(create_app(build_root(CheapHashing(), FakeEmbedding(), settings=settings)))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with make_client(tmp_path) as test_client:
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
    assert 'id="transcript"' not in response.text  # what was said isn't shown on the page
    assert "/me/forget" in response.text


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


def test_signup_is_closed_by_default(tmp_path: Path) -> None:
    with make_client(tmp_path, signup_enabled=False) as closed:
        assert closed.get("/auth/signup").json() == {"enabled": False}
        response = closed.post(
            "/auth/signup", json={"email": "asha@example.com", "password": "password123"}
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "signup is closed; ask an admin for an account"}
    assert Settings(_env_file=None).auth.signup_enabled is False  # type: ignore[call-arg]


def test_admins_are_named_in_settings(client: TestClient) -> None:
    assert client.get("/auth/signup").json() == {"enabled": True}
    admin = signup(client, ADMIN)
    other = signup(client)

    assert admin["user"]["is_admin"] is True
    assert other["user"]["is_admin"] is False
    assert client.get("/me", headers=bearer(admin["access_token"])).json()["is_admin"] is True


SEED = {
    "user": {"email": "Mohan@Example.com", "password": "mohan-test-1", "display_name": "Mohan"},
    "persona": "elder",
    "preferences": {"address_as": "Mohan ji", "pace": "slow"},
    "people": [{"name": "Ravi", "relation": "son"}],
    "calls": [
        {
            "key": "c1",
            "days_ago": 2,
            "at": "19:00",
            "minutes": 12,
            "summary": "Spoke about the mela.",
            "notes": ["Ask about Ramesh"],
            "turns": [{"role": "user", "text": "Mela yaad hai?"}],
        },
        {"key": "c2", "days_ago": 1, "minutes": 3, "ended_abruptly": True},
    ],
    "memories": [
        {"key": "noida", "text": "Ravi lives in Noida", "about": "Ravi", "call": "c1"},
        {"corrects": "noida", "text": "Ravi lives in Dwarka", "about": "Ravi", "call": "c2"},
        {"text": "Loves chai", "kind": "preference", "importance": 4},
    ],
    "threads": [
        {
            "key": "paint",
            "title": "Painting again",
            "status": "in_progress",
            "check_in_after": "today+2",
            "last_offered_days_ago": 3,
            "times_offered": 2,
            "call": "c1",
            "events": [{"text": "Bought brushes", "status": "in_progress", "call": "c2"}],
        }
    ],
    "boundaries": [{"topic": "painting", "thread": "paint", "call": "c2"}],
    "declined_suggestions": ["go for a walk", {"text": "yoga", "call": "c1"}],
    "reminders": [
        {"title": "Ravi's birthday", "date": "today+5", "repeat": "yearly", "about": "Ravi"},
        {
            "title": "Pills",
            "date": "today-3",
            "repeat": "weekly",
            "time": "08:30",
            "settled": [{"date": "today-3", "status": "done"}],
        },
        {"title": "Visit", "status": "cancelled", "call": "c1"},
        {"title": "June trip"},
    ],
}


def admin_auth(client: TestClient) -> dict[str, str]:
    return bearer(signup(client, ADMIN)["access_token"])


def upload(client: TestClient, auth: dict[str, str], **body: object) -> Any:
    return client.post("/admin/accounts", json={"seed": SEED, **body}, headers=auth)


def test_admin_routes_are_for_admins_only(client: TestClient) -> None:
    user = bearer(signup(client)["access_token"])

    for headers, code in (({}, 401), (user, 403)):
        assert client.get("/admin/accounts", headers=headers).status_code == code
        assert upload(client, headers).status_code == code
        assert client.get("/admin/accounts/x/seed", headers=headers).status_code == code
    assert client.get("/admin/accounts", headers=user).json() == {
        "detail": "only admins can do this"
    }


def test_an_admin_loads_lists_and_downloads_an_account(client: TestClient) -> None:
    auth = admin_auth(client)

    loaded = upload(client, auth)

    assert loaded.status_code == 201, loaded.text
    body = loaded.json()
    assert body["replaced"] is False
    assert body["account"]["email"] == "mohan@example.com"
    assert body["account"]["persona"] == "elder"
    assert body["added"] == {
        "people": 1,
        "calls": 2,
        "memories": 3,
        "threads": 1,
        "boundaries": 1,
        "declined_suggestions": 2,
        "reminders": 4,
    }
    mohan = client.post(
        "/auth/login", json={"email": "mohan@example.com", "password": "mohan-test-1"}
    )
    assert mohan.status_code == 200
    accounts = {a["email"]: a for a in client.get("/admin/accounts", headers=auth).json()}
    assert accounts["mohan@example.com"]["memories"] == 2  # Noida was corrected
    assert accounts["mohan@example.com"]["calls"] == 2
    assert accounts["mohan@example.com"]["last_call_at"] is not None
    assert accounts[ADMIN]["is_admin"] is True
    download = client.get(f"/admin/accounts/{body['account']['id']}/seed", headers=auth)
    assert download.headers["content-disposition"] == 'attachment; filename="mohan.json"'
    assert "password" not in download.json()["user"]


def test_a_download_loads_back_as_the_same_account(client: TestClient) -> None:
    auth = admin_auth(client)
    first_id = upload(client, auth).json()["account"]["id"]
    first = client.get(f"/admin/accounts/{first_id}/seed", headers=auth).json()

    again = client.post(
        "/admin/accounts", json={"seed": first, "replace": True}, headers=auth
    ).json()
    second = client.get(f"/admin/accounts/{again['account']['id']}/seed", headers=auth).json()

    assert again["replaced"] is True
    assert again["account"]["id"] != first_id
    assert second == first
    assert first["memories"][:2] == [
        {"key": "m1", "text": "Ravi lives in Noida", "about": "Ravi", "call": "c1"},
        {"corrects": "m1", "text": "Ravi lives in Dwarka", "about": "Ravi", "call": "c2"},
    ]
    assert first["boundaries"] == [{"topic": "painting", "thread": "t1", "call": "c2"}]
    assert first["reminders"][2]["settled"] == [{"date": "today-3", "status": "done"}]
    assert first["threads"][0]["times_offered"] == 2
    # The password stayed, though the downloaded file had none.
    login = {"email": "mohan@example.com", "password": "mohan-test-1"}
    assert client.post("/auth/login", json=login).status_code == 200
    assert client.get(f"/admin/accounts/{first_id}/seed", headers=auth).status_code == 404


def test_loading_refuses_what_would_go_wrong(client: TestClient) -> None:
    auth = admin_auth(client)
    upload(client, auth)
    no_password = {**SEED, "user": {"email": "new@example.com"}}
    bad_reference = {**SEED, "memories": [{"text": "x", "call": "nope"}]}
    admin_file = {**SEED, "user": {"email": "Boss@example.com", "password": "password123"}}

    def detail(response: Any) -> tuple[int, str]:
        return response.status_code, response.json()["detail"]

    assert detail(upload(client, auth)) == (
        409,
        "mohan@example.com already exists; choose replace to load it again",
    )
    assert detail(client.post("/admin/accounts", json={"seed": no_password}, headers=auth)) == (
        422,
        "new@example.com is a new account, so it needs a password",
    )
    assert detail(client.post("/admin/accounts", json={"seed": bad_reference}, headers=auth)) == (
        422,
        "seed: Value error, unknown call 'nope'",
    )
    assert detail(client.post("/admin/accounts", json={"seed": admin_file}, headers=auth)) == (
        422,
        "boss@example.com is an admin; admin accounts aren't loaded from files",
    )
    assert detail(upload(client, auth, replace=True, password="short")) == (
        422,
        "password must be at least 8 characters",
    )
    assert detail(
        client.post("/admin/accounts", json={"seed": {"persona": "elder"}}, headers=auth)
    ) == (422, "the file needs a 'user' section naming the account")
    too_big = client.post("/admin/accounts", content=b" " * 1_000_001, headers=auth)
    assert too_big.status_code == 413


def test_a_failed_replace_keeps_the_old_account(client: TestClient) -> None:
    auth = admin_auth(client)
    upload(client, auth)
    unknown_persona = {**SEED, "persona": "pirate"}
    before = client.get("/admin/accounts", headers=auth).json()

    response = client.post(
        "/admin/accounts",
        json={"seed": unknown_persona, "replace": True, "password": "new-password-1"},
        headers=auth,
    )

    assert response.status_code == 422
    assert client.get("/admin/accounts", headers=auth).json() == before
    login = {"email": "mohan@example.com", "password": "mohan-test-1"}
    assert client.post("/auth/login", json=login).status_code == 200


def test_forget_me_needs_the_password_then_erases_memories_but_keeps_the_account(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as app:
        mohan = {"email": "mohan@example.com", "password": "password123", "display_name": "Mohan"}
        seed = {"user": mohan, "memories": [{"text": "Misses Ramesh", "about": "Ramesh"}]}
        assert app.post("/admin/accounts", json={"seed": seed}, headers=admin_auth(app)).is_success
        auth = bearer(app.post("/auth/login", json=mohan).json()["access_token"])

        wrong = app.post("/me/forget", json={"password": "not-it"}, headers=auth)
        erased = app.post("/me/forget", json={"password": "password123"}, headers=auth)

        assert wrong.status_code == 401
        assert erased.status_code == 204
        assert app.get("/me", headers=auth).json()["display_name"] == "Mohan"
        assert app.post("/me/forget", json={"password": "password123"}).status_code == 401
    with sqlite3.connect(tmp_path / "api.db") as conn:
        assert conn.execute("SELECT count(*) FROM people").fetchone()[0] == 0
        assert conn.execute("SELECT text FROM facts").fetchall() == [("",)]


def test_the_api_summarises_finished_calls_in_the_background(tmp_path: Path) -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        database={"url": f"sqlite:///{tmp_path}/summaries.db"},
        auth={"jwt_secret": "jwt-test-secret-of-sufficient-length"},
        finalize={"interval_seconds": 0.01},
    )
    migrate(settings.database.sqlite_path())
    analyzer = FakeAnalyzer()

    class FakeAnalysis(Module):
        @singleton
        @provider
        def analyzer(self) -> CallAnalyzer:
            return analyzer

    injector = build_root(CheapHashing(), FakeEmbedding(), FakeAnalysis(), settings=settings)
    sessions: SessionRepo = injector.get(SessionRepo)  # type: ignore[type-abstract]

    async def ended_call() -> Session:
        user = make_user()
        await injector.get(UserRepo).add(user)  # type: ignore[type-abstract]
        session = make_session(user.id)
        await sessions.add(session)
        await injector.get(TurnRepo).append(make_turn(session.id, 1, "Aaj Bapa ki bahut yaad aayi"))  # type: ignore[type-abstract]
        await sessions.end(session.id, at(5), None)
        return session

    async def summary(session_id: str) -> str | None:
        stored = await sessions.get(session_id)
        assert stored is not None
        return stored.summary

    with TestClient(create_app(injector)) as client:
        session = client.portal.call(ended_call)  # type: ignore[union-attr]
        for _ in range(200):
            if client.portal.call(summary, session.id):  # type: ignore[union-attr]
                break
            time.sleep(0.01)
        assert client.portal.call(summary, session.id) == "A short, light chat about his day."  # type: ignore[union-attr]
