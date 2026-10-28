import sqlite3
from pathlib import Path

import pytest

from baatein.cli import main
from baatein.storage.sqlite.migrate import bundled_migrations


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
