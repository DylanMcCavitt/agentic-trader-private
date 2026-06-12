> Disclaimer: this file is part of educational/reference software and is not financial or investment advice.

# Daily trading run — RSI(2) mean reversion on SPY

You are executing one scheduled trading check in the Robinhood **Agentic** account.
Follow these steps exactly. Do not improvise trades, symbols, or sizing. The
strategy decision comes from `scripts/decide.py` only — never from your own
market opinion. All numbered steps are mandatory.

1. **Load context.** Run `python3 scripts/load_config.py` to get the effective
   config (`config.json` deep-merged with untracked `config.local.json`, which
   holds the real `account_number`). Use that merged config everywhere below.
   If `account_number` is missing or `REPLACE_ME`, stop — the order gate will
   block everything anyway. Read `state/state.json`. If `halt` is true: log
   "halted: <reason>" to the journal (step 10), notify (step 12), stop.

2. **Confirm the market is open today.** Get a SPY quote via the Robinhood MCP
   (`get_equity_quotes`). If the quote's last-trade timestamp is not from today
   (market holiday), journal "market closed", notify, stop.

3. **Portfolio + kill switch.** Call `get_portfolio` for the account in the
   merged config. If `total_value` > state `hwm`, update `hwm` in the state
   file. If `total_value` < `hwm × (1 − kill_drawdown_pct/100)`: set
   `halt: true` with `halt_reason` describing the drawdown, journal it, notify
   with sound, stop. Place no orders.

4. **Position check.** Call `get_equity_positions`. holding = true iff there is
   a SPY position with quantity > 0.

5. **Compute the signal.**
   `uv run scripts/decide.py --price <SPY last trade price> --holding <true|false>`
   The JSON `decision` is one of BUY / SELL / HOLD / NONE and is final.

6. **Execute the decision.**
   - **BUY** (only if not holding): size = `min(position_fraction × buying_power,
     max_order_usd)`, rounded down to 2 decimals. Call `review_equity_order`
     (market, dollar_amount, regular_hours, gfd). If review shows blocking
     alerts, journal them and stop. Otherwise call `place_equity_order` with the
     same parameters and a fresh UUID `ref_id`.
   - **SELL** (only if holding): sell the full `shares_available_for_sells` as a
     market order (regular_hours, gfd), review first, fresh UUID `ref_id`.
   - **HOLD / NONE**: no order.
   - If `place_equity_order` is blocked by the order gate hook, that is final —
     do NOT retry with altered parameters to get around it. In dry-run mode the
     gate blocks all placements by design: journal the order you *would* have
     placed, prefixed `DRY-RUN:`.

7. **Verify fills.** If an order was placed, wait ~10s, then `get_equity_orders`
   (filter to today, symbol SPY) and record the fill state.

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

11. **Update state.** Write `state/state.json`: `last_run` (ISO timestamp),
    `last_action` `{date, decision, order_placed: bool, order_id}`, and
    `position_opened` (set to today's date on a fill of a buy; null after a
    sell fills; otherwise leave unchanged).

12. **Notify.** `osascript -e 'display notification "<decision + value>" with title "Agentic Trader"'`.

Hard rules: trade only the symbol and account in the merged config; at most one
order per run; never use margin features; never place an order the review step
flagged; if any MCP call errors twice in a row, journal the error, notify, and
stop without trading.
