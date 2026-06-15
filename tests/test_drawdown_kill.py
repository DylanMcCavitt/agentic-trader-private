"""Tests for scripts/drawdown_kill.py."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "drawdown_kill", ROOT / "scripts" / "drawdown_kill.py"
)
drawdown_kill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drawdown_kill)

BASE_CONFIG = {"kill_drawdown_pct": 15}
BASE_STATE = {
    "hwm": 100,
    "halt": False,
    "halt_reason": None,
    "last_run": None,
    "last_action": None,
    "position_opened": None,
}


def make_root(tmp_path, *, config=None, state=None):
    (tmp_path / "state").mkdir()
    if config is None:
        config = BASE_CONFIG
    if state is None:
        state = BASE_STATE
    (tmp_path / "config.json").write_text(json.dumps(config))
    (tmp_path / "state" / "state.json").write_text(json.dumps(state))
    return tmp_path


def read_state(root):
    return json.loads((root / "state" / "state.json").read_text())


def test_cli_takes_total_value_and_outputs_json(tmp_path):
    root = make_root(tmp_path)
    env = {**os.environ, "DRAWDOWN_KILL_ROOT": str(root)}

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "drawdown_kill.py"),
            "--total-value",
            "125",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    output = json.loads(result.stdout)
    assert output["hwm"] == 125
    assert output["halt"] is False
    assert read_state(root)["hwm"] == 125


def test_new_high_updates_hwm_upward_and_preserves_fields(tmp_path):
    state = dict(BASE_STATE, extra={"keep": True})
    root = make_root(tmp_path, state=state)

    result = drawdown_kill.evaluate_and_update("125.50", root)

    saved = read_state(root)
    assert saved["hwm"] == 125.5
    assert saved["halt"] is False
    assert saved["halt_reason"] is None
    assert saved["extra"] == {"keep": True}
    assert result["hwm"] == 125.5
    assert result["halt"] is False
    assert result["breached"] is False


def test_breach_sets_halt_and_reason_without_lowering_hwm(tmp_path):
    root = make_root(tmp_path)

    result = drawdown_kill.evaluate_and_update("84.99", root)

    saved = read_state(root)
    assert saved["hwm"] == 100
    assert saved["halt"] is True
    assert "84.99" in saved["halt_reason"]
    assert "15%" in saved["halt_reason"]
    assert "hwm 100" in saved["halt_reason"]
    assert result["halt"] is True
    assert result["breached"] is True


def test_exact_threshold_does_not_halt_or_lower_hwm(tmp_path):
    root = make_root(tmp_path)

    result = drawdown_kill.evaluate_and_update("85.0", root)

    saved = read_state(root)
    assert saved["hwm"] == 100
    assert saved["halt"] is False
    assert saved["halt_reason"] is None
    assert result["halt"] is False
    assert result["breached"] is False


def test_near_non_breach_does_not_halt_or_lower_hwm(tmp_path):
    root = make_root(tmp_path)

    result = drawdown_kill.evaluate_and_update("85.0001", root)

    saved = read_state(root)
    assert saved["hwm"] == 100
    assert saved["halt"] is False
    assert saved["halt_reason"] is None
    assert result["halt"] is False
    assert result["breached"] is False


def test_idempotent_breach_reruns_leave_state_unchanged(tmp_path):
    root = make_root(tmp_path)

    first = drawdown_kill.evaluate_and_update("84.99", root)
    after_first = read_state(root)
    second = drawdown_kill.evaluate_and_update("84.99", root)

    assert read_state(root) == after_first
    assert second == first


def test_existing_halt_reason_is_preserved(tmp_path):
    state = dict(BASE_STATE, halt=True, halt_reason="manual halt")
    root = make_root(tmp_path, state=state)

    result = drawdown_kill.evaluate_and_update("80", root)

    saved = read_state(root)
    assert saved["halt"] is True
    assert saved["halt_reason"] == "manual halt"
    assert saved["hwm"] == 100
    assert result["halt_reason"] == "manual halt"


def test_existing_halt_without_reason_gets_drawdown_reason_on_breach(tmp_path):
    state = dict(BASE_STATE, halt=True, halt_reason=None)
    root = make_root(tmp_path, state=state)

    result = drawdown_kill.evaluate_and_update("80", root)

    saved = read_state(root)
    assert saved["halt"] is True
    assert "drawdown kill switch" in saved["halt_reason"]
    assert result["halt_reason"] == saved["halt_reason"]
