"""Screener math on synthetic dataframes — no network, no yfinance calls."""

import numpy as np
import pandas as pd
import pytest

from trader.screener import screens
from trader.screener.run import check_symbol, run_screener


def make_bars(
    *,
    days: int = 30,
    close: float = 100.0,
    last_close: float | None = None,
    last_open: float | None = None,
    volume: float = 1_000_000,
    last_volume: float | None = None,
    close_5d_ago: float | None = None,
) -> pd.DataFrame:
    """Flat synthetic daily bars with optional overrides on the last day."""
    closes = np.full(days, close)
    if close_5d_ago is not None:
        closes[-6] = close_5d_ago
    if last_close is not None:
        closes[-1] = last_close
    opens = closes.copy()
    if last_open is not None:
        opens[-1] = last_open
    volumes = np.full(days, volume)
    if last_volume is not None:
        volumes[-1] = last_volume
    index = pd.bdate_range(end="2026-07-06", periods=days)
    return pd.DataFrame(
        {
            "Open": opens,
            "High": np.maximum(opens, closes),
            "Low": np.minimum(opens, closes),
            "Close": closes,
            "Volume": volumes,
        },
        index=index,
    )


class TestComputeMetrics:
    def test_basic_metrics(self):
        bars = make_bars(close=100.0, last_close=105.0, volume=2_000_000)
        m = screens.compute_metrics("TEST", bars)
        assert m.last == 105.0
        assert m.pct_chg_1d == pytest.approx(5.0)
        assert m.pct_chg_5d == pytest.approx(5.0)
        # prior 20d avg dollar volume: 100 * 2M
        assert m.dollar_volume == pytest.approx(200_000_000)
        assert m.volume_ratio == pytest.approx(1.0)

    def test_gap_pct_uses_open_vs_prior_close(self):
        bars = make_bars(close=100.0, last_open=103.0, last_close=101.0)
        m = screens.compute_metrics("TEST", bars)
        assert m.gap_pct == pytest.approx(3.0)
        assert m.pct_chg_1d == pytest.approx(1.0)

    def test_volume_ratio_excludes_today_from_baseline(self):
        bars = make_bars(volume=1_000_000, last_volume=3_000_000)
        m = screens.compute_metrics("TEST", bars)
        assert m.volume_ratio == pytest.approx(3.0)

    def test_empty_bars_returns_none(self):
        empty = make_bars().iloc[0:0]
        assert screens.compute_metrics("TEST", empty) is None

    def test_single_bar_has_no_change_metrics(self):
        m = screens.compute_metrics("TEST", make_bars(days=1))
        assert m is not None
        assert m.pct_chg_1d is None
        assert m.pct_chg_5d is None


class TestFloors:
    def test_passes_both_floors(self):
        m = screens.compute_metrics("TEST", make_bars(close=100.0, volume=1_000_000))
        floors = screens.check_floors(m)
        assert floors == {"min_price": True, "min_avg_dollar_volume": True}

    def test_price_floor(self):
        bars = make_bars(close=4.0, volume=100_000_000)
        m = screens.compute_metrics("TEST", bars)
        assert screens.check_floors(m)["min_price"] is False

    def test_dollar_volume_floor(self):
        bars = make_bars(close=100.0, volume=100_000)  # $10M/day < $50M
        m = screens.compute_metrics("TEST", bars)
        assert screens.check_floors(m)["min_avg_dollar_volume"] is False

    def test_floor_boundaries_inclusive(self):
        # exactly $50M avg dollar volume and exactly $5 price pass
        bars = make_bars(close=5.0, volume=10_000_000)
        m = screens.compute_metrics("TEST", bars)
        assert m.dollar_volume == pytest.approx(50_000_000)
        assert all(screens.check_floors(m).values())


class TestScreens:
    def test_mover_1d(self):
        m = screens.compute_metrics("T", make_bars(close=100.0, last_close=103.5))
        assert "movers_1d" in screens.apply_screens(m)

    def test_mover_1d_below_threshold(self):
        m = screens.compute_metrics("T", make_bars(close=100.0, last_close=102.0))
        assert "movers_1d" not in screens.apply_screens(m)

    def test_mover_5d(self):
        m = screens.compute_metrics("T", make_bars(close_5d_ago=100.0, close=109.0, last_close=109.0))
        assert "movers_5d" in screens.apply_screens(m)

    def test_volume_surge(self):
        m = screens.compute_metrics("T", make_bars(volume=1_000_000, last_volume=2_500_000))
        assert "volume_surge" in screens.apply_screens(m)

    def test_gap_up(self):
        m = screens.compute_metrics("T", make_bars(close=100.0, last_open=102.5, last_close=101.0))
        assert "gap_up" in screens.apply_screens(m)

    def test_down_move_matches_nothing(self):
        m = screens.compute_metrics("T", make_bars(close=100.0, last_close=90.0))
        assert screens.apply_screens(m) == []


class TestScreenSymbols:
    def test_filters_and_sorts(self):
        bars = {
            "BIGMOVE": make_bars(close=100.0, last_close=110.0),
            "SMALLMOVE": make_bars(close=100.0, last_close=104.0),
            "CHEAP": make_bars(close=4.0, last_close=4.4, volume=100_000_000),  # fails price floor
            "QUIET": make_bars(close=100.0),  # no screens
        }
        result = screens.screen_symbols(bars)
        symbols = [m.symbol for m in result]
        assert symbols == ["BIGMOVE", "SMALLMOVE"]
        assert "movers_1d" in result[0].screens
        assert result[0].passes_floors

    def test_deterministic_tiebreak_by_symbol(self):
        bars = {
            "BBB": make_bars(close=100.0, last_close=105.0),
            "AAA": make_bars(close=100.0, last_close=105.0),
        }
        assert [m.symbol for m in screens.screen_symbols(bars)] == ["AAA", "BBB"]


class TestRunScreener:
    def test_run_with_injected_fetch(self):
        def fake_fetch(symbols):
            return {"TQQQ": make_bars(close=100.0, last_close=106.0)}

        report = run_screener(offline_universe=True, fetch=fake_fetch)
        assert report["universe"]["source"] == "fallback"
        assert report["fetched"] == 1
        assert report["warnings"]  # most of the universe had no data
        cands = report["candidates"]
        assert len(cands) == 1
        cand = cands[0]
        assert cand["symbol"] == "TQQQ"
        assert cand["passes_floors"] is True
        assert "movers_1d" in cand["screens"]
        for key in ("last", "pct_chg_1d", "pct_chg_5d", "dollar_volume", "volume_ratio", "gap_pct"):
            assert key in cand

    def test_top_limits_candidates(self):
        def fake_fetch(symbols):
            return {
                s: make_bars(close=100.0, last_close=104.0 + i)
                for i, s in enumerate(["AAA", "BBB", "CCC"])
            }

        report = run_screener(top=2, offline_universe=True, fetch=fake_fetch)
        assert len(report["candidates"]) == 2

    def test_total_failure_reports_zero_fetched(self):
        report = run_screener(offline_universe=True, fetch=lambda s: {})
        assert report["fetched"] == 0
        assert report["candidates"] == []


class TestCheckSymbol:
    def test_pass(self):
        def fake_fetch(symbols):
            return {symbols[0]: make_bars(close=100.0)}

        result = check_symbol("aapl", fetch=fake_fetch)
        assert result["symbol"] == "AAPL"
        assert result["ok"] is True

    def test_fail_no_data(self):
        result = check_symbol("NOPE", fetch=lambda s: {})
        assert result["ok"] is False
        assert "error" in result

    def test_fail_floors(self):
        def fake_fetch(symbols):
            return {symbols[0]: make_bars(close=3.0)}

        result = check_symbol("PENNY", fetch=fake_fetch)
        assert result["ok"] is False
        assert result["floors"]["min_price"] is False
