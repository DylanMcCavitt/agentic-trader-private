#!/usr/bin/env python3
"""Deterministic portfolio drawdown kill switch.

Given a Robinhood portfolio total_value, update state/state.json's high-water
mark upward only and trip the halt flag if configured drawdown is breached.
"""
import argparse
import json
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

# Test seam: DRAWDOWN_KILL_ROOT overrides the repo root (unset in production).
ROOT = Path(os.environ.get("DRAWDOWN_KILL_ROOT") or Path(__file__).parent.parent)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: Path = ROOT) -> dict[str, Any]:
    """Tracked config.json deep-merged with untracked config.local.json."""
    cfg = json.loads((root / "config.json").read_text())
    local = root / "config.local.json"
    if local.exists():
        cfg = deep_merge(cfg, json.loads(local.read_text()))
    return cfg


def parse_decimal(value: Any, name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} {value!r} is not numeric") from exc
    if not parsed.is_finite():
        raise ValueError(f"{name} {value!r} is not finite")
    return parsed


def json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def format_decimal(value: Decimal) -> str:
    """Stable human-readable decimal without scientific notation."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w") as tmp:
            json.dump(data, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_DIRECTORY)
        except (AttributeError, OSError):
            return
        try:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def evaluate_and_update(total_value_arg: Any, root: Path = ROOT) -> dict[str, Any]:
    total_value = parse_decimal(total_value_arg, "total_value")
    cfg = load_config(root)
    if "kill_drawdown_pct" not in cfg:
        raise KeyError("config is missing required key: kill_drawdown_pct")
    kill_drawdown_pct = parse_decimal(cfg["kill_drawdown_pct"], "kill_drawdown_pct")
    if kill_drawdown_pct < 0 or kill_drawdown_pct > 100:
        raise ValueError("kill_drawdown_pct must be between 0 and 100")

    state_path = root / "state" / "state.json"
    state = json.loads(state_path.read_text())
    previous_hwm = parse_decimal(state.get("hwm", 0), "state hwm")
    hwm = previous_hwm

    if total_value > hwm:
        hwm = total_value
        state["hwm"] = json_number(hwm)
    elif "hwm" not in state:
        state["hwm"] = json_number(hwm)

    threshold = hwm * (Decimal("1") - kill_drawdown_pct / Decimal("100"))
    breached = total_value < threshold
    already_halted = bool(state.get("halt"))

    if breached:
        reason = (
            "drawdown kill switch: "
            f"total_value {format_decimal(total_value)} breached "
            f"{format_decimal(kill_drawdown_pct)}% drawdown from "
            f"hwm {format_decimal(hwm)}"
        )
        if not already_halted or not state.get("halt_reason"):
            state["halt"] = True
            state["halt_reason"] = reason

    atomic_write_json(state_path, state)

    return {
        "total_value": json_number(total_value),
        "hwm": json_number(hwm),
        "halt": bool(state.get("halt")),
        "halt_reason": state.get("halt_reason"),
        "breached": breached,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--total-value",
        required=True,
        help="Portfolio total_value returned by get_portfolio.",
    )
    args = parser.parse_args()

    try:
        result = evaluate_and_update(args.total_value)
    except Exception as exc:
        print(f"drawdown kill switch error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
