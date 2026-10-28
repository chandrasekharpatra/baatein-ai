import argparse
import asyncio
from collections.abc import Sequence

from baatein.config import Settings
from baatein.container import build_root
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.migrate import migrate


async def reembed(settings: Settings) -> str:
    injector = build_root(settings=settings)
    store = injector.get(SqliteMemoryStore)
    try:
        count = await store.rebuild_index()
    finally:
        await injector.get(Database).close()
    return f"re-embedded {count} facts into {store.vec_table}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baatein")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")
    commands.add_parser("reembed", help="rebuild the fact vector index for the current embedder")
    args = parser.parse_args(argv)

    settings = Settings()
    path = settings.database.sqlite_path()
    version = migrate(path)
    print(f"{path}: schema version {version}")
    if args.command == "reembed":
        print(asyncio.run(reembed(settings)))
    return 0
