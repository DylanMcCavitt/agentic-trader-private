"""Alembic migration smoke test.

Runs `upgrade head` against DATABASE_URL when Postgres is reachable
(CI provides a service container; locally, `docker compose up -d`).
Skips cleanly when no Postgres is available.
"""

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _postgres_url() -> str:
    from trader.db.session import database_url

    return database_url()


def _postgres_available(url: str) -> bool:
    try:
        engine = sa.create_engine(url, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _postgres_available(_postgres_url()),
    reason="Postgres not reachable at DATABASE_URL",
)
def test_upgrade_head_creates_all_tables():
    url = _postgres_url()
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)

    command.upgrade(cfg, "head")

    engine = sa.create_engine(url)
    names = set(sa.inspect(engine).get_table_names())
    for table in (
        "accounts",
        "sleeves",
        "theses",
        "orders",
        "fills",
        "grades",
        "param_history",
        "lane_runs",
    ):
        assert table in names
