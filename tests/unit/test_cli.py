import asyncio
import os
import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from injector import Injector, Module, provider, singleton

from baatein import cli
from baatein.cli import load_env as _real_load_env
from baatein.cli import main
from baatein.config import Settings
from baatein.container import build_root
from baatein.ports.analyzer import CallAnalyzer
from baatein.ports.embedder import Embedder
from baatein.ports.repos import SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.migrate import bundled_migrations
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

    assert "seeded ram@example.com" in first
    assert "1 people, 0 calls, 1 memories" in first
    assert "removed the existing ram@example.com" in second
    with sqlite3.connect(tmp_path / "app.db") as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM facts").fetchone()[0] == 1


def test_seed_without_a_user_section_is_refused(tmp_path: Path) -> None:
    seed = tmp_path / "no-user.json"
    seed.write_text('{"memories": []}')

    with pytest.raises(SystemExit, match="needs a 'user' section"):
        main(["seed", str(seed)])


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
            await injector.get(TurnRepo).append(make_turn(session.id, 1, "Bas theek"))  # type: ignore[type-abstract]
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
