"""Aggressive autonomous trader.

Two virtual sleeves (equity, options) over one Robinhood account, driven by a
five-lane pipeline (Research, Thesis, Risk, Execution, Review) plus a weekly
IMPROVE lane, all bounded by the hard outer envelope in `trader.envelope`.
"""

__version__ = "0.1.0"
