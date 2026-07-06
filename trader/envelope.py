"""HARD OUTER ENVELOPE — human-only file.

============================================================================
THIS FILE IS NEVER MACHINE-EDITABLE.

The IMPROVE lane (and every other agent, lane, script, or automation) may
READ these constants but must NEVER modify this file. Tunable runtime
parameters live in the database (see trader/params.py) and may move freely
*inside* these bounds; nothing automated may change the bounds themselves.
Changes to this file require a human commit with explicit intent.
============================================================================

All fractions are of total account equity unless noted. "start" values are
the defaults new params are seeded with; they must sit inside the bounds.
"""

# --- Options sleeve budget (fraction of account allocated to options) -----
OPTIONS_SLEEVE_BUDGET_MIN = 0.10
OPTIONS_SLEEVE_BUDGET_MAX = 0.35
OPTIONS_SLEEVE_BUDGET_DEFAULT = 0.25

# --- Per-position size cap (fraction of account per position) -------------
PER_POSITION_MIN = 0.02
PER_POSITION_MAX = 0.08
PER_POSITION_DEFAULT = 0.05

# --- Concurrent open positions (count, across sleeves) --------------------
CONCURRENT_POSITIONS_MIN = 2
CONCURRENT_POSITIONS_MAX = 8
CONCURRENT_POSITIONS_DEFAULT = 5

# --- Per-sleeve drawdown halt (fraction from sleeve high-water mark) ------
SLEEVE_DRAWDOWN_HALT_MIN = 0.10
SLEEVE_DRAWDOWN_HALT_MAX = 0.25
SLEEVE_DRAWDOWN_HALT_DEFAULT = 0.15

# --- Account kill-switch ---------------------------------------------------
# FIXED, not a range. Trading halts entirely when account equity is 30%
# below its high-water mark. HUMAN-ONLY: not tunable by IMPROVE or any
# other automated process under any circumstances.
ACCOUNT_KILL_SWITCH_DRAWDOWN = 0.30

# --- Trade cadence (live orders per trading day, across sleeves) ----------
TRADES_PER_DAY_MIN = 1
TRADES_PER_DAY_MAX = 6
TRADES_PER_DAY_DEFAULT = 3

# --- Options DTE window (days to expiration for any option bought) --------
DTE_MIN = 5
DTE_MAX = 60
DTE_WINDOW_DEFAULT_MIN = 7
DTE_WINDOW_DEFAULT_MAX = 45
