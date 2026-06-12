"""Tests for scripts/order_gate.py — every block path and the allow paths.

The gate is exercised as a subprocess (the way the hook runner invokes it),
asserting on exit code and the stderr reason. Hermetic seams:

- ORDER_GATE_ROOT points the gate at a temp repo root (fixture config/state).
- ORDER_GATE_NOW pins the clock so market-hours tests never flake.

No real account values appear anywhere; fixtures use obvious placeholders.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "order_gate.py"
ORDER_TOOL = "mcp__robinhood__place_equity_order"
FAKE_ACCOUNT = "TEST-FAKE-ACCOUNT-000"  # obviously not a real account number

# Wednesday 2026-06-10, 10:30 ET — regular market hours.
MARKET_OPEN_NOW = "2026-06-10T10:30:00"
SATURDAY_NOW = "2026-06-13T10:30:00"
AFTER_HOURS_NOW = "2026-06-10T17:30:00"

BASE_CONFIG = {
    "symbol": "SPY",
    "account_number": "REPLACE_ME",
    "dry_run": False,
    "max_order_usd": 500,
}
BASE_STATE = {
    "hwm": 0,
    "halt": False,
    "halt_reason": None,
    "last_run": None,
    "last_action": None,
    "position_opened": None,
}
_OMIT = object()


def make_root(tmp_path, *, config=_OMIT, local=_OMIT, state=_OMIT):
    """Build a fixture repo root. Pass None to omit a file, a str for raw
    (corrupt) content, or a dict to override the defaults."""
    if config is _OMIT:
        config = BASE_CONFIG
    if local is _OMIT:
        local = {"account_number": FAKE_ACCOUNT, "dry_run": False}
    if state is _OMIT:
        state = BASE_STATE
    (tmp_path / "state").mkdir()
    for name, content in [
        ("config.json", config),
        ("config.local.json", local),
        ("state/state.json", state),
    ]:
        if content is None:
            continue
        text = content if isinstance(content, str) else json.dumps(content)
        (tmp_path / name).write_text(text)
    return tmp_path


def valid_buy(**overrides):
    order = {
        "account_number": FAKE_ACCOUNT,
        "symbol": "SPY",
        "side": "buy",
        "type": "market",
        "dollar_amount": 100,
    }
    order.update(overrides)
    return order


def run_gate(root, order, *, now=MARKET_OPEN_NOW, tool_name=ORDER_TOOL):
    env = os.environ.copy()
    env["ORDER_GATE_ROOT"] = str(root)
    env["ORDER_GATE_NOW"] = now
    payload = {"tool_name": tool_name, "tool_input": order}
    return subprocess.run(
        [sys.executable, str(GATE)],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
    )


def assert_blocked(result, reason_fragment):
    assert result.returncode == 2, result.stderr
    assert "ORDER BLOCKED" in result.stderr
    assert reason_fragment in result.stderr


# 1. dry-run true -> block
def test_dry_run_blocks(tmp_path):
    root = make_root(tmp_path, local={"account_number": FAKE_ACCOUNT, "dry_run": True})
    result = run_gate(root, valid_buy())
    assert_blocked(result, "dry_run=true")


# 2. halt flag in state -> block
def test_halt_flag_blocks(tmp_path):
    state = dict(BASE_STATE, halt=True, halt_reason="drawdown breached")
    result = run_gate(make_root(tmp_path, state=state), valid_buy())
    assert_blocked(result, "kill switch active: drawdown breached")


# 3. wrong account number -> block
def test_wrong_account_blocks(tmp_path):
    result = run_gate(
        make_root(tmp_path), valid_buy(account_number="TEST-OTHER-ACCOUNT")
    )
    assert_blocked(result, "not the agentic account")


# 4. missing / placeholder local account -> block
def test_missing_config_local_blocks(tmp_path):
    result = run_gate(make_root(tmp_path, local=None), valid_buy())
    assert_blocked(result, "config.local.json is missing")


def test_placeholder_account_blocks(tmp_path):
    root = make_root(tmp_path, local={"account_number": "REPLACE_ME"})
    result = run_gate(root, valid_buy())
    assert_blocked(result, "REPLACE_ME placeholder")


# 5. wrong symbol -> block
def test_wrong_symbol_blocks(tmp_path):
    result = run_gate(make_root(tmp_path), valid_buy(symbol="TSLA"))
    assert_blocked(result, "not whitelisted (only SPY)")


# 6. buy that is not market / missing dollar_amount -> block
@pytest.mark.parametrize(
    "order",
    [
        valid_buy(type="limit"),
        {k: v for k, v in valid_buy().items() if k != "dollar_amount"},
    ],
    ids=["limit-buy", "no-dollar-amount"],
)
def test_non_market_or_unsized_buy_blocks(tmp_path, order):
    result = run_gate(make_root(tmp_path), order)
    assert_blocked(result, "buys must be market orders sized with dollar_amount")


# 7. dollar_amount over max_order_usd -> block
def test_oversized_buy_blocks(tmp_path):
    result = run_gate(make_root(tmp_path), valid_buy(dollar_amount=500.01))
    assert_blocked(result, "exceeds max_order_usd")


# 8. sell without quantity -> block
def test_sell_without_quantity_blocks(tmp_path):
    order = valid_buy(side="sell")
    del order["dollar_amount"]
    result = run_gate(make_root(tmp_path), order)
    assert_blocked(result, "sells must specify quantity")


# 9. unknown side -> block
def test_unknown_side_blocks(tmp_path):
    result = run_gate(make_root(tmp_path), valid_buy(side="short"))
    assert_blocked(result, "unknown side 'short'")


# 10. outside regular market hours / weekend -> block
@pytest.mark.parametrize("now", [SATURDAY_NOW, AFTER_HOURS_NOW], ids=["weekend", "after-hours"])
def test_outside_market_hours_blocks(tmp_path, now):
    result = run_gate(make_root(tmp_path), valid_buy(), now=now)
    assert_blocked(result, "outside regular market hours")


# 11. second order same day -> block
def test_second_order_same_day_blocks(tmp_path):
    state = dict(BASE_STATE, last_action={"date": "2026-06-10", "order_placed": True})
    result = run_gate(make_root(tmp_path, state=state), valid_buy())
    assert_blocked(result, "already placed today")


# 12. missing or corrupt state file -> block (fail closed)
@pytest.mark.parametrize("state", [None, "{not json"], ids=["missing", "corrupt"])
def test_bad_state_fails_closed(tmp_path, state):
    result = run_gate(make_root(tmp_path, state=state), valid_buy())
    assert_blocked(result, "cannot load config/state")


# 13. missing or corrupt config -> block (fail closed)
@pytest.mark.parametrize("config", [None, '{"symbol": '], ids=["missing", "corrupt"])
def test_bad_config_fails_closed(tmp_path, config):
    result = run_gate(make_root(tmp_path, config=config), valid_buy())
    assert_blocked(result, "cannot load config/state")


def test_corrupt_config_local_fails_closed(tmp_path):
    result = run_gate(make_root(tmp_path, local="oops"), valid_buy())
    assert_blocked(result, "cannot load config/state")


# 14. non-order tool name -> allow
def test_non_order_tool_allowed(tmp_path):
    # No config/state files at all: the gate must not even need them.
    result = run_gate(tmp_path, {}, tool_name="mcp__robinhood__get_portfolio")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# 15. fully valid payload during market hours -> allow
def test_valid_buy_allowed(tmp_path):
    result = run_gate(make_root(tmp_path), valid_buy())
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_valid_sell_allowed(tmp_path):
    order = valid_buy(side="sell", quantity=3)
    del order["dollar_amount"]
    result = run_gate(make_root(tmp_path), order)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
