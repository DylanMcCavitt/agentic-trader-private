"""Initial schema: multi-tenant-ready (every table carries account_id).

One row in ``accounts`` per brokerage account; today there is exactly one
(the personal Robinhood account), but the schema is designed so a managed
service can host many.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    broker: Mapped[str] = mapped_column(String(50), default="robinhood")
    # Latest portfolio equity fed by the execution lane (kill-switch input).
    equity: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Account high-water mark; only ever ratchets up. The 30% account
    # kill-switch (envelope.ACCOUNT_KILL_SWITCH_DRAWDOWN) measures from here.
    hwm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    equity_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    sleeves: Mapped[list["Sleeve"]] = relationship(back_populates="account")


class Sleeve(Base):
    __tablename__ = "sleeves"
    __table_args__ = (UniqueConstraint("account_id", "type", name="uq_sleeves_account_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # equity | options
    budget_fraction: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    drawdown_halt_fraction: Mapped[Decimal] = mapped_column(Numeric(6, 4))
    halted: Mapped[bool] = mapped_column(Boolean, default=False)
    hwm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # Latest sleeve value fed by the execution/review lane (per-sleeve
    # drawdown halt input); tracked alongside the account-level equity.
    equity: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    account: Mapped[Account] = relationship(back_populates="sleeves")
    theses: Mapped[list["Thesis"]] = relationship(back_populates="sleeve")


class Thesis(Base):
    __tablename__ = "theses"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    sleeve_id: Mapped[int] = mapped_column(ForeignKey("sleeves.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # long | short (bearish via inverse/puts)
    instrument: Mapped[str] = mapped_column(String(20))  # shares | call | put | etf
    entry: Mapped[str] = mapped_column(Text)
    exit: Mapped[str] = mapped_column(Text)
    invalidation: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="proposed", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    sleeve: Mapped[Sleeve] = relationship(back_populates="theses")
    orders: Mapped[list["Order"]] = relationship(back_populates="thesis")
    grades: Mapped[list["Grade"]] = relationship(back_populates="thesis")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    sleeve_id: Mapped[int | None] = mapped_column(ForeignKey("sleeves.id"), index=True)
    thesis_id: Mapped[int | None] = mapped_column(ForeignKey("theses.id"), index=True)
    ref_id: Mapped[str] = mapped_column(String(64), unique=True)
    symbol: Mapped[str | None] = mapped_column(String(20), index=True)
    instrument: Mapped[str | None] = mapped_column(String(20))  # equity | option
    side: Mapped[str] = mapped_column(String(10))  # buy | sell
    qty: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    notional: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # pending | simulated | denied | filled | partially_filled | cancelled |
    # rejected | unmatched
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    gate_verdict: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # full order as composed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    thesis: Mapped[Thesis | None] = relationship(back_populates="orders")
    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    order: Mapped[Order] = relationship(back_populates="fills")


class Grade(Base):
    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    thesis_id: Mapped[int] = mapped_column(ForeignKey("theses.id"), index=True)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    rubric: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    thesis: Mapped[Thesis] = relationship(back_populates="grades")


class ParamHistory(Base):
    __tablename__ = "param_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    param_name: Mapped[str] = mapped_column(String(100), index=True)
    old_value: Mapped[str | None] = mapped_column(String(100))
    new_value: Mapped[str] = mapped_column(String(100))
    evidence: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(50))  # human | improve-lane
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Quote(Base):
    """Quote/screen snapshot stored by the execution lane.

    Gates read the most recent row per symbol for quote-freshness and
    liquidity checks. ``symbol`` is the underlying ticker for both equity
    and option quotes; option-specific fields (open interest, bid/ask of
    the contract) live in the dedicated columns / payload.
    """

    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="equity")  # equity | option
    price: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))  # last/mark
    bid: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    ask: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    avg_dollar_volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(14, 0))
    # For option quotes: identifies the contract this row describes.
    occ_symbol: Mapped[str | None] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    quoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class LaneRun(Base):
    __tablename__ = "lane_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    lane: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running", index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    artifact: Mapped[dict[str, Any] | None] = mapped_column(JSON)
