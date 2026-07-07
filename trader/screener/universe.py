"""Scannable universe construction.

yfinance has no true screener endpoint, so the universe is built
programmatically:

- S&P 500 + Nasdaq-100 constituents, scraped from Wikipedia (bs4, no extra
  deps). When the network or page layout fails, a static fallback list
  committed to the repo (``universe_fallback.txt``) is used so offline/CI
  runs stay deterministic.
- A curated list of liquid ETFs, including leveraged and inverse products,
  so the equity sleeve can express bearish views long-only.

Symbols are normalized to yfinance format (``BRK.B`` -> ``BRK-B``).
"""

from __future__ import annotations

import re
from pathlib import Path

_WIKI_SOURCES = [
    ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("symbol",)),
    ("https://en.wikipedia.org/wiki/Nasdaq-100", ("ticker", "symbol")),
]

_FALLBACK_PATH = Path(__file__).with_name("universe_fallback.txt")

_SYMBOL_RE = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")

# Liquid ETFs, incl. leveraged/inverse — the equity sleeve is long-only, so
# bearish exposure comes from the inverse products in this list.
CURATED_ETFS = [
    # Broad index
    "SPY", "QQQ", "IWM", "DIA",
    # Leveraged long / inverse index
    "TQQQ", "SQQQ", "UPRO", "SPXU", "TNA", "TZA", "QLD", "SSO",
    # Semis / tech leveraged
    "SOXL", "SOXS", "SOXX", "SMH", "USD",
    # Single-stock / thematic leveraged
    "NVDL", "TSLL", "FNGU",
    # Sector
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XBI", "LABU", "LABD",
    # Volatility
    "UVXY", "VXX", "SVXY",
    # Commodities / crypto / rates
    "GLD", "SLV", "GDX", "USO", "UNG", "BITO", "IBIT", "TLT", "TMF", "TMV",
    # International / breadth
    "EEM", "FXI", "YINN", "ARKK",
]


def normalize_symbol(symbol: str) -> str:
    """Normalize an exchange symbol to yfinance format (dots -> dashes)."""
    return symbol.strip().upper().replace(".", "-")


def load_fallback(path: Path | None = None) -> list[str]:
    """Load the committed static constituent list (offline/CI path)."""
    raw = (path or _FALLBACK_PATH).read_text().split()
    return sorted({normalize_symbol(s) for s in raw if s})


def _scrape_wikipedia_symbols(url: str, header_names: tuple[str, ...]) -> list[str]:
    import requests
    from bs4 import BeautifulSoup

    html = requests.get(url, headers={"User-Agent": "agentic-trader/0.1"}, timeout=30).text
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table", class_="wikitable"):
        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        idx = next((headers.index(n) for n in header_names if n in headers), None)
        if idx is None:
            continue
        symbols = []
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) > idx:
                text = cells[idx].get_text(strip=True)
                if _SYMBOL_RE.fullmatch(text):
                    symbols.append(text)
        # A constituents table has hundreds of rows; anything smaller is a
        # lookalike (e.g. "annual returns" tables on the Nasdaq-100 page).
        if len(symbols) > 50:
            return symbols
    return []


def fetch_index_constituents() -> list[str]:
    """S&P 500 + Nasdaq-100 symbols from Wikipedia; raises on failure."""
    symbols: set[str] = set()
    for url, header_names in _WIKI_SOURCES:
        scraped = _scrape_wikipedia_symbols(url, header_names)
        if not scraped:
            raise RuntimeError(f"no constituents table found at {url}")
        symbols.update(normalize_symbol(s) for s in scraped)
    return sorted(symbols)


def build_universe(*, offline: bool = False) -> tuple[list[str], str]:
    """Return (symbols, source) — source is 'wikipedia' or 'fallback'.

    Index constituents come from Wikipedia when reachable, else from the
    committed fallback file. Curated ETFs are always included.
    """
    source = "fallback"
    constituents: list[str] = []
    if not offline:
        try:
            constituents = fetch_index_constituents()
            source = "wikipedia"
        except Exception:
            constituents = []
    if not constituents:
        constituents = load_fallback()
        source = "fallback"
    symbols = sorted(set(constituents) | {normalize_symbol(s) for s in CURATED_ETFS})
    return symbols, source
