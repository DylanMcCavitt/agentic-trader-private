"""Journal writer: rendering, idempotency, multi-day ordering."""

from datetime import date, datetime, timezone
from decimal import Decimal

from tests.m4_seed import seed_trading_day
from trader.db.models import LaneRun
from trader.journal.writer import render_day_section, upsert_day

DAY = date(2026, 7, 6)


def test_render_day_section_contains_all_event_types(db_session):
    seed_trading_day(db_session)
    section = render_day_section(db_session, DAY)

    assert section.startswith(f"## {DAY.isoformat()}")
    assert "research: succeeded — 3 candidates" in section
    assert "TQQQ long etf" in section
    assert "buy on breakout above 95" in section
    assert "buy TQQQ qty 10" in section
    assert "filled 10 @ 96.00" in section
    assert "7.5 — good entry, exit plan held" in section
    assert "max_trades_per_day: 3 -> 4 (improve-lane)" in section


def test_render_includes_halts(db_session):
    seed_trading_day(db_session, halted_options=True)
    section = render_day_section(db_session, DAY)
    assert "**HALT**: options sleeve is halted" in section


def test_upsert_is_idempotent(db_session, tmp_path):
    seed_trading_day(db_session)
    path1 = upsert_day(db_session, DAY, journal_dir=tmp_path)
    first = path1.read_text()
    path2 = upsert_day(db_session, DAY, journal_dir=tmp_path)
    assert path1 == path2 == tmp_path / "2026" / "07.md"
    assert path2.read_text() == first
    assert first.count(f"## {DAY.isoformat()}") == 1


def test_rerun_replaces_days_section_with_new_data(db_session, tmp_path):
    seed_trading_day(db_session)
    upsert_day(db_session, DAY, journal_dir=tmp_path)

    db_session.add(
        LaneRun(
            account_id=1,
            lane="review",
            started_at=datetime(2026, 7, 6, 21, 0, tzinfo=timezone.utc),
            status="succeeded",
            summary="1 trade graded",
        )
    )
    db_session.commit()

    path = upsert_day(db_session, DAY, journal_dir=tmp_path)
    text = path.read_text()
    assert "review: succeeded — 1 trade graded" in text
    assert text.count(f"## {DAY.isoformat()}") == 1


def test_multiple_days_sorted_in_month_file(db_session, tmp_path):
    seed_trading_day(db_session)
    # write later day first, then an earlier day — file must sort by date
    upsert_day(db_session, date(2026, 7, 6), journal_dir=tmp_path)
    upsert_day(db_session, date(2026, 7, 2), journal_dir=tmp_path)

    text = (tmp_path / "2026" / "07.md").read_text()
    assert text.index("## 2026-07-02") < text.index("## 2026-07-06")
    assert text.startswith("# Journal — 2026-07")


def test_days_in_different_months_go_to_different_files(db_session, tmp_path):
    upsert_day(db_session, date(2026, 6, 30), journal_dir=tmp_path)
    upsert_day(db_session, date(2026, 7, 1), journal_dir=tmp_path)
    assert (tmp_path / "2026" / "06.md").exists()
    assert (tmp_path / "2026" / "07.md").exists()
