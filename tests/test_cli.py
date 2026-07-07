import pytest

from trader import cli


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "db" in out
    assert "params" in out


def test_params_show_defaults(capsys):
    assert cli.main(["params", "show", "--defaults"]) == 0
    out = capsys.readouterr().out
    assert "options_sleeve_budget_fraction" in out
    assert "max_trades_per_day" in out
