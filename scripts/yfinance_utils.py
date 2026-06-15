"""Shared yfinance download settings."""

from __future__ import annotations

import math
import os

DEFAULT_YFINANCE_DOWNLOAD_TIMEOUT_SECONDS = 10.0
YFINANCE_DOWNLOAD_TIMEOUT_ENV = "AGENTIC_TRADER_YFINANCE_TIMEOUT_SECONDS"


def yfinance_download_timeout() -> float:
    """Return the explicit timeout passed to yf.download()."""
    raw = os.environ.get(YFINANCE_DOWNLOAD_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_YFINANCE_DOWNLOAD_TIMEOUT_SECONDS
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{YFINANCE_DOWNLOAD_TIMEOUT_ENV} must be a finite positive number"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(f"{YFINANCE_DOWNLOAD_TIMEOUT_ENV} must be finite and positive")
    return timeout
