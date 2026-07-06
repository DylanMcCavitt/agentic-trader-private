from decimal import Decimal

import pytest
from sqlalchemy import select

from trader.db.models import Fill, LaneRun, Order
from trader.sleeves import ledger, reconcile as rec

from tests.gate_helpers import add_pending_order, make_account


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")


def broker_order(ref_id="p1", state="filled", executions=None):
    return {
        "ref_id": ref_id,
        "state": state,
        "executions": executions
        if executions is not None
        else [{"quantity": 1, "price": 100.0, "timestamp": "2026-07-06T15:05:00Z"}],
    }


def test_matched_order_writes_fill_and_updates_status(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_pending_order(db_session, account, sleeve, ref_id="p1", symbol="NVDA", notional=100)

    result = rec.reconcile(db_session, [broker_order()])
    assert result.clean
    assert result.matched == 1
    assert result.fills_written == 1

    order = db_session.execute(select(Order).where(Order.ref_id == "p1")).scalar_one()
    assert order.status == "filled"
    fill = db_session.execute(select(Fill)).scalar_one()
    assert fill.qty == Decimal("1")
    assert fill.price == Decimal("100")


def test_reconcile_is_idempotent(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_pending_order(db_session, account, sleeve, ref_id="p1", symbol="NVDA", notional=100)

    rec.reconcile(db_session, [broker_order()])
    result = rec.reconcile(db_session, [broker_order()])
    assert result.fills_written == 0
    assert len(db_session.execute(select(Fill)).scalars().all()) == 1


def test_partial_fill_then_complete(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    order = add_pending_order(db_session, account, sleeve, ref_id="p1", symbol="NVDA", notional=500)
    order.qty = Decimal("5")
    db_session.commit()

    rec.reconcile(
        db_session,
        [broker_order(state="partially_filled", executions=[{"quantity": 2, "price": 100.0}])],
    )
    assert db_session.execute(select(Order).where(Order.ref_id == "p1")).scalar_one().status == "partially_filled"

    rec.reconcile(
        db_session,
        [broker_order(state="filled", executions=[
            {"quantity": 2, "price": 100.0}, {"quantity": 3, "price": 101.0},
        ])],
    )
    order = db_session.execute(select(Order).where(Order.ref_id == "p1")).scalar_one()
    assert order.status == "filled"
    fills = db_session.execute(select(Fill)).scalars().all()
    assert sum(f.qty for f in fills) == Decimal("5")


def test_cancelled_and_rejected_states(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_pending_order(db_session, account, sleeve, ref_id="c1", symbol="NVDA", notional=100)
    add_pending_order(db_session, account, sleeve, ref_id="r1", symbol="AMD", notional=100)

    result = rec.reconcile(
        db_session,
        [
            broker_order(ref_id="c1", state="cancelled", executions=[]),
            broker_order(ref_id="r1", state="rejected", executions=[]),
        ],
    )
    assert result.clean
    statuses = {
        o.ref_id: o.status
        for o in db_session.execute(select(Order)).scalars()
    }
    assert statuses["c1"] == "cancelled"
    assert statuses["r1"] == "rejected"


def test_unauthorized_broker_order_is_flagged(db_session):
    make_account(db_session)
    result = rec.reconcile(db_session, [broker_order(ref_id="rogue-999")])
    assert not result.clean
    assert len(result.unauthorized) == 1

    event = db_session.execute(select(LaneRun).where(LaneRun.lane == "reconcile")).scalar_one()
    assert event.status == "flagged"
    assert event.artifact["unauthorized"][0]["ref_id"] == "rogue-999"


def test_broker_order_without_ref_id_is_unauthorized(db_session):
    make_account(db_session)
    result = rec.reconcile(db_session, [{"state": "filled", "executions": []}])
    assert not result.clean
    assert len(result.unauthorized) == 1


def test_gate_order_missing_at_broker_is_flagged(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_pending_order(db_session, account, sleeve, ref_id="ghost-1", symbol="NVDA", notional=100)

    result = rec.reconcile(db_session, [])
    assert not result.clean
    assert result.missing_at_broker == ["ghost-1"]
    order = db_session.execute(select(Order).where(Order.ref_id == "ghost-1")).scalar_one()
    assert order.status == "unmatched"

    event = db_session.execute(select(LaneRun).where(LaneRun.lane == "reconcile")).scalar_one()
    assert event.status == "flagged"


def test_simulated_and_denied_orders_not_expected_at_broker(db_session):
    account = make_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    for ref_id, status in (("sim-1", "simulated"), ("den-1", "denied")):
        order = add_pending_order(db_session, account, sleeve, ref_id=ref_id, symbol="NVDA", notional=100)
        order.status = status
    db_session.commit()

    result = rec.reconcile(db_session, [])
    assert result.clean


def test_clean_run_writes_ok_event(db_session):
    make_account(db_session)
    result = rec.reconcile(db_session, [])
    assert result.clean
    event = db_session.execute(select(LaneRun).where(LaneRun.lane == "reconcile")).scalar_one()
    assert event.status == "ok"


def test_parse_broker_orders_envelopes():
    orders = [{"ref_id": "a"}]
    assert rec.parse_broker_orders(orders) == orders
    assert rec.parse_broker_orders({"results": orders}) == orders
    assert rec.parse_broker_orders({"orders": orders}) == orders
    assert rec.parse_broker_orders('[{"ref_id": "a"}]') == orders
    with pytest.raises(ValueError):
        rec.parse_broker_orders({"nope": 1})
    with pytest.raises(ValueError):
        rec.parse_broker_orders("42")
