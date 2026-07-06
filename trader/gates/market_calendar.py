"""US equity market calendar (NYSE) for gate checks.

Part of the trust boundary (``trader/gates/`` — human-only). Regular hours
are 9:30–16:00 US/Eastern; early-close days end at 13:00. Full holidays and
early closes are enumerated explicitly for 2026–2027; dates outside the
covered years fail closed (market treated as closed) so a stale calendar
can never allow off-hours trading.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Years this calendar covers. is_market_open() fails closed outside them.
COVERED_YEARS = {2026, 2027}

# NYSE full holidays (observed dates).
HOLIDAYS: frozenset[date] = frozenset(
    {
        # 2026
        date(2026, 1, 1),    # New Year's Day
        date(2026, 1, 19),   # Martin Luther King Jr. Day
        date(2026, 2, 16),   # Washington's Birthday
        date(2026, 4, 3),    # Good Friday
        date(2026, 5, 25),   # Memorial Day
        date(2026, 6, 19),   # Juneteenth
        date(2026, 7, 3),    # Independence Day (Jul 4 is a Saturday; observed Friday)
        date(2026, 9, 7),    # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
        # 2027
        date(2027, 1, 1),    # New Year's Day
        date(2027, 1, 18),   # Martin Luther King Jr. Day
        date(2027, 2, 15),   # Washington's Birthday
        date(2027, 3, 26),   # Good Friday
        date(2027, 5, 31),   # Memorial Day
        date(2027, 6, 18),   # Juneteenth (Jun 19 is a Saturday; observed Friday)
        date(2027, 7, 5),    # Independence Day (Jul 4 is a Sunday; observed Monday)
        date(2027, 9, 6),    # Labor Day
        date(2027, 11, 25),  # Thanksgiving
        date(2027, 12, 24),  # Christmas (Dec 25 is a Saturday; observed Friday)
    }
)

# 13:00 ET early closes.
EARLY_CLOSES: frozenset[date] = frozenset(
    {
        date(2026, 11, 27),  # day after Thanksgiving
        date(2026, 12, 24),  # Christmas Eve
        date(2027, 11, 26),  # day after Thanksgiving
    }
)


def _to_eastern(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Naive datetimes are ambiguous; treat as UTC (fail toward closed
        # rather than assuming local wall-clock time).
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN)


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS and d.year in COVERED_YEARS


def close_time(d: date) -> time:
    return EARLY_CLOSE if d in EARLY_CLOSES else MARKET_CLOSE


def is_market_open(dt: datetime | None = None) -> bool:
    """True when US equity markets are open at ``dt`` (default: now)."""
    dt = _to_eastern(dt or datetime.now(timezone.utc))
    d = dt.date()
    if not is_trading_day(d):
        return False
    return MARKET_OPEN <= dt.time() < close_time(d)


def market_status(dt: datetime | None = None) -> str:
    """Human-readable status string for gate deny reasons."""
    dt = _to_eastern(dt or datetime.now(timezone.utc))
    d = dt.date()
    if d.year not in COVERED_YEARS:
        return f"calendar does not cover {d.year} (fail closed)"
    if d.weekday() >= 5:
        return f"{d} is a weekend"
    if d in HOLIDAYS:
        return f"{d} is a US market holiday"
    if dt.time() < MARKET_OPEN:
        return f"before market open (9:30 ET) at {dt.time():%H:%M} ET"
    if dt.time() >= close_time(d):
        early = " (early close 13:00 ET)" if d in EARLY_CLOSES else ""
        return f"after market close{early} at {dt.time():%H:%M} ET"
    return "open"
