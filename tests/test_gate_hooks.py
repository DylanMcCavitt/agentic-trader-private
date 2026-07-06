"""Hook-protocol behavior: stdin/stdout JSON contract and fail-closed paths."""

import io
import json

import pytest

from trader.gates import common
from trader.gates.common import Verdict


def run_main(monkeypatch, capsys, evaluate, stdin_text: str):
    monkeypatch.setattr(
        "trader.db.session.get_session", lambda url=None: FakeSession()
    )
    rc = common.run_gate_main(evaluate, stdin=io.StringIO(stdin_text))
    out = capsys.readouterr().out
    return rc, json.loads(out)


class FakeSession:
    def close(self):
        pass


def decision_of(payload):
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] in {"allow", "deny"}
    return hso["permissionDecision"], hso["permissionDecisionReason"]


def test_malformed_json_fails_closed(monkeypatch, capsys):
    rc, out = run_main(monkeypatch, capsys, None, "this is not json{")
    assert rc == 0
    decision, reason = decision_of(out)
    assert decision == "deny"
    assert "malformed" in reason


def test_missing_tool_input_fails_closed(monkeypatch, capsys):
    rc, out = run_main(monkeypatch, capsys, None, json.dumps({"tool_name": "x"}))
    assert rc == 0
    decision, reason = decision_of(out)
    assert decision == "deny"
    assert "tool_input" in reason


def test_evaluate_exception_fails_closed(monkeypatch, capsys):
    def boom(session, tool_input):
        raise RuntimeError("db exploded")

    rc, out = run_main(
        monkeypatch, capsys, boom,
        json.dumps({"tool_name": "x", "tool_input": {"symbol": "NVDA"}}),
    )
    assert rc == 0
    decision, reason = decision_of(out)
    assert decision == "deny"
    assert "fail closed" in reason


def test_allow_verdict_passes_through(monkeypatch, capsys):
    def ok(session, tool_input):
        return Verdict("allow", "approved for test")

    rc, out = run_main(
        monkeypatch, capsys, ok,
        json.dumps({"tool_name": "x", "tool_input": {"symbol": "NVDA"}}),
    )
    assert rc == 0
    decision, reason = decision_of(out)
    assert decision == "allow"
    assert reason == "approved for test"


def test_gate_modules_are_runnable_entrypoints():
    """The M3 settings.json wires `python -m trader.gates.equity_gate` /
    `option_gate`; both modules must expose main()."""
    from trader.gates import equity_gate, option_gate

    assert callable(equity_gate.main)
    assert callable(option_gate.main)


# --- order parsing tolerance -------------------------------------------------


def test_parse_order_key_aliases():
    order = common.parse_order(
        {"client_order_id": "x1", "ticker": "nvda", "action": "BUY", "qty": "5", "price": 101.5},
        "equity",
    )
    assert order.ref_id == "x1"
    assert order.symbol == "NVDA"
    assert order.side == "buy"
    assert float(order.qty) == 5
    assert float(order.limit_price) == 101.5


def test_parse_order_compound_sides():
    bto = common.parse_order({"side": "buy_to_open"}, "option")
    assert (bto.side, bto.position_effect) == ("buy", "open")
    stc = common.parse_order({"side": "sell_to_close"}, "option")
    assert (stc.side, stc.position_effect) == ("sell", "close")
    sto = common.parse_order({"side": "sell_to_open"}, "option")
    assert (sto.side, sto.position_effect) == ("sell", "open")


def test_parse_order_option_leg_payload():
    order = common.parse_order(
        {
            "ref_id": "o1",
            "symbol": "NVDA",
            "quantity": 1,
            "legs": [
                {
                    "side": "buy",
                    "position_effect": "open",
                    "option_type": "call",
                    "strike_price": 200,
                    "expiration_date": "2026-08-07",
                }
            ],
        },
        "option",
    )
    assert order.leg_count == 1
    assert order.side == "buy"
    assert order.position_effect == "open"
    assert order.occ_symbol == "NVDA260807C00200000"


def test_parse_order_garbage_values_do_not_crash():
    order = common.parse_order(
        {"ref_id": "  ", "symbol": None, "quantity": "lots", "limit_price": "NaN",
         "side": "yolo", "expiration_date": "soon", "strike_price": "high"},
        "option",
    )
    assert order.ref_id is None
    assert order.symbol is None
    assert order.qty is None
    assert order.limit_price is None
    assert order.side is None
    assert order.expiration is None
    assert order.strike is None


def test_occ_symbol_formatting():
    order = common.parse_order(
        {"symbol": "F", "option_type": "put", "strike_price": 12.5,
         "expiration_date": "2026-12-18"},
        "option",
    )
    assert order.occ_symbol == "F261218P00012500"
