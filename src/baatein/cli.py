import argparse
from collections.abc import Sequence

from baatein.config import Settings
from baatein.storage.sqlite.migrate import migrate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baatein")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="apply database migrations")
    parser.parse_args(argv)

    path = Settings().database.sqlite_path()
    version = migrate(path)
    print(f"{path}: schema version {version}")
    return 0
