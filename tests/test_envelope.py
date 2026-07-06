"""Envelope sanity: bounds ordered, defaults inside bounds, kill-switch fixed."""

from trader import envelope


def test_bounds_ordered():
    assert envelope.OPTIONS_SLEEVE_BUDGET_MIN < envelope.OPTIONS_SLEEVE_BUDGET_MAX
    assert envelope.PER_POSITION_MIN < envelope.PER_POSITION_MAX
    assert envelope.CONCURRENT_POSITIONS_MIN < envelope.CONCURRENT_POSITIONS_MAX
    assert envelope.SLEEVE_DRAWDOWN_HALT_MIN < envelope.SLEEVE_DRAWDOWN_HALT_MAX
    assert envelope.TRADES_PER_DAY_MIN < envelope.TRADES_PER_DAY_MAX
    assert envelope.DTE_MIN < envelope.DTE_MAX


def test_defaults_inside_bounds():
    assert (
        envelope.OPTIONS_SLEEVE_BUDGET_MIN
        <= envelope.OPTIONS_SLEEVE_BUDGET_DEFAULT
        <= envelope.OPTIONS_SLEEVE_BUDGET_MAX
    )
    assert envelope.PER_POSITION_MIN <= envelope.PER_POSITION_DEFAULT <= envelope.PER_POSITION_MAX
    assert (
        envelope.CONCURRENT_POSITIONS_MIN
        <= envelope.CONCURRENT_POSITIONS_DEFAULT
        <= envelope.CONCURRENT_POSITIONS_MAX
    )
    assert (
        envelope.SLEEVE_DRAWDOWN_HALT_MIN
        <= envelope.SLEEVE_DRAWDOWN_HALT_DEFAULT
        <= envelope.SLEEVE_DRAWDOWN_HALT_MAX
    )
    assert envelope.TRADES_PER_DAY_MIN <= envelope.TRADES_PER_DAY_DEFAULT <= envelope.TRADES_PER_DAY_MAX
    assert envelope.DTE_MIN <= envelope.DTE_WINDOW_DEFAULT_MIN < envelope.DTE_WINDOW_DEFAULT_MAX <= envelope.DTE_MAX


def test_fraction_bounds_sane():
    for value in (
        envelope.OPTIONS_SLEEVE_BUDGET_MIN,
        envelope.OPTIONS_SLEEVE_BUDGET_MAX,
        envelope.PER_POSITION_MIN,
        envelope.PER_POSITION_MAX,
        envelope.SLEEVE_DRAWDOWN_HALT_MIN,
        envelope.SLEEVE_DRAWDOWN_HALT_MAX,
        envelope.ACCOUNT_KILL_SWITCH_DRAWDOWN,
    ):
        assert 0 < value < 1


def test_kill_switch_is_fixed_thirty_percent():
    assert envelope.ACCOUNT_KILL_SWITCH_DRAWDOWN == 0.30
