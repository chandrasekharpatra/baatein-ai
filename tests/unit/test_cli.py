import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from injector import Injector, Module, provider, singleton

from baatein import cli
from baatein.cli import main
from baatein.config import Settings
from baatein.container import build_root
from baatein.ports.embedder import Embedder
from baatein.storage.sqlite.migrate import bundled_migrations
from tests.fakes.embedder import FakeEmbedder


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
