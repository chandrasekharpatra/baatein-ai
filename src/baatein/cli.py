import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from baatein.api.app import create_app
from baatein.config import Settings
from baatein.container import build_root
from baatein.memory.seed import Seeder, SeedFile
from baatein.ports.auth import PasswordHasher
from baatein.storage.sqlite.db import Database
from baatein.storage.sqlite.memory_store import SqliteMemoryStore
from baatein.storage.sqlite.migrate import migrate
from baatein.storage.sqlite.repos import delete_user


async def reembed(settings: Settings) -> str:
    injector = build_root(settings=settings)
    store = injector.get(SqliteMemoryStore)
    try:
        count = await store.rebuild_index()
    finally:
        await injector.get(Database).close()
    return f"re-embedded {count} facts into {store.vec_table}"


def read_seed(path: Path) -> SeedFile:
    seed = SeedFile.model_validate_json(path.read_text(encoding="utf-8"))
    if seed.user is None:
        raise SystemExit(f"{path}: a seed file for the CLI needs a 'user' section")
    return seed


async def load_seed(settings: Settings, seed: SeedFile, replace: bool) -> str:
    """Create the account in a seed file's `user` section and load the rest into it."""
    assert seed.user is not None
    injector = build_root(settings=settings)
    db = injector.get(Database)
    try:
        if replace and await delete_user(db, seed.user.email):
            print(f"removed the existing {seed.user.email}")
        seeder = injector.get(Seeder)
        hasher: PasswordHasher = injector.get(PasswordHasher)  # type: ignore[type-abstract]
        user = await seeder.create_user(seed, hasher.hash(seed.user.password))
        result = await seeder.load(user, seed)
    finally:
        await db.close()
    counts = ", ".join(f"{n} {name.replace('_', ' ')}" for name, n in vars(result).items())
    return f"seeded {user.email} ({user.id}): {counts}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baatein")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")
    commands.add_parser("reembed", help="rebuild the fact vector index for the current embedder")
    seed_cmd = commands.add_parser(
        "seed", help="create a test account from a seed file (see docs/SEED_DATA.md)"
    )
    seed_cmd.add_argument("file", type=Path)
    seed_cmd.add_argument(
        "--replace", action="store_true", help="delete that user and their data first"
    )
    serve = commands.add_parser("serve", help="run the HTTP API and web client")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = Settings()
    path = settings.database.sqlite_path()
    version = migrate(path)
    print(f"{path}: schema version {version}")
    if args.command == "reembed":
        print(asyncio.run(reembed(settings)))
    elif args.command == "seed":
        print(asyncio.run(load_seed(settings, read_seed(args.file), args.replace)))
    elif args.command == "serve":
        uvicorn.run(create_app(build_root(settings=settings)), host=args.host, port=args.port)
    return 0
