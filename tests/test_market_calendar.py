from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from trader.gates import market_calendar as cal

ET = ZoneInfo("America/New_York")


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_regular_day_open_hours():
    assert cal.is_market_open(et(2026, 7, 6, 10, 0))
    assert cal.is_market_open(et(2026, 7, 6, 9, 30))
    assert not cal.is_market_open(et(2026, 7, 6, 9, 29))
    assert not cal.is_market_open(et(2026, 7, 6, 16, 0))
    assert cal.is_market_open(et(2026, 7, 6, 15, 59))


def test_weekend_closed():
    assert not cal.is_market_open(et(2026, 7, 4, 11, 0))  # Saturday
    assert not cal.is_market_open(et(2026, 7, 5, 11, 0))  # Sunday
    assert "weekend" in cal.market_status(et(2026, 7, 4, 11, 0))


def test_holidays_closed():
    for d in [
        et(2026, 1, 1, 11), et(2026, 1, 19, 11), et(2026, 2, 16, 11),
        et(2026, 4, 3, 11), et(2026, 5, 25, 11), et(2026, 6, 19, 11),
        et(2026, 7, 3, 11), et(2026, 9, 7, 11), et(2026, 11, 26, 11),
        et(2026, 12, 25, 11),
        et(2027, 1, 1, 11), et(2027, 1, 18, 11), et(2027, 3, 26, 11),
        et(2027, 6, 18, 11), et(2027, 7, 5, 11), et(2027, 11, 25, 11),
        et(2027, 12, 24, 11),
    ]:
        assert not cal.is_market_open(d), d
    assert "holiday" in cal.market_status(et(2026, 11, 26, 11))


def test_early_close():
    assert cal.is_market_open(et(2026, 11, 27, 12, 59))
    assert not cal.is_market_open(et(2026, 11, 27, 13, 0))
    assert not cal.is_market_open(et(2026, 12, 24, 14, 0))
    assert cal.is_market_open(et(2027, 11, 26, 10, 0))
    assert not cal.is_market_open(et(2027, 11, 26, 13, 30))


def test_uncovered_year_fails_closed():
    assert not cal.is_market_open(et(2028, 3, 6, 11, 0))  # a normal Monday in 2028
    assert not cal.is_trading_day(date(2025, 7, 7))
    assert "does not cover" in cal.market_status(et(2028, 3, 6, 11, 0))


def test_naive_datetime_treated_as_utc():
    # 15:00 naive == 15:00 UTC == 11:00 ET in July -> open
    assert cal.is_market_open(datetime(2026, 7, 6, 15, 0))
    # 15:00 UTC explicitly
    assert cal.is_market_open(datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc))
    # 04:00 naive == 00:00 ET -> closed
    assert not cal.is_market_open(datetime(2026, 7, 6, 4, 0))
