import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader.db.models import Base


@pytest.fixture()
def db_session():
    """SQLite in-memory session with the full schema created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
