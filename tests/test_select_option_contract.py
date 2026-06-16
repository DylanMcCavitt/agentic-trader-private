"""Tests for scripts/select_option_contract.py: broker-chain contract picker.

Selection rule: nearest expiry in the DTE window, then the highest-premium
contract still <= the premium budget (most meaningful affordable contract,
not necessarily ITM). Fixtures mirror Robinhood option-instrument + market-data
field shapes (string prices, expiration_date, strike_price, type).
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "select_option_contract", ROOT / "scripts" / "select_option_contract.py"
)
soc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soc)

TODAY = date(2026, 6, 15)


def contract(strike, *, exp="2026-07-17", typ="call", ask=None, bid=None,
             mark=None, oid=None):
    d = {"id": oid or f"id-{typ}-{exp}-{strike}", "chain_symbol": "AAPL",
         "expiration_date": exp, "strike_price": f"{strike:.4f}", "type": typ}
    if ask is not None:
        d["ask_price"] = f"{ask:.2f}"
    if bid is not None:
        d["bid_price"] = f"{bid:.2f}"
    if mark is not None:
        d["adjusted_mark_price"] = f"{mark:.2f}"
    return d


def select(raw, **kw):
    params = dict(right="call", spot=207.5, dte_min=28, dte_max=45,
                  max_premium=300.0, contracts=1, today=TODAY)
    params.update(kw)
    return soc.select_contract(raw, **params)


def test_picks_highest_premium_under_budget():
    # 32 DTE expiry; strike 200 -> $250, strike 195 -> $400 (over), strike 210 -> $100
    chain = [contract(195, ask=4.00), contract(200, ask=2.50),
             contract(210, ask=1.00)]
    out = select(chain)
    assert out["within_budget"] is True
    assert out["strike"] == 200.0          # highest premium still <= $300
    assert out["premium"] == 250.0
    assert out["limit_price"] == 2.50
    assert out["option_id"] == "id-call-2026-07-17-200"


def test_nearest_expiry_wins():
    near = contract(200, exp="2026-07-17", ask=2.50)   # 32 DTE
    far = contract(205, exp="2026-07-24", ask=1.50)    # 39 DTE, cheaper but later
    out = select([far, near])
    assert out["expiry"] == "2026-07-17"
    assert out["strike"] == 200.0


def test_no_contracts_in_dte_window():
    chain = [contract(200, exp="2026-06-20", ask=2.50),   # 5 DTE
             contract(200, exp="2026-09-01", ask=2.50)]   # 78 DTE
    out = select(chain)
    assert out["within_budget"] is False
    assert "DTE" in out["reason"]


def test_all_over_budget_reports_cheapest():
    chain = [contract(190, ask=9.00), contract(195, ask=6.50)]
    out = select(chain)
    assert out["within_budget"] is False
    assert out["cheapest_premium"] == 650.0
    assert out["cheapest_strike"] == 195.0


def test_falls_back_to_mark_when_ask_missing():
    chain = [contract(200, ask=None, mark=2.20, bid=2.00)]
    out = select(chain)
    assert out["within_budget"] is True
    assert out["limit_price"] == 2.20      # mark used (ask absent)


def test_right_filter_ignores_puts():
    chain = [contract(200, typ="put", ask=1.00), contract(205, typ="call", ask=2.00)]
    out = select(chain, right="call")
    assert out["within_budget"] is True
    assert out["strike"] == 205.0


def test_put_selection():
    chain = [contract(210, typ="put", ask=2.00), contract(215, typ="put", ask=2.80),
             contract(220, typ="put", ask=5.00)]
    out = select(chain, right="put")
    assert out["within_budget"] is True
    assert out["strike"] == 215.0          # highest premium <= $300 ($280)
    assert out["premium"] == 280.0


def test_handles_wrapped_results_payload():
    chain = {"results": [contract(200, ask=2.50)]}
    out = select(chain)
    assert out["within_budget"] is True
    assert out["strike"] == 200.0


def test_main_reads_chains_json(monkeypatch, capsys):
    chain = json.dumps([contract(200, ask=2.50)])
    monkeypatch.setattr(sys, "argv",
                        ["select_option_contract.py", "--right", "call",
                         "--spot", "207.5", "--dte-min", "28", "--dte-max", "45",
                         "--max-premium", "300", "--contracts", "1",
                         "--today", "2026-06-15", "--chains-json", chain])
    soc.main()
    out = json.loads(capsys.readouterr().out)
    assert out["within_budget"] is True and out["strike"] == 200.0
