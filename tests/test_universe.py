"""Universe construction: fallback loading and normalization. No network."""

from trader.screener import universe


def test_fallback_list_loads_and_is_substantial():
    symbols = universe.load_fallback()
    assert len(symbols) > 400  # S&P 500 + Nasdaq-100 union
    assert "AAPL" in symbols
    assert all(s == s.upper() for s in symbols)
    assert all("." not in s for s in symbols)  # normalized to yfinance format


def test_normalize_symbol():
    assert universe.normalize_symbol("brk.b") == "BRK-B"
    assert universe.normalize_symbol(" AAPL ") == "AAPL"


def test_build_universe_offline_uses_fallback_and_adds_etfs():
    symbols, source = universe.build_universe(offline=True)
    assert source == "fallback"
    for etf in ("TQQQ", "SQQQ", "SOXL", "SOXS"):
        assert etf in symbols
    assert "AAPL" in symbols
    assert symbols == sorted(set(symbols))


def test_build_universe_falls_back_when_fetch_fails(monkeypatch):
    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(universe, "fetch_index_constituents", boom)
    symbols, source = universe.build_universe(offline=False)
    assert source == "fallback"
    assert "AAPL" in symbols
