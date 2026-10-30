import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from injector import Injector, Module, provider, singleton

from baatein import cli
from baatein.auth.service import AuthService
from baatein.cli import load_env as _real_load_env
from baatein.cli import main
from baatein.config import Settings
from baatein.container import build_root
from baatein.ports.analyzer import CallAnalyzer
from baatein.ports.embedder import Embedder
from baatein.ports.repos import SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import bundled_migrations, migrate
from tests.factories import at, make_session, make_turn, make_user
from tests.fakes.analyzer import FakeAnalyzer
from tests.fakes.embedder import FakeEmbedder


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real .env (keys, settings) out of these tests."""
    monkeypatch.setattr(cli, "load_env", lambda: None)


def test_main_loads_env_from_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text("BAATEIN_TEST_ONLY_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BAATEIN_TEST_ONLY_KEY", raising=False)

    _real_load_env()  # the autouse fixture only replaced cli.load_env

    assert os.environ.pop("BAATEIN_TEST_ONLY_KEY") == "from-dotenv"


def test_migrate_creates_database_at_configured_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "db" / "app.db"
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{path}")

    assert main(["migrate"]) == 0

    latest = len(bundled_migrations())
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == latest
    assert f"schema version {latest}" in capsys.readouterr().out


def test_command_is_required() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_reembed_rebuilds_index_with_current_embedder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")

    class FakeEmbedding(Module):
        @singleton
        @provider
        def embedder(self) -> Embedder:
            return FakeEmbedder()

    def build_with_fake(*overrides: Module, settings: Settings | None = None) -> Injector:
        return build_root(*overrides, FakeEmbedding(), settings=settings)

    monkeypatch.setattr(cli, "build_root", build_with_fake)

    assert main(["reembed"]) == 0

    assert "re-embedded 0 facts into facts_vec_fake_bag_of_words_256" in capsys.readouterr().out


def test_serve_runs_api_with_host_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, str, int]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, host, port: calls.append((app, host, port)))

    assert main(["serve", "--host", "0.0.0.0", "--port", "9000"]) == 0

    [(app, host, port)] = calls
    assert isinstance(app, FastAPI)
    assert (host, port) == ("0.0.0.0", 9000)


def fake_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEmbedding(Module):
        @singleton
        @provider
        def embedder(self) -> Embedder:
            return FakeEmbedder()

    def build_with_fake(*overrides: Module, settings: Settings | None = None) -> Injector:
        return build_root(*overrides, FakeEmbedding(), settings=settings)

    monkeypatch.setattr(cli, "build_root", build_with_fake)


def test_seed_creates_the_account_and_replaces_it_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    fake_embedding(monkeypatch)
    seed = tmp_path / "ram.json"
    seed.write_text(
        '{"user": {"email": "ram@example.com", "password": "long-enough"},'
        ' "memories": [{"text": "Loves rasagola", "about": "Sabitri"}]}'
    )

    assert main(["seed", str(seed)]) == 0
    first = capsys.readouterr().out
    assert main(["seed", str(seed), "--replace"]) == 0
    second = capsys.readouterr().out
    assert main(["seed", str(seed), "--if-missing"]) == 0
    third = capsys.readouterr().out

    assert "seeded ram@example.com" in first
    assert "1 people, 0 calls, 1 memories" in first
    assert "replaced ram@example.com" in second
    assert "ram@example.com already exists, left as it is" in third
    with sqlite3.connect(tmp_path / "app.db") as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 1
    with pytest.raises(SystemExit, match="already exists; choose replace"):
        main(["seed", str(seed)])


def test_seed_password_replaces_the_files_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seed files are public in the repo, so a deploy loads them with a secret password."""
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("BAATEIN_AUTH__SEED_PASSWORD", "deploy-secret-1")
    monkeypatch.setenv("BAATEIN_AUTH__JWT_SECRET", "jwt-test-secret-of-sufficient-length")
    fake_embedding(monkeypatch)
    files = []
    for name in ("asha", "ravi"):
        files.append(tmp_path / f"{name}.json")
        files[-1].write_text(f'{{"user": {{"email": "{name}@example.com"}}}}')

    assert main(["seed", *map(str, files), "--if-missing"]) == 0

    async def can_log_in(email: str) -> bool:
        injector = build_root(settings=Settings())
        try:
            await injector.get(AuthService).login(email, "deploy-secret-1")
            return True
        finally:
            await injector.get(Database).close()

    assert asyncio.run(can_log_in("asha@example.com"))
    assert asyncio.run(can_log_in("ravi@example.com"))


def test_create_user_asks_for_the_password_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    monkeypatch.setenv("BAATEIN_AUTH__ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("BAATEIN_AUTH__JWT_SECRET", "jwt-test-secret-of-sufficient-length")
    answers = iter(["boss-password-1", "boss-password-1", "x", "y", "short", "short"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(answers))

    assert main(["create-user", "Boss@example.com", "--name", "Boss"]) == 0
    assert "created boss@example.com (admin)" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="don't match"):
        main(["create-user", "ravi@example.com"])
    with pytest.raises(SystemExit, match="at least 8 characters"):
        main(["create-user", "ravi@example.com"])


def test_forget_user_needs_yes_then_erases_but_keeps_the_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    fake_embedding(monkeypatch)
    seed = tmp_path / "ram.json"
    seed.write_text(
        '{"user": {"email": "ram@example.com", "password": "long-enough"},'
        ' "memories": [{"text": "Loves rasagola", "about": "Sabitri"}]}'
    )
    assert main(["seed", str(seed)]) == 0

    assert main(["forget-user", "ram@example.com"]) == 1
    assert "Add --yes" in capsys.readouterr().out
    assert main(["forget-user", "ram@example.com", "--yes"]) == 0
    assert "erased everything Baatein remembered about ram@example.com" in capsys.readouterr().out
    assert main(["forget-user", "nobody@example.com", "--yes"]) == 0
    assert "no user nobody@example.com" in capsys.readouterr().out
    with sqlite3.connect(tmp_path / "app.db") as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM people").fetchone()[0] == 0
        assert conn.execute("SELECT text FROM facts").fetchall() == [("",)]


def test_metrics_reports_recent_calls_for_everyone_or_one_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    settings = Settings()
    migrate(settings.database.sqlite_path())
    call_id = ended_call_in(settings)

    async def save() -> None:
        injector = build_root(settings=settings)
        try:
            sessions: SessionRepo = injector.get(SessionRepo)  # type: ignore[type-abstract]
            await sessions.save_metrics(call_id, {"reply_ms": [900], "replies": 1})
        finally:
            await injector.get(Database).close()

    asyncio.run(save())
    with sqlite3.connect(tmp_path / "app.db") as conn:
        conn.execute("UPDATE sessions SET started_at = ?", (datetime.now(UTC).isoformat(),))
        [email] = conn.execute("SELECT email FROM users").fetchone()

    assert main(["metrics"]) == 0
    everyone = capsys.readouterr().out
    assert main(["metrics", "--days", "1", "--email", email]) == 0
    one = capsys.readouterr().out
    assert main(["metrics", "--email", "nobody@example.com"]) == 0

    assert "Last 7 day(s)" in everyone
    assert "reply            p50 900 ms, p90 900 ms (1)" in everyone
    assert f"Last 1 day(s) for {email}" in one
    assert "no user nobody@example.com" in capsys.readouterr().out


def test_demo_date_sets_and_clears_the_demo_clock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    assert main(["demo-date", "2026-11-02"]) == 0
    assert "Set BAATEIN_DEV__CLOCK_FILE" in capsys.readouterr().out

    monkeypatch.setenv("BAATEIN_DEV__CLOCK_FILE", str(tmp_path / "clock.json"))
    assert main(["demo-date", "2026-11-02", "--time", "18:30"]) == 0
    assert "it is now Mon 02 Nov 2026, 18:30 (Asia/Kolkata)" in capsys.readouterr().out
    assert (tmp_path / "clock.json").exists()
    assert main(["demo-date", "--clear"]) == 0
    assert "demo clock cleared" in capsys.readouterr().out
    assert main(["demo-date", "--clear"]) == 0
    assert "demo clock was not set" in capsys.readouterr().out
    assert main(["demo-date"]) == 0
    assert "Give a day" in capsys.readouterr().out


def test_seed_without_a_user_section_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    seed = tmp_path / "no-user.json"
    seed.write_text('{"memories": []}')
    broken = tmp_path / "broken.json"
    broken.write_text('{"memories": [{"txt": "x"}]}')

    with pytest.raises(SystemExit, match="needs a 'user' section"):
        main(["seed", str(seed)])
    with pytest.raises(SystemExit, match=r"broken.json: .*txt"):
        main(["seed", str(broken)])


def ended_call_in(settings: Settings) -> str:
    """Add a user and a finished call to the database; returns the call's id."""

    async def add() -> str:
        injector = build_root(settings=settings)
        user = make_user()
        session = make_session(user.id)
        try:
            await injector.get(UserRepo).add(user)  # type: ignore[type-abstract]
            sessions: SessionRepo = injector.get(SessionRepo)  # type: ignore[type-abstract]
            await sessions.add(session)
            await injector.get(TurnRepo).append(
                make_turn(session.id, 1, "Aaj Bapa ki bahut yaad aayi")
            )  # type: ignore[type-abstract]
            await sessions.end(session.id, at(5), None)
        finally:
            await injector.get(Database).close()
        return session.id

    return asyncio.run(add())


def fake_analyzer(monkeypatch: pytest.MonkeyPatch, analyzer: FakeAnalyzer) -> None:
    class FakeAnalysis(Module):
        @singleton
        @provider
        def analyzer(self) -> CallAnalyzer:
            return analyzer

    def build_with_fake(*overrides: Module, settings: Settings | None = None) -> Injector:
        return build_root(*overrides, FakeAnalysis(), settings=settings)

    monkeypatch.setattr(cli, "build_root", build_with_fake)


def test_finalize_summarises_due_calls_or_one_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    main(["migrate"])
    call_id = ended_call_in(Settings())
    fake_analyzer(monkeypatch, FakeAnalyzer())

    assert main(["finalize"]) == 0
    assert "summarised 1 call(s)" in capsys.readouterr().out
    assert main(["finalize", "--session", call_id]) == 0
    assert f"summarised {call_id}" in capsys.readouterr().out
    with sqlite3.connect(tmp_path / "app.db") as conn:
        row = conn.execute("SELECT summary, finalize_status FROM sessions").fetchone()
    assert row == ("A short, light chat about his day.", "done")


def test_finalize_reports_a_failed_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BAATEIN_DATABASE__URL", f"sqlite:///{tmp_path}/app.db")
    main(["migrate"])
    call_id = ended_call_in(Settings())
    fake_analyzer(monkeypatch, FakeAnalyzer(error=RuntimeError("Claude is down")))

    assert main(["finalize", "--session", call_id]) == 0
    assert f"summarising {call_id} failed" in capsys.readouterr().out
