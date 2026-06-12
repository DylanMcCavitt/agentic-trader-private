"""Tests for scripts/option_gate.py — every block path and the allow paths.

Same harness as tests/test_order_gate.py: the gate runs as a subprocess with
ORDER_GATE_ROOT / ORDER_GATE_NOW seams. No real account values anywhere.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "option_gate.py"
ORDER_TOOL = "mcp__robinhood__place_option_order"
FAKE_ACCOUNT = "TEST-FAKE-ACCOUNT-000"

# Wednesday 2026-06-10, 10:30 ET — regular market hours.
MARKET_OPEN_NOW = "2026-06-10T10:30:00"
SATURDAY_NOW = "2026-06-13T10:30:00"
AFTER_HOURS_NOW = "2026-06-10T17:30:00"

BASE_CONFIG = {
    "symbol": "SPY",
    "account_number": "REPLACE_ME",
    "dry_run": False,
    "max_order_usd": 500,
    "max_option_premium_usd": 1500,
    "max_option_contracts": 2,
}
BASE_STATE = {
    "hwm": 0,
    "halt": False,
    "halt_reason": None,
    "last_run": None,
    "last_action": None,
    "last_option_action": None,
    "position_opened": None,
}
_OMIT = object()


def make_root(tmp_path, *, config=_OMIT, local=_OMIT, state=_OMIT):
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


def valid_open(**overrides):
    order = {
        "account_number": FAKE_ACCOUNT,
        "quantity": "1",
        "type": "limit",
        "price": "5.00",
        "legs": [{"option_id": "00000000-0000-0000-0000-000000000000",
                  "side": "buy", "position_effect": "open"}],
    }
    order.update(overrides)
    return order


def valid_close(**overrides):
    order = valid_open(legs=[{"option_id": "00000000-0000-0000-0000-000000000000",
                              "side": "sell", "position_effect": "close"}])
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
    assert_blocked(run_gate(root, valid_open()), "dry_run=true")


# 2. halt flag -> block
def test_halt_flag_blocks(tmp_path):
    state = dict(BASE_STATE, halt=True, halt_reason="drawdown breached")
    result = run_gate(make_root(tmp_path, state=state), valid_open())
    assert_blocked(result, "kill switch active: drawdown breached")


# 3. account checks -> block
def test_wrong_account_blocks(tmp_path):
    result = run_gate(make_root(tmp_path),
                      valid_open(account_number="TEST-OTHER-ACCOUNT"))
    assert_blocked(result, "not the agentic account")


def test_missing_config_local_blocks(tmp_path):
    result = run_gate(make_root(tmp_path, local=None), valid_open())
    assert_blocked(result, "config.local.json is missing")


def test_placeholder_account_blocks(tmp_path):
    root = make_root(tmp_path, local={"account_number": "REPLACE_ME"})
    assert_blocked(run_gate(root, valid_open()), "REPLACE_ME placeholder")


# 4. options not configured -> block (fail closed)
def test_missing_option_caps_block(tmp_path):
    config = {k: v for k, v in BASE_CONFIG.items()
              if not k.startswith("max_option")}
    result = run_gate(make_root(tmp_path, config=config), valid_open())
    assert_blocked(result, "options trading is not enabled")


# 5. leg structure -> block
@pytest.mark.parametrize("legs", [
    [],
    None,
    [{"option_id": "x", "side": "buy", "position_effect": "open"}] * 2,
], ids=["empty", "null", "two-legs"])
def test_leg_count_blocks(tmp_path, legs):
    result = run_gate(make_root(tmp_path), valid_open(legs=legs))
    assert_blocked(result, "exactly one leg required")


# 6. long-only: any short-premium or mismatched leg -> block
@pytest.mark.parametrize("side,effect", [
    ("sell", "open"),   # short premium — the critical block
    ("buy", "close"),   # closing a short — implies a short existed
    ("short", "open"),
], ids=["sell-to-open", "buy-to-close", "unknown-side"])
def test_disallowed_leg_blocks(tmp_path, side, effect):
    order = valid_open(legs=[{"option_id": "x", "side": side,
                              "position_effect": effect}])
    result = run_gate(make_root(tmp_path), order)
    assert_blocked(result, "only buy+open or sell+close")


def test_ratio_quantity_above_one_blocks(tmp_path):
    order = valid_open(legs=[{"option_id": "x", "side": "buy",
                              "position_effect": "open", "ratio_quantity": 2}])
    assert_blocked(run_gate(make_root(tmp_path), order), "must be 1")


# 7. quantity checks -> block
@pytest.mark.parametrize("qty,fragment", [
    ("0", "must be >= 1"),
    ("3", "exceeds max_option_contracts"),
    ("x", "not an integer"),
    (None, "not an integer"),
], ids=["zero", "over-cap", "non-numeric", "missing"])
def test_bad_quantity_blocks(tmp_path, qty, fragment):
    assert_blocked(run_gate(make_root(tmp_path), valid_open(quantity=qty)), fragment)


# 8. opens must be limit with a price -> block
def test_market_open_blocks(tmp_path):
    order = valid_open(type="market")
    del order["price"]
    result = run_gate(make_root(tmp_path), order)
    assert_blocked(result, "opens must be limit orders with a price")


def test_open_without_price_blocks(tmp_path):
    order = valid_open()
    del order["price"]
    result = run_gate(make_root(tmp_path), order)
    assert_blocked(result, "opens must be limit orders with a price")


# 9. premium cap -> block (price x qty x 100)
def test_premium_over_cap_blocks(tmp_path):
    # 8.00 x 2 x 100 = 1600 > 1500
    result = run_gate(make_root(tmp_path), valid_open(price="8.00", quantity="2"))
    assert_blocked(result, "exceeds max_option_premium_usd")


# 10. market hours -> block
@pytest.mark.parametrize("now", [SATURDAY_NOW, AFTER_HOURS_NOW],
                         ids=["weekend", "after-hours"])
def test_outside_market_hours_blocks(tmp_path, now):
    result = run_gate(make_root(tmp_path), valid_open(), now=now)
    assert_blocked(result, "outside regular market hours")


# 11. second option order same day -> block
def test_second_option_order_same_day_blocks(tmp_path):
    state = dict(BASE_STATE,
                 last_option_action={"date": "2026-06-10", "order_placed": True})
    result = run_gate(make_root(tmp_path, state=state), valid_open())
    assert_blocked(result, "option order was already placed today")


# equity orders that day do NOT consume the option budget
def test_equity_order_today_does_not_block_option(tmp_path):
    state = dict(BASE_STATE,
                 last_action={"date": "2026-06-10", "order_placed": True})
    result = run_gate(make_root(tmp_path, state=state), valid_open())
    assert result.returncode == 0, result.stderr


# 12. missing/corrupt config or state -> block (fail closed)
@pytest.mark.parametrize("kw", [{"state": None}, {"state": "{not json"},
                                {"config": None}, {"config": '{"symbol": '}],
                         ids=["no-state", "bad-state", "no-config", "bad-config"])
def test_bad_files_fail_closed(tmp_path, kw):
    result = run_gate(make_root(tmp_path, **kw), valid_open())
    assert_blocked(result, "cannot load config/state")


# 13. other tools -> allow without touching files
def test_non_option_tool_allowed(tmp_path):
    result = run_gate(tmp_path, {}, tool_name="mcp__robinhood__get_option_quotes")
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_equity_order_tool_ignored_by_option_gate(tmp_path):
    result = run_gate(tmp_path, {}, tool_name="mcp__robinhood__place_equity_order")
    assert result.returncode == 0, result.stderr


# 14. valid orders -> allow
def test_valid_limit_open_allowed(tmp_path):
    result = run_gate(make_root(tmp_path), valid_open())
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_valid_sell_to_close_allowed(tmp_path):
    result = run_gate(make_root(tmp_path), valid_close())
    assert result.returncode == 0, result.stderr


def test_valid_market_close_allowed(tmp_path):
    # Closes may be market orders (getting out beats a perfect price).
    order = valid_close(type="market")
    del order["price"]
    result = run_gate(make_root(tmp_path), order)
    assert result.returncode == 0, result.stderr
