from pathlib import Path

import pytest
from injector import Module, provider, singleton

from baatein.config import Settings
from baatein.container import build_root
from baatein.memory.embeddings.fastembed import FastEmbedEmbedder
from baatein.ports.clock import Clock, SystemClock
from baatein.ports.embedder import Embedder
from baatein.ports.memory import MemoryStore
from baatein.ports.repos import FollowUpRepo, SessionRepo, TurnRepo, UserRepo
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.repos import (
    SqliteFollowUpRepo,
    SqliteSessionRepo,
    SqliteTurnRepo,
    SqliteUserRepo,
)
from baatein.voice.languages import LanguageProfile, LanguageRegistry, ProviderSpec
from baatein.voice.providers import VoicePipelineFactory


def test_root_binds_given_settings() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert build_root(settings=settings).get(Settings) is settings


def test_pipeline_factory_is_singleton() -> None:
    injector = build_root(settings=Settings(_env_file=None))  # type: ignore[call-arg]

    assert injector.get(VoicePipelineFactory) is injector.get(VoicePipelineFactory)


def test_extra_modules_override_defaults() -> None:
    custom = LanguageRegistry(
        [LanguageProfile("klingon", ProviderSpec("x"), ProviderSpec("y"), "qapla")]
    )

    class Override(Module):
        @singleton
        @provider
        def languages(self) -> LanguageRegistry:
            return custom

    injector = build_root(Override(), settings=Settings(_env_file=None))  # type: ignore[call-arg]

    assert injector.get(LanguageRegistry) is custom


@pytest.mark.parametrize(
    ("port", "adapter"),
    [
        (UserRepo, SqliteUserRepo),
        (SessionRepo, SqliteSessionRepo),
        (TurnRepo, SqliteTurnRepo),
        (FollowUpRepo, SqliteFollowUpRepo),
        (Clock, SystemClock),
    ],
)
def test_ports_resolve_to_default_adapters(port: type, adapter: type) -> None:
    injector = build_root(settings=Settings(_env_file=None))  # type: ignore[call-arg]

    assert isinstance(injector.get(port), adapter)
    assert injector.get(port) is injector.get(port)


def test_repos_share_one_database_at_configured_path(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database={"url": f"sqlite:///{tmp_path}/x.db"})  # type: ignore[call-arg]
    injector = build_root(settings=settings)

    assert injector.get(Database).path == f"{tmp_path}/x.db"


def test_memory_store_is_sqlite_store_with_configured_embedder() -> None:
    injector = build_root(settings=Settings(_env_file=None))  # type: ignore[call-arg]

    store = injector.get(MemoryStore)

    assert store is injector.get(SqliteMemoryStore)
    assert isinstance(injector.get(Embedder), FastEmbedEmbedder)
    assert store.vec_table.endswith("_384")  # type: ignore[attr-defined]
