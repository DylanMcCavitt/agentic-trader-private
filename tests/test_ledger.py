from decimal import Decimal

import pytest

from trader.sleeves import ledger

from tests.gate_helpers import add_filled_position, add_pending_order, make_account


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")


def test_init_creates_account_and_two_sleeves(db_session):
    account = ledger.init_sleeves(db_session)
    types = {s.type: s for s in account.sleeves}
    assert set(types) == {"equity", "options"}
    assert float(types["options"].budget_fraction) == pytest.approx(0.25)
    assert float(types["equity"].budget_fraction) == pytest.approx(0.75)
    assert float(types["equity"].drawdown_halt_fraction) == pytest.approx(0.15)


def test_init_is_idempotent(db_session):
    ledger.init_sleeves(db_session)
    account = ledger.init_sleeves(db_session)
    assert len(account.sleeves) == 2


def test_buy_fills_build_average_cost_position(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=100, ref_id="b1")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=110, ref_id="b2")

    positions = ledger.positions_for_sleeve(db_session, sleeve)
    pos = positions["NVDA"]
    assert pos.qty == Decimal("20")
    assert pos.avg_price == Decimal("105")
    assert pos.cost_basis == Decimal("2100")


def test_sell_realizes_pnl_against_average_cost(db_session):
    from datetime import timedelta

    from trader.db.models import Fill, Order
    from tests.gate_helpers import NOW

    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=100, ref_id="b1")

    sell = Order(
        account_id=account.id, sleeve_id=sleeve.id, ref_id="s1", symbol="NVDA",
        instrument="equity", side="sell", qty=Decimal("4"), status="filled",
        payload={"position_key": "NVDA"},
    )
    db_session.add(sell)
    db_session.flush()
    db_session.add(
        Fill(account_id=account.id, order_id=sell.id, qty=Decimal("4"),
             price=Decimal("120"), filled_at=NOW + timedelta(hours=1))
    )
    db_session.commit()

    pos = ledger.positions_for_sleeve(db_session, sleeve)["NVDA"]
    assert pos.qty == Decimal("6")
    assert pos.realized_pnl == Decimal("80")  # (120-100) * 4
    assert pos.cost_basis == Decimal("600")


def test_option_positions_use_100x_multiplier(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=2, price=5.0,
        instrument="option", position_key="NVDA260807C00200000", ref_id="o1",
    )
    pos = ledger.positions_for_sleeve(db_session, sleeve)["NVDA260807C00200000"]
    assert pos.cost_basis == Decimal("1000")  # 2 x $5 x 100
    assert pos.avg_price == Decimal("5")


def test_distinct_contracts_are_distinct_positions(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=1, price=5.0,
        instrument="option", position_key="NVDA260807C00200000", ref_id="o1",
    )
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=1, price=3.0,
        instrument="option", position_key="NVDA260807P00180000", ref_id="o2",
    )
    assert len(ledger.open_positions(db_session, sleeve)) == 2
    assert ledger.open_position_count(db_session, account) == 2


def test_pending_buys_count_toward_exposure_and_positions(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=100)
    add_pending_order(db_session, account, sleeve, ref_id="p1", symbol="AMD", notional=2_000)

    assert ledger.pending_buy_exposure(db_session, sleeve) == Decimal("2000")
    assert ledger.open_position_count(db_session, account) == 2

    report = ledger.sleeve_report(db_session, account, sleeve)
    assert report.open_exposure == pytest.approx(1_000.0)
    assert report.pending_exposure == pytest.approx(2_000.0)
    # equity 100k, budget 75% = 75k, minus 3k committed
    assert report.remaining_budget == pytest.approx(72_000.0)


def test_sleeve_report_without_equity_has_unknown_budget(db_session):
    account = make_account(db_session, equity=None)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    report = ledger.sleeve_report(db_session, account, sleeve)
    assert report.budget_dollars is None
    assert report.remaining_budget is None


def test_closed_position_drops_from_open_but_keeps_pnl(db_session):
    from datetime import timedelta

    from trader.db.models import Fill, Order
    from tests.gate_helpers import NOW

    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=100, ref_id="b1")
    sell = Order(
        account_id=account.id, sleeve_id=sleeve.id, ref_id="s1", symbol="NVDA",
        instrument="equity", side="sell", qty=Decimal("10"), status="filled",
        payload={"position_key": "NVDA"},
    )
    db_session.add(sell)
    db_session.flush()
    db_session.add(
        Fill(account_id=account.id, order_id=sell.id, qty=Decimal("10"),
             price=Decimal("90"), filled_at=NOW + timedelta(hours=1))
    )
    db_session.commit()

    assert ledger.open_positions(db_session, sleeve) == []
    report = ledger.sleeve_report(db_session, account, sleeve)
    assert report.realized_pnl == pytest.approx(-100.0)
    assert report.open_exposure == pytest.approx(0.0)
