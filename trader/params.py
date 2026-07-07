"""Tunable runtime parameters, validated against the hard outer envelope.

Current values are derived from the database: the latest ``param_history``
row for a given param wins; params with no history use the envelope default.
The IMPROVE lane changes a param by appending a ``param_history`` row (with
evidence); it can never move a value outside the envelope bounds because
every write path goes through :func:`validate`.
"""

from __future__ import annotations

from dataclasses import dataclass

from trader import envelope


class EnvelopeViolation(ValueError):
    """Raised when a proposed param value falls outside the hard envelope."""


@dataclass(frozen=True)
class ParamSpec:
    name: str
    min: float
    max: float
    default: float
    integer: bool = False
    description: str = ""


SPECS: dict[str, ParamSpec] = {
    spec.name: spec
    for spec in [
        ParamSpec(
            name="options_sleeve_budget_fraction",
            min=envelope.OPTIONS_SLEEVE_BUDGET_MIN,
            max=envelope.OPTIONS_SLEEVE_BUDGET_MAX,
            default=envelope.OPTIONS_SLEEVE_BUDGET_DEFAULT,
            description="Fraction of account allocated to the options sleeve",
        ),
        ParamSpec(
            name="per_position_max_fraction",
            min=envelope.PER_POSITION_MIN,
            max=envelope.PER_POSITION_MAX,
            default=envelope.PER_POSITION_DEFAULT,
            description="Max fraction of account per position",
        ),
        ParamSpec(
            name="max_concurrent_positions",
            min=envelope.CONCURRENT_POSITIONS_MIN,
            max=envelope.CONCURRENT_POSITIONS_MAX,
            default=envelope.CONCURRENT_POSITIONS_DEFAULT,
            integer=True,
            description="Max concurrent open positions across sleeves",
        ),
        ParamSpec(
            name="sleeve_drawdown_halt_fraction",
            min=envelope.SLEEVE_DRAWDOWN_HALT_MIN,
            max=envelope.SLEEVE_DRAWDOWN_HALT_MAX,
            default=envelope.SLEEVE_DRAWDOWN_HALT_DEFAULT,
            description="Per-sleeve drawdown (from sleeve HWM) that halts the sleeve",
        ),
        ParamSpec(
            name="max_trades_per_day",
            min=envelope.TRADES_PER_DAY_MIN,
            max=envelope.TRADES_PER_DAY_MAX,
            default=envelope.TRADES_PER_DAY_DEFAULT,
            integer=True,
            description="Max live orders per trading day across sleeves",
        ),
        ParamSpec(
            name="dte_min_days",
            min=envelope.DTE_MIN,
            max=envelope.DTE_MAX,
            default=envelope.DTE_WINDOW_DEFAULT_MIN,
            integer=True,
            description="Minimum days-to-expiration for option buys",
        ),
        ParamSpec(
            name="dte_max_days",
            min=envelope.DTE_MIN,
            max=envelope.DTE_MAX,
            default=envelope.DTE_WINDOW_DEFAULT_MAX,
            integer=True,
            description="Maximum days-to-expiration for option buys",
        ),
    ]
}


def validate(name: str, value: float, *, clamp: bool = False) -> float:
    """Validate ``value`` for param ``name`` against the envelope.

    Raises :class:`EnvelopeViolation` for unknown params or out-of-bounds
    values. With ``clamp=True``, out-of-bounds values are clamped to the
    nearest bound instead of rejected. Integer params are rejected (or
    rounded, when clamping) if not whole numbers.
    """
    spec = SPECS.get(name)
    if spec is None:
        raise EnvelopeViolation(f"unknown param {name!r}")

    if spec.integer:
        if clamp:
            value = round(value)
        elif value != int(value):
            raise EnvelopeViolation(f"{name} must be an integer, got {value}")

    if value < spec.min or value > spec.max:
        if clamp:
            value = min(max(value, spec.min), spec.max)
        else:
            raise EnvelopeViolation(
                f"{name}={value} outside envelope [{spec.min}, {spec.max}]"
            )

    return int(value) if spec.integer else float(value)


def defaults() -> dict[str, float]:
    """Envelope-default value for every param."""
    return {name: (int(s.default) if s.integer else s.default) for name, s in SPECS.items()}


def current(session=None) -> dict[str, float]:
    """Current param values: latest param_history row wins, else default.

    ``session`` is a SQLAlchemy session; when None, defaults are returned
    (useful before the DB exists, e.g. `trader params show` offline).
    """
    values = defaults()
    if session is None:
        return values

    from sqlalchemy import select

    from trader.db.models import ParamHistory

    seen: set[str] = set()
    rows = session.execute(
        select(ParamHistory).order_by(ParamHistory.created_at.desc(), ParamHistory.id.desc())
    ).scalars()
    for row in rows:
        if row.param_name in seen or row.param_name not in SPECS:
            continue
        seen.add(row.param_name)
        values[row.param_name] = validate(row.param_name, float(row.new_value), clamp=True)
    return values
