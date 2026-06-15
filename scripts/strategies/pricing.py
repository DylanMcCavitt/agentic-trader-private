"""Black-Scholes pricing for the options-fleet backtest.

Backtest-only approximation: there is no free historical IV surface, so
scripts/backtest_fleet.py prices synthetic contracts with BS using EWMA
realized vol x an IV premium factor. Stdlib only (math.erf), no scipy.
"""
import math


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, t_years: float, sigma: float,
             r: float, right: str, q: float = 0.0) -> float:
    """European Black-Scholes-Merton price with continuous dividend yield q.

    Degrades to (discounted) intrinsic when the option has no time or no
    volatility left. q defaults to 0.0 to preserve the old Black-Scholes API.
    """
    intrinsic = max(0.0, spot - strike) if right == "call" else max(0.0, strike - spot)
    if t_years <= 0:
        return intrinsic
    spot_disc = spot * math.exp(-q * t_years)
    strike_disc = strike * math.exp(-r * t_years)
    if sigma <= 0:
        fwd = spot_disc - strike_disc if right == "call" else strike_disc - spot_disc
        return max(0.0, fwd)
    d1 = ((math.log(spot / strike) + (r - q + sigma ** 2 / 2) * t_years)
          / (sigma * math.sqrt(t_years)))
    d2 = d1 - sigma * math.sqrt(t_years)
    if right == "call":
        return spot_disc * norm_cdf(d1) - strike_disc * norm_cdf(d2)
    return strike_disc * norm_cdf(-d2) - spot_disc * norm_cdf(-d1)
