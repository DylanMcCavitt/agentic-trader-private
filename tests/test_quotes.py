"""Quote snapshot writing against SQLite in-memory. No network."""

from datetime import datetime, timezone

from sqlalchemy import select

from trader.db.models import Quote
from trader.screener.quotes import QuoteData, snapshot


def fake_fetch(symbols):
    return [
        QuoteData(
            symbol=s,
            price=100.5,
            bid=100.4,
            ask=100.6,
            volume=1_234_567,
            avg_dollar_volume=75_000_000.0,
            ts=datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc),
        )
        for s in symbols
        if s != "MISSING"
    ]


def test_snapshot_writes_rows(db_session):
    rows = snapshot(["AAPL", "TQQQ"], db_session, fetch=fake_fetch)
    assert len(rows) == 2

    stored = list(db_session.execute(select(Quote).order_by(Quote.symbol)).scalars())
    assert [q.symbol for q in stored] == ["AAPL", "TQQQ"]
    q = stored[0]
    assert float(q.price) == 100.5
    assert float(q.bid) == 100.4
    assert float(q.ask) == 100.6
    assert q.payload == {"volume": 1_234_567}
    assert float(q.avg_dollar_volume) == 75_000_000.0
    assert q.quoted_at is not None
    assert q.account_id is not None
    assert q.kind == "equity"


def test_snapshot_normalizes_symbols(db_session):
    rows = snapshot(["brk.b"], db_session, fetch=fake_fetch)
    assert rows[0].symbol == "BRK-B"


def test_snapshot_partial_failure_writes_what_it_can(db_session):
    rows = snapshot(["AAPL", "MISSING"], db_session, fetch=fake_fetch)
    assert [r.symbol for r in rows] == ["AAPL"]


def test_snapshot_total_failure_writes_nothing(db_session):
    rows = snapshot(["MISSING"], db_session, fetch=fake_fetch)
    assert rows == []
    assert db_session.execute(select(Quote)).first() is None
