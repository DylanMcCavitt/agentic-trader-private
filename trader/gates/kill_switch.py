"""Kill-switch: account-level (fixed 30% from HWM) and per-sleeve halts.

Part of the trust boundary (``trader/gates/`` — human-only).

The execution lane feeds portfolio equity in via ``trader kill-switch
update --equity X`` (optionally with per-sleeve values); the gates read the
latest stored state. High-water marks only ever ratchet up. The account
drawdown threshold comes from :mod:`trader.envelope` and is not tunable;
per-sleeve halt fractions are tunable params inside the envelope.

Fail-closed: if equity has never been fed, the account state is unknown and
the gates must deny.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trader import envelope
from trader.db.models import Account, Sleeve, utcnow
from trader.gates import runtime


@dataclass
class SleeveStatus:
    sleeve_id: int
    type: str
    equity: float | None
    hwm: float | None
    drawdown: float | None
    halt_fraction: float
    halted: bool
    reason: str = ""


@dataclass
class KillSwitchStatus:
    account_id: int | None
    equity: float | None
    hwm: float | None
    drawdown: float | None
    account_halted: bool
    reason: str = ""
    sleeves: list[SleeveStatus] = field(default_factory=list)


def _drawdown(equity: Decimal | None, hwm: Decimal | None) -> float | None:
    if equity is None or hwm is None or hwm <= 0:
        return None
    return float((hwm - equity) / hwm)


def status(session, config: dict | None = None) -> KillSwitchStatus:
    """Compute kill-switch state from the latest stored equity/HWM values.

    Unknown state (no account row, equity never fed) reports the account as
    halted with an explanatory reason — the gates fail closed on it.
    """
    account = runtime.get_account(session, config)
    if account is None:
        return KillSwitchStatus(
            account_id=None,
            equity=None,
            hwm=None,
            drawdown=None,
            account_halted=True,
            reason="no account row — run `trader sleeves init` and feed equity",
        )

    dd = _drawdown(account.equity, account.hwm)
    if dd is None:
        result = KillSwitchStatus(
            account_id=account.id,
            equity=float(account.equity) if account.equity is not None else None,
            hwm=float(account.hwm) if account.hwm is not None else None,
            drawdown=None,
            account_halted=True,
            reason="account equity/HWM unknown — feed via `trader kill-switch update --equity X`",
        )
    else:
        tripped = dd >= envelope.ACCOUNT_KILL_SWITCH_DRAWDOWN
        result = KillSwitchStatus(
            account_id=account.id,
            equity=float(account.equity),
            hwm=float(account.hwm),
            drawdown=dd,
            account_halted=tripped,
            reason=(
                f"account drawdown {dd:.1%} >= kill-switch "
                f"{envelope.ACCOUNT_KILL_SWITCH_DRAWDOWN:.0%} from HWM"
                if tripped
                else "ok"
            ),
        )

    for sleeve in sorted(account.sleeves, key=lambda s: s.type):
        sdd = _drawdown(sleeve.equity, sleeve.hwm)
        halt_fraction = float(sleeve.drawdown_halt_fraction)
        tripped = sleeve.halted or (sdd is not None and sdd >= halt_fraction)
        reason = "ok"
        if sleeve.halted:
            reason = "halted flag latched"
        elif tripped:
            reason = f"sleeve drawdown {sdd:.1%} >= halt fraction {halt_fraction:.0%}"
        result.sleeves.append(
            SleeveStatus(
                sleeve_id=sleeve.id,
                type=sleeve.type,
                equity=float(sleeve.equity) if sleeve.equity is not None else None,
                hwm=float(sleeve.hwm) if sleeve.hwm is not None else None,
                drawdown=sdd,
                halt_fraction=halt_fraction,
                halted=tripped,
                reason=reason,
            )
        )
    return result


def update(
    session,
    equity: float,
    sleeve_values: dict[str, float] | None = None,
    config: dict | None = None,
) -> KillSwitchStatus:
    """Feed new account equity (and optional per-sleeve values) into the DB.

    Ratchets HWMs up, latches ``sleeve.halted`` when a sleeve crosses its
    halt fraction, and returns the resulting status. Un-halting a sleeve is
    a deliberate human action (flip the flag in the DB), not automatic.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")
    account = runtime.get_account(session, config)
    if account is None:
        raise ValueError("no account row — run `trader sleeves init` first")

    eq = Decimal(str(equity))
    account.equity = eq
    account.hwm = max(account.hwm, eq) if account.hwm is not None else eq
    account.equity_updated_at = utcnow()

    for sleeve in account.sleeves:
        value = (sleeve_values or {}).get(sleeve.type)
        if value is not None:
            sv = Decimal(str(value))
            sleeve.equity = sv
            sleeve.hwm = max(sleeve.hwm, sv) if sleeve.hwm is not None else sv
        sdd = _drawdown(sleeve.equity, sleeve.hwm)
        if sdd is not None and sdd >= float(sleeve.drawdown_halt_fraction):
            sleeve.halted = True

    session.commit()
    return status(session, config)


def account_halted(session, config: dict | None = None) -> tuple[bool, str]:
    """Convenience for the gates: (halted, reason). Fails closed."""
    try:
        st = status(session, config)
    except Exception as exc:  # any DB failure means unknown state
        return True, f"kill-switch state unavailable: {exc.__class__.__name__}"
    return st.account_halted, st.reason


def sleeve_halted(session, sleeve: Sleeve) -> tuple[bool, str]:
    """(halted, reason) for one sleeve. Fails closed on unknown drawdown only
    if the halted flag is set; a sleeve with no equity fed yet is not halted
    (budget checks still constrain it), but the account-level unknown-equity
    check will already have denied in that case."""
    if sleeve.halted:
        return True, f"{sleeve.type} sleeve is halted"
    sdd = _drawdown(sleeve.equity, sleeve.hwm)
    if sdd is not None and sdd >= float(sleeve.drawdown_halt_fraction):
        return True, (
            f"{sleeve.type} sleeve drawdown {sdd:.1%} >= halt fraction "
            f"{float(sleeve.drawdown_halt_fraction):.0%}"
        )
    return False, "ok"
