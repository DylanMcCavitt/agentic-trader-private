"""Digest composition from seeded DB rows. No network, no notify."""

from datetime import date

from tests.m4_seed import seed_trading_day
from trader.digest.compose import compose_digest, write_digest

DAY = date(2026, 7, 6)


def test_digest_full_day(db_session):
    seed_trading_day(db_session)
    md = compose_digest(db_session, DAY)

    assert f"# Daily digest — {DAY.isoformat()}" in md
    # P&L: one buy of 10 @ 96 -> equity sleeve cash flow -960
    assert "equity: $-960.00" in md
    # open position marked to the seeded quote (99), unrealized +30
    assert "TQQQ" in md
    assert "$99.00" in md
    assert "$30.00" in md
    # thesis text shown next to the position
    assert "buy on breakout above 95" in md
    # trades with grade
    assert "grade 7.5" in md
    # gate rejection with reason
    assert "per-position cap exceeded" in md
    # params section
    assert "max_trades_per_day: 4" in md  # param_history override applied
    # no halts
    assert "All sleeves active" in md


def test_digest_reports_halts(db_session):
    seed_trading_day(db_session, halted_options=True)
    md = compose_digest(db_session, DAY)
    assert "HALTED" in md
    assert "options" in md


def test_digest_empty_day(db_session):
    md = compose_digest(db_session, date(2026, 1, 2))
    assert "No fills today." in md
    assert "No open positions." in md
    assert "No trades today." in md


def test_write_digest_creates_file(db_session, tmp_path):
    seed_trading_day(db_session)
    md = compose_digest(db_session, DAY)
    path = write_digest(md, DAY, digest_dir=tmp_path, notify=False)
    assert path == tmp_path / "2026-07-06.md"
    assert path.read_text() == md
