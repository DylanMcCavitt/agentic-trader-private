"""Strategy registry: maps the `signal` name in config.json to a function.

Every signal shares one contract (see signals.py):

    signal(df, params) -> {"entry": bool, "exit": bool, "reason": str, "metrics": {...}}

The engine (scripts/run_strategies.py) turns entry/exit into BUY/SELL for
equity books and OPEN/CLOSE for option books, so one signal can drive both
an equity strategy and an options strategy.
"""
from . import signals

SIGNALS = {
    "rsi2_long": signals.rsi2_long,
    "ibs_long": signals.ibs_long,
    "bollinger_long": signals.bollinger_long,
    "donchian_long": signals.donchian_long,
    "rsi2_short": signals.rsi2_short,
    "donchian_short": signals.donchian_short,
}

# Vectorized twins of SIGNALS, used by scripts/backtest_fleet.py.
SIGNAL_SERIES = {
    "rsi2_long": signals.rsi2_long_series,
    "ibs_long": signals.ibs_long_series,
    "bollinger_long": signals.bollinger_long_series,
    "donchian_long": signals.donchian_long_series,
    "rsi2_short": signals.rsi2_short_series,
    "donchian_short": signals.donchian_short_series,
}
