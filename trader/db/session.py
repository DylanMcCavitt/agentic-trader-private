"""Engine and session factory.

Reads ``DATABASE_URL`` from the environment; defaults to the local Docker
Compose Postgres (port 5433 to avoid clashing with a system Postgres).
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "postgresql+psycopg://trader:trader@localhost:5433/trader"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url())


def get_session(url: str | None = None) -> Session:
    factory = sessionmaker(bind=get_engine(url), expire_on_commit=False)
    return factory()
