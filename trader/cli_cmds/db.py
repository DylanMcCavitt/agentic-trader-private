"""`trader db ...` — database management commands."""

from __future__ import annotations

import argparse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config():
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    return cfg


def cmd_upgrade(args: argparse.Namespace) -> int:
    from alembic import command

    command.upgrade(_alembic_config(), args.revision)
    return 0


def configure(subparsers) -> None:
    db = subparsers.add_parser("db", help="database management")
    db_sub = db.add_subparsers(dest="db_command", required=True)

    upgrade = db_sub.add_parser("upgrade", help="run alembic migrations")
    upgrade.add_argument("revision", nargs="?", default="head")
    upgrade.set_defaults(func=cmd_upgrade)
