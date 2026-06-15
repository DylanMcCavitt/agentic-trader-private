"""Tests for scripts/reconcile_state.py."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "reconcile_state", ROOT / "scripts" / "reconcile_state.py"
)
reconcile_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile_state)

TODAY = "2026-06-10"
LAST_RUN = "2026-06-10T15:45:00-04:00"
BASE_STATE = {
    "hwm": 1000,
    "halt": False,
    "halt_reason": None,
    "last_run": None,
    "last_action": None,
    "last_option_action": None,
    "position_opened": None,
    "custom": {"preserve": True},
}


def make_root(tmp_path, *, state=None):
    (tmp_path / "state").mkdir()
    (tmp_path / "config.json").write_text(json.dumps({"symbol": "SPY"}))
    (tmp_path / "state" / "state.json").write_text(
        json.dumps(BASE_STATE if state is None else state)
    )
    return tmp_path


def read_state(root):
    return json.loads((root / "state" / "state.json").read_text())


def write_marker(root, kind, marker):
    name = "gate_equity.json" if kind == "equity" else "gate_option.json"
    (root / "state" / name).write_text(json.dumps(marker))


def test_cli_reconciles_orders_json_and_writes_state(tmp_path):
    root = make_root(tmp_path)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "eq-ref", "symbol": "SPY", "side": "buy"},
    )
    orders = [{
        "id": "eq-order-cli",
        "ref_id": "eq-ref",
        "symbol": "SPY",
        "side": "buy",
        "state": "filled",
        "quantity": "1",
        "cumulative_quantity": "1",
        "created_at": f"{TODAY}T14:31:00Z",
    }]
    env = {**os.environ, "RECONCILE_STATE_ROOT": str(root),
           "RECONCILE_STATE_NOW": LAST_RUN}

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "reconcile_state.py"),
            "--kind", "equity",
            "--date", TODAY,
            "--decision", "BUY",
            "--orders-json", json.dumps(orders),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    output = json.loads(result.stdout)
    saved = read_state(root)
    assert output["order_placed"] is True
    assert output["order_id"] == "eq-order-cli"
    assert saved["last_action"] == output
    assert saved["last_run"] == LAST_RUN


def test_equity_fill_reconciles_order_placed_true_and_position_opened(tmp_path):
    root = make_root(tmp_path)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "eq-ref", "symbol": "SPY", "side": "buy"},
    )
    orders = {
        "orders": [
            {
                "id": "eq-order-1",
                "client_order_id": "eq-ref",
                "symbol": "SPY",
                "side": "buy",
                "state": "filled",
                "quantity": "1.5",
                "cumulative_quantity": "1.5",
                "average_price": "420.00",
                "created_at": f"{TODAY}T14:31:00Z",
            }
        ]
    }

    record = reconcile_state.reconcile_state(
        kind="equity",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        decision="BUY",
        last_run=LAST_RUN,
    )

    saved = read_state(root)
    assert record["order_placed"] is True
    assert record["order_id"] == "eq-order-1"
    assert record["status"] == "filled"
    assert record["fill_state"] == "filled"
    assert record["filled"] is True
    assert record["decision"] == "BUY"
    assert saved["last_action"] == record
    assert saved["position_opened"] == TODAY
    assert saved["last_run"] == LAST_RUN
    assert saved["hwm"] == 1000
    assert saved["halt"] is False
    assert saved["halt_reason"] is None
    assert saved["custom"] == {"preserve": True}


def test_ref_id_matching_is_case_and_whitespace_insensitive(tmp_path):
    root = make_root(tmp_path)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "  AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE  ",
         "symbol": "SPY", "side": "buy"},
    )
    orders = [
        {
            "id": "eq-order-uuid",
            "ref_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "symbol": "SPY",
            "side": "buy",
            "state": "filled",
            "quantity": "1",
            "cumulative_quantity": "1",
            "created_at": f"{TODAY}T14:31:00Z",
        }
    ]

    record = reconcile_state.reconcile_state(
        kind="equity",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        decision="BUY",
        last_run=LAST_RUN,
    )

    assert record["order_placed"] is True
    assert record["order_id"] == "eq-order-uuid"
    assert record["fill_state"] == "filled"


def test_equity_filled_sell_clears_position_opened(tmp_path):
    state = dict(BASE_STATE, position_opened="2026-06-01")
    root = make_root(tmp_path, state=state)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "sell-ref", "symbol": "SPY", "side": "sell"},
    )
    orders = [
        {
            "id": "eq-sell-1",
            "ref_id": "sell-ref",
            "symbol": "SPY",
            "side": "sell",
            "state": "filled",
            "quantity": "1.5",
            "cumulative_quantity": "1.5",
            "created_at": f"{TODAY}T14:35:00Z",
        }
    ]

    reconcile_state.reconcile_state(
        kind="equity",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        decision="SELL",
        last_run=LAST_RUN,
    )

    assert read_state(root)["position_opened"] is None


def test_equity_believed_but_absent_reconciles_order_placed_false(tmp_path):
    state = dict(
        BASE_STATE,
        last_action={"date": TODAY, "decision": "BUY", "order_placed": True,
                     "order_id": "believed"},
        position_opened="2026-06-01",
    )
    root = make_root(tmp_path, state=state)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "eq-ref", "symbol": "SPY", "side": "buy"},
    )

    record = reconcile_state.reconcile_state(
        kind="equity",
        orders_raw={"orders": []},
        root=root,
        target_date=TODAY,
        decision="BUY",
        last_run=LAST_RUN,
    )

    saved = read_state(root)
    assert record == {
        "date": TODAY,
        "decision": "BUY",
        "order_placed": False,
        "order_id": None,
        "status": "not_found",
        "fill_state": None,
        "filled": False,
        "ref_id": "eq-ref",
        "side": "buy",
        "symbol": "SPY",
    }
    assert saved["last_action"] == record
    assert saved["position_opened"] == "2026-06-01"


@pytest.mark.parametrize(
    "marker",
    [
        None,
        {"date": TODAY, "symbol": "SPY", "side": "buy"},
        {"date": "2026-06-09", "ref_id": "eq-ref", "symbol": "SPY", "side": "buy"},
    ],
    ids=["no-marker", "marker-without-ref", "stale-marker"],
)
def test_equity_requires_same_day_marker_with_ref_id_before_matching(
    tmp_path, marker
):
    root = make_root(tmp_path)
    if marker is not None:
        write_marker(root, "equity", marker)
    orders = [
        {
            "id": "eq-order-1",
            "ref_id": "eq-ref",
            "symbol": "SPY",
            "side": "buy",
            "state": "filled",
            "quantity": "1",
            "cumulative_quantity": "1",
            "created_at": f"{TODAY}T14:31:00Z",
        }
    ]

    record = reconcile_state.reconcile_state(
        kind="equity",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        decision="BUY",
        last_run=LAST_RUN,
    )

    assert record["order_placed"] is False
    assert record["order_id"] is None
    assert record["status"] == "not_found"
    assert record["fill_state"] is None
    assert read_state(root)["position_opened"] is None


@pytest.mark.parametrize(
    "order",
    [
        {
            "id": "eq-order-wrong-ref",
            "ref_id": "other-ref",
            "created_at": f"{TODAY}T14:31:00Z",
        },
        {
            "id": "eq-order-missing-ref",
            "created_at": f"{TODAY}T14:31:00Z",
        },
        {
            "id": "eq-order-stale",
            "ref_id": "eq-ref",
            "created_at": "2026-06-09T14:31:00Z",
        },
    ],
    ids=["wrong-ref", "missing-broker-ref", "stale-broker-order"],
)
def test_equity_unrelated_missing_ref_or_stale_broker_orders_reconcile_false(
    tmp_path, order
):
    root = make_root(tmp_path)
    write_marker(
        root,
        "equity",
        {"date": TODAY, "ref_id": "eq-ref", "symbol": "SPY", "side": "buy"},
    )
    broker_order = {
        "symbol": "SPY",
        "side": "buy",
        "state": "filled",
        "quantity": "1",
        "cumulative_quantity": "1",
        **order,
    }

    record = reconcile_state.reconcile_state(
        kind="equity",
        orders_raw=[broker_order],
        root=root,
        target_date=TODAY,
        decision="BUY",
        last_run=LAST_RUN,
    )

    assert record["order_placed"] is False
    assert record["order_id"] is None
    assert record["status"] == "not_found"
    assert record["fill_state"] is None
    assert read_state(root)["position_opened"] is None


def test_option_fill_reconciles_last_option_action_true(tmp_path):
    root = make_root(tmp_path)
    write_marker(
        root,
        "option",
        {
            "date": TODAY,
            "ref_id": "opt-ref",
            "side": "buy",
            "position_effect": "open",
            "option_id": "opt-1",
            "legs": [{"option_id": "opt-1", "side": "buy", "position_effect": "open"}],
        },
    )
    orders = [
        {
            "id": "opt-order-1",
            "ref_id": "opt-ref",
            "state": "filled",
            "quantity": "1",
            "processed_quantity": "1",
            "created_at": f"{TODAY}T14:32:00Z",
            "legs": [{"option_id": "opt-1", "side": "buy", "position_effect": "open"}],
        }
    ]

    record = reconcile_state.reconcile_state(
        kind="option",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        action="BUY_OPEN",
        last_run=LAST_RUN,
    )

    saved = read_state(root)
    assert record["order_placed"] is True
    assert record["order_id"] == "opt-order-1"
    assert record["status"] == "filled"
    assert record["fill_state"] == "filled"
    assert record["filled"] is True
    assert record["action"] == "BUY_OPEN"
    assert record["side"] == "buy"
    assert record["position_effect"] == "open"
    assert saved["last_option_action"] == record
    assert saved["position_opened"] is None
    assert saved["custom"] == {"preserve": True}


def test_option_believed_but_absent_reconciles_order_placed_false(tmp_path):
    state = dict(
        BASE_STATE,
        last_option_action={"date": TODAY, "action": "BUY_OPEN",
                            "order_placed": True, "order_id": "believed"},
    )
    root = make_root(tmp_path, state=state)
    write_marker(root, "option", {"date": TODAY, "ref_id": "opt-ref"})

    record = reconcile_state.reconcile_state(
        kind="option",
        orders_raw={"results": []},
        root=root,
        target_date=TODAY,
        action="BUY_OPEN",
        last_run=LAST_RUN,
    )

    assert record == {
        "date": TODAY,
        "action": "BUY_OPEN",
        "order_placed": False,
        "order_id": None,
        "status": "not_found",
        "fill_state": None,
        "filled": False,
        "ref_id": "opt-ref",
    }
    assert read_state(root)["last_option_action"] == record


def test_option_missing_broker_ref_does_not_match_by_leg_metadata(tmp_path):
    root = make_root(tmp_path)
    write_marker(
        root,
        "option",
        {
            "date": TODAY,
            "ref_id": "opt-ref",
            "side": "buy",
            "position_effect": "open",
            "option_id": "opt-1",
            "legs": [{"option_id": "opt-1", "side": "buy", "position_effect": "open"}],
        },
    )
    orders = [
        {
            "id": "opt-order-missing-ref",
            "state": "filled",
            "quantity": "1",
            "processed_quantity": "1",
            "created_at": f"{TODAY}T14:32:00Z",
            "legs": [{"option_id": "opt-1", "side": "buy", "position_effect": "open"}],
        }
    ]

    record = reconcile_state.reconcile_state(
        kind="option",
        orders_raw=orders,
        root=root,
        target_date=TODAY,
        action="BUY_OPEN",
        last_run=LAST_RUN,
    )

    assert record["order_placed"] is False
    assert record["order_id"] is None
    assert record["status"] == "not_found"
    assert record["fill_state"] is None
    assert read_state(root)["last_option_action"] == record
