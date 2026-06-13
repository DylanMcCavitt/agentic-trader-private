#!/usr/bin/env python3
"""Deterministic US equity-market calendar (stdlib only).

The order/option gates run as fast PreToolUse hooks and import only stdlib —
no pandas, no pandas_market_calendars. This module hard-codes the NYSE/Nasdaq
full-closure holidays and half-day early closes so a weekday holiday at 10:30
ET is blocked deterministically (it would otherwise pass the weekday+hours
check) and run.sh can skip the run entirely on holidays and half-days.

Dates are the *observed* market dates (the day the exchange is actually
closed), encoded as "YYYY-MM-DD" strings. Maintain this list yearly.

CLI contract (consumed by run.sh):
    python3 scripts/market_calendar.py --is-trading-day YYYY-MM-DD
        exit 0  -> it IS a normal full trading day
        exit 1  -> weekend, holiday, OR early-close half-day
    python3 scripts/market_calendar.py --is-holiday YYYY-MM-DD
        exit 0  -> it is a full-closure holiday
        exit 1  -> it is not
run.sh's trade window is 15:30-15:58 ET, which is AFTER a 13:00 early close,
so --is-trading-day rejects half-days too: a half-day must be a clean no-op.
"""
import sys
from datetime import date

# US equity-market FULL-CLOSURE dates (observed). Update yearly.
HOLIDAYS = frozenset({
    # 2026
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Washington's Birthday
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-06-19",  # Juneteenth
    "2026-07-03",  # Independence Day (observed, Jul 4 is Sat)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
    # 2027
    "2027-01-01",  # New Year's Day
    "2027-01-18",  # Martin Luther King Jr. Day
    "2027-02-15",  # Washington's Birthday
    "2027-03-26",  # Good Friday
    "2027-05-31",  # Memorial Day
    "2027-06-18",  # Juneteenth (observed, Jun 19 is Sat)
    "2027-07-05",  # Independence Day (observed, Jul 4 is Sun)
    "2027-09-06",  # Labor Day
    "2027-11-25",  # Thanksgiving Day
    "2027-12-24",  # Christmas Day (observed, Dec 25 is Sat)
})

# Early-close half-days -> early close as minutes-since-midnight ET (13:00).
EARLY_CLOSE_MINUTES = 13 * 60  # 780
EARLY_CLOSES = {
    # 2026
    "2026-11-27": EARLY_CLOSE_MINUTES,  # day after Thanksgiving
    "2026-12-24": EARLY_CLOSE_MINUTES,  # Christmas Eve
    # 2027
    "2027-11-26": EARLY_CLOSE_MINUTES,  # day after Thanksgiving
    "2027-12-23": EARLY_CLOSE_MINUTES,  # Christmas Eve
}


def _key(d) -> str:
    """Normalize a date or 'YYYY-MM-DD' string to the calendar key."""
    if isinstance(d, date):
        return d.isoformat()
    return str(d)


def is_market_holiday(d) -> bool:
    """True if d is a full-closure market holiday. Accepts date or string."""
    return _key(d) in HOLIDAYS


def early_close_minutes(d):
    """Early-close minute-of-day (e.g. 780 for 13:00) if d is a half-day,
    else None. Accepts date or 'YYYY-MM-DD' string."""
    return EARLY_CLOSES.get(_key(d))


def is_trading_day(d) -> bool:
    """True if d is a normal full trading day: a weekday, not a holiday, and
    not an early-close half-day. Accepts date or 'YYYY-MM-DD' string."""
    key = _key(d)
    if isinstance(d, date):
        dt = d
    else:
        dt = date.fromisoformat(key)
    if dt.weekday() > 4:  # 5=Sat, 6=Sun
        return False
    if key in HOLIDAYS:
        return False
    if key in EARLY_CLOSES:
        return False
    return True


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: market_calendar.py "
              "[--is-trading-day|--is-holiday] YYYY-MM-DD", file=sys.stderr)
        return 2
    flag, value = argv
    try:
        d = date.fromisoformat(value)
    except ValueError:
        print(f"invalid date {value!r} (expected YYYY-MM-DD)", file=sys.stderr)
        return 2
    if flag == "--is-trading-day":
        return 0 if is_trading_day(d) else 1
    if flag == "--is-holiday":
        return 0 if is_market_holiday(d) else 1
    print(f"unknown flag {flag!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
