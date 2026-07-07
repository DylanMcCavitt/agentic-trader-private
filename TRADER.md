> Disclaimer: this file is part of educational/reference software and is not financial or investment advice.

# Daily trading run — RSI(2) mean reversion on SPY

You are executing one scheduled trading check in the Robinhood **Agentic** account.
Follow these steps exactly. Do not improvise trades, symbols, or sizing. The
strategy decision comes from `scripts/decide_with_quote.py` / `scripts/decide.py`
only — never from your own market opinion. All numbered steps are mandatory.

1. **Load context.** Run `python3 scripts/load_config.py` to get the effective
   config (`config.json` deep-merged with untracked `config.local.json`, which
   holds the real `account_number`). Use that merged config everywhere below.
   If `account_number` is missing or `REPLACE_ME`, stop — the order gate will
   block everything anyway. Read `state/state.json`. If `halt` is true: log
   "halted: <reason>" to the journal (step 10), notify (step 12), stop.

2. **Confirm the market is open today.** Get a SPY quote via the Robinhood MCP
   (`get_equity_quotes`). If the quote's last-trade timestamp is not from today
   (market holiday), journal "market closed", notify, stop. Preserve the raw
   quote payload (price and timestamp) for step 5; do not hand-copy only the
   price.

3. **Portfolio + kill switch.** Call `get_portfolio` for the account in the
   merged config, then run:
   `uv run scripts/drawdown_kill.py --total-value <portfolio total_value>`
   The script owns the `hwm`, `halt`, and `halt_reason` state update. If the
   script fails, emits invalid JSON, or omits required keys, journal the error,
   notify, stop, and place no orders. If its JSON output has `halt: true`,
   journal `halted: <halt_reason>`, notify with sound, stop, and place no
   orders.

4. **Position check.** Call `get_equity_positions`. holding = true iff there is
   a SPY position with quantity > 0.

5. **Compute the signal and persist the quote.** Run the deterministic quote
   wrapper with the raw SPY quote payload from step 2:
   `uv run scripts/decide_with_quote.py --quote-json '<raw SPY quote JSON>' --holding <true|false>`
   (If raw JSON cannot be passed safely, use `--price <SPY last trade price>
   --quote-ts <SPY last-trade timestamp>`.) The wrapper writes
   `state.last_quote = {symbol, price, ts}` and uses that same price for the
   decision. The order gate later validates any order against that persisted
   quote (freshness + price tolerance). The JSON `decision` is one of
   BUY / SELL / HOLD / NONE and is final.

6. **Execute the decision.**
   - **BUY** (only if not holding): size = `min(position_fraction × buying_power,
     max_order_usd)`, rounded down to 2 decimals. Call `review_equity_order`
     (market, dollar_amount, regular_hours, gfd). If review shows blocking
     alerts, do not place; proceed to step 7 reconciliation. Otherwise call
     `place_equity_order` with the same parameters and a fresh UUID `ref_id`.
   - **SELL** (only if holding): sell the full `shares_available_for_sells` as a
     market order (regular_hours, gfd), review first with a fresh UUID `ref_id`.
     If review shows blocking alerts, do not place; proceed to step 7
     reconciliation.
   - **HOLD / NONE**: no order.
   - If `place_equity_order` is blocked by the order gate hook, that is final —
     do NOT retry with altered parameters to get around it. In dry-run mode the
     gate blocks all placements by design: journal the order you *would* have
     placed, prefixed `DRY-RUN:`.

7. **Broker-sourced state reconciliation.** Always run reconciliation after
   the decision/order-attempt path in step 6, including HOLD/NONE, review-block,
   dry-run block, and order-gate block outcomes. Wait ~10s only if a placement
   reached the broker; otherwise do not wait. Then call
   `get_equity_orders` (today, symbol SPY) and pass the raw JSON — not a prose
   summary — to:
   `uv run scripts/reconcile_state.py --kind equity --date <YYYY-MM-DD ET> --decision <BUY|SELL|HOLD|NONE> --orders-json '<raw get_equity_orders JSON>'`
   (stdin is also okay instead of `--orders-json`). This script is the
   canonical writer for `last_run`, `last_action`, and `position_opened`: it
   sets `order_placed=false` when no broker order matches the same-day gate
   marker ref_id, including no-order paths. For option order flows, use
   `get_option_orders` and `--kind option --action <action>` the same way after
   every option decision/attempt (wait ~10s only if placement reached the
   broker) to update `last_option_action`.

8. **Paper fleet update.** Get quotes for SPY, QQQ, and IWM in one
   `get_equity_quotes` call, then run:
   `uv run scripts/run_strategies.py --quotes '{"SPY": <px>, "QQQ": <px>, "IWM": <px>}'`
   This updates every paper strategy book in `state/paper.json` and appends to
   `logs/paper.md` on its own — do not edit those files yourself. It places no
   real orders. If it errors, journal the error text and continue; the paper
   fleet must never block the live run. Include each strategy's `action` and
   `value` from its JSON output in the journal entry (one line per strategy).

9. **Allocator verdict.** Run:
   `uv run scripts/allocate.py --pick --record`
   This Thompson-samples today's champion across the paper books and appends
   the verdict to untracked `state/allocator.json` (a same-day re-run is a
   no-op — do not add `--force`). Include its one-line verdict in the journal
   entry (step 10), format: "champion today: <name> (score <decayed sharpe>,
   weight <w>)". The verdict is recommend-only: it changes no config and is
   never read by the order gates. If it errors, journal the error text and
   continue; the allocator must never block the live run.

10. **Journal.** Append one entry to `logs/journal.md`:
    date/time ET, portfolio value, signal JSON, action taken (or DRY-RUN/blocked
    reason), order id + fill state if any. When describing a dollar-sized order,
    always write it as notional with the share estimate and quote, e.g.
    "BUY $510.43 notional of SPY (~0.70 sh at $727.08), market order" — never
    "BUY SPY market $510.43", which reads like a limit price.

11. **Do not manually update state.** `state/state.json` was already written by
    the deterministic reconciler in step 7. Never overwrite
    `last_action`/`last_option_action` from journal prose, memory, or believed
    session state.

12. **Notify.** `osascript -e 'display notification "<decision + value>" with title "Agentic Trader"'`.

Hard rules: trade only the symbol and account in the merged config; at most one
order per run; never use margin features; never place an order the review step
flagged; if any MCP call errors twice in a row, journal the error, notify, and
stop without trading.
