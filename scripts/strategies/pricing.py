"""Black-Scholes pricing for the options-fleet backtest.

Backtest-only approximation: there is no free historical IV surface, so
scripts/backtest_fleet.py prices synthetic contracts with BS using EWMA
realized vol x an IV premium factor. Stdlib only (math.erf), no scipy.
"""
import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float,
             r: float, right: str) -> float:
    """European BS price; degrades to (discounted) intrinsic when the option
    has no time or no vol left."""
    intrinsic = max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
    if t_years <= 0:
        return intrinsic
    if sigma <= 0:
        fwd = (spot - strike * math.exp(-r * t_years) if right == "call"
               else strike * math.exp(-r * t_years) - spot)
        return max(0.0, fwd)
    d1 = ((math.log(spot / strike) + (r + sigma ** 2 / 2) * t_years)
          / (sigma * math.sqrt(t_years)))
    d2 = d1 - sigma * math.sqrt(t_years)
    if right == "call":
        return spot * norm_cdf(d1) - strike * math.exp(-r * t_years) * norm_cdf(d2)
    return strike * math.exp(-r * t_years) * norm_cdf(-d2) - spot * norm_cdf(-d1)
