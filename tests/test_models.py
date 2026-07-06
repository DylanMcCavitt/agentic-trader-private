"""Schema smoke test: models import, tables create, a full object graph persists."""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from trader.db import models


def test_all_tables_present():
    names = set(models.Base.metadata.tables)
    assert names == {
        "accounts",
        "sleeves",
        "theses",
        "orders",
        "fills",
        "grades",
        "param_history",
        "lane_runs",
    }


def test_every_table_is_account_scoped():
    for name, table in models.Base.metadata.tables.items():
        if name == "accounts":
            continue
        assert "account_id" in table.columns, f"{name} missing account_id"


def test_insert_full_graph(db_session):
    account = models.Account(name="personal")
    db_session.add(account)
    db_session.flush()

    sleeve = models.Sleeve(
        account_id=account.id,
        type="options",
        budget_fraction=Decimal("0.25"),
        drawdown_halt_fraction=Decimal("0.15"),
        hwm=Decimal("10000.00"),
    )
    db_session.add(sleeve)
    db_session.flush()

    thesis = models.Thesis(
        account_id=account.id,
        sleeve_id=sleeve.id,
        symbol="NVDA",
        direction="long",
        instrument="call",
        entry="buy 30-DTE call on volume surge",
        exit="+50% or 5 trading days",
        invalidation="close below 20d MA",
        payload={"dte": 30},
    )
    db_session.add(thesis)
    db_session.flush()

    order = models.Order(
        account_id=account.id,
        thesis_id=thesis.id,
        ref_id="trader-0001",
        side="buy",
        qty=Decimal("1"),
        gate_verdict={"approved": True},
    )
    db_session.add(order)
    db_session.flush()

    db_session.add_all(
        [
            models.Fill(
                account_id=account.id,
                order_id=order.id,
                qty=Decimal("1"),
                price=Decimal("3.50"),
                filled_at=datetime.now(timezone.utc),
            ),
            models.Grade(
                account_id=account.id,
                thesis_id=thesis.id,
                score=Decimal("7.5"),
                rubric={"entry_quality": "good"},
            ),
            models.LaneRun(account_id=account.id, lane="execution", status="ok"),
        ]
    )
    db_session.commit()

    loaded = db_session.execute(select(models.Order)).scalar_one()
    assert loaded.ref_id == "trader-0001"
    assert loaded.thesis.symbol == "NVDA"
    assert len(loaded.fills) == 1
