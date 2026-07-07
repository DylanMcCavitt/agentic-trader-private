"""Runtime configuration shared by gates, kill-switch, and sleeve CLI.

- ``config.local.json`` (gitignored) holds local, non-secret-ish identifiers
  such as ``account_number`` and an optional ``account_name``. Never commit it.
- ``dry_run`` is a DB param-style flag stored in ``param_history`` under the
  name ``dry_run`` (0/1). It defaults to **true** (dry run) until M5 flips it
  with an explicit row — fail toward not trading.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.local.json"

DRY_RUN_PARAM = "dry_run"
DEFAULT_ACCOUNT_NAME = "personal"
QUOTE_FRESHNESS_MINUTES = 10

# Liquidity floors for the equity gate.
MIN_AVG_DOLLAR_VOLUME = 50_000_000  # $50M average daily dollar volume
MIN_PRICE = 5.0

# Liquidity floors for the option gate.
MIN_OPEN_INTEREST = 100
MAX_RELATIVE_SPREAD = 0.10  # (ask - bid) / mid


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Read config.local.json; missing or malformed file yields {}."""
    p = path or Path(os.environ.get("TRADER_CONFIG", CONFIG_PATH))
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def account_name(config: dict[str, Any] | None = None) -> str:
    cfg = config if config is not None else load_config()
    return str(cfg.get("account_name") or DEFAULT_ACCOUNT_NAME)


def get_account(session, config: dict[str, Any] | None = None):
    """Fetch the configured Account row, or None if absent."""
    from sqlalchemy import select

    from trader.db.models import Account

    return session.execute(
        select(Account).where(Account.name == account_name(config))
    ).scalar_one_or_none()


def dry_run_enabled(session) -> bool:
    """Latest ``dry_run`` param_history row wins; no row means dry run ON."""
    from sqlalchemy import select

    from trader.db.models import ParamHistory

    row = session.execute(
        select(ParamHistory)
        .where(ParamHistory.param_name == DRY_RUN_PARAM)
        .order_by(ParamHistory.created_at.desc(), ParamHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return True
    return str(row.new_value).strip().lower() not in {"0", "false", "off", "no"}
