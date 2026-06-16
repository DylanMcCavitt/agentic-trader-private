> Disclaimer: this file is part of educational/reference software and is not financial or investment advice.

# Daily trading run — RSI(2) mean reversion on SPY + option sleeve

You are executing one scheduled trading check in the Robinhood **Agentic** account.
Follow these steps exactly. Do not improvise trades, symbols, or sizing. Every
strategy decision comes from `scripts/decide_with_quote.py` / `scripts/decide.py`
(equity sleeve) or `scripts/decide_option.py` (option sleeve) only — never from
your own market opinion. All numbered steps are mandatory.

The run has two independent sleeves against the same account: the **equity
sleeve** (steps 1–7, RSI(2) on SPY) and the **option sleeve** (steps 8–9, a
single long call/put chosen from the best paper option strategy). Both are
dry-run while `dry_run: true`; the order gates block every live placement by
design. The option sleeve must never block the equity sleeve.

1. **Load context.** Run `python3 scripts/load_config.py` to get the effective
   config (`config.json` deep-merged with untracked `config.local.json`, which
   holds the real `account_number`). Use that merged config everywhere below.
   If `account_number` is missing or `REPLACE_ME`, stop — the order gate will
   block everything anyway. Read `state/state.json`. If `halt` is true: log
   "halted: <reason>" to the journal (step 12), notify (step 14), stop.

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

6. **Execute the equity decision.**
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

7. **Broker-sourced state reconciliation (equity).** Always run reconciliation
   after the decision/order-attempt path in step 6, including HOLD/NONE,
   review-block, dry-run block, and order-gate block outcomes. Wait ~10s only if
   a placement reached the broker; otherwise do not wait. Then call
   `get_equity_orders` (today, symbol SPY) and pass the raw JSON — not a prose
   summary — to:
   `uv run scripts/reconcile_state.py --kind equity --date <YYYY-MM-DD ET> --decision <BUY|SELL|HOLD|NONE> --orders-json '<raw get_equity_orders JSON>'`
   (stdin is also okay instead of `--orders-json`). This script is the
   canonical writer for `last_run`, `last_action`, and `position_opened`: it
   sets `order_placed=false` when no broker order matches the same-day gate
   marker ref_id, including no-order paths.

8. **Option sleeve — select and decide.** This whole sleeve is best-effort: if
   any MCP call or script here errors twice in a row, journal the error and skip
   to step 10 (the option sleeve must never block the equity path or the run).
   - **Holding check.** Call `get_option_positions` for the merged-config
     account. The sleeve is *holding* iff there is an open long option position
     (quantity > 0) whose underlying + right matches one of the
     `option_sleeve.candidates` strategies. There is at most one such position
     (one option order per day). In dry-run mode no position is ever opened, so
     this is normally false.
   - **If flat:** run `uv run scripts/select_sleeve.py`. It returns today's
     option strategy — the best-scoring candidate once one has ≥
     `option_sleeve.min_score_days` paper returns, else `option_sleeve.default`.
     It always returns a strategy; it never blocks on paper history. Get the
     chosen `symbol`'s quote with `get_equity_quotes`, then:
     `uv run scripts/decide_option.py --strategy <name> --holding false --price <underlying last trade> --quote-ts <ts>`
   - **If holding:** the managing strategy is the candidate whose (underlying,
     right) matches the open position. Run:
     `uv run scripts/decide_option.py --strategy <name> --holding true --price <underlying last trade> --quote-ts <ts> --expiry <position expiry YYYY-MM-DD>`
   - The JSON `decision` is one of OPEN / CLOSE / HOLD / NONE and is final.

9. **Option sleeve — execute and reconcile.**
   - **OPEN** (flat + entry): fetch the chosen underlying's chain from the
     broker — `get_option_chains`, then `get_option_instruments` (tradable
     `<right>`s) and `get_option_quotes` for those instruments — and pass the
     raw JSON to:
     `uv run scripts/select_option_contract.py --right <call|put> --spot <underlying px> --dte-min <params.dte_min> --dte-max <params.dte_max> --max-premium <max_option_premium_usd> --contracts 1 --chains-json '<raw chain+quote JSON>'`
     If `within_budget` is false, place no order — journal the `reason` (e.g.
     "cheapest contract $X exceeds $300 budget"). Otherwise call
     `review_option_order`, then `place_option_order` for the returned
     `option_id`: one leg, side `buy` / position_effect `open`, `type` `limit`,
     `price` = the returned `limit_price`, `quantity` 1, regular hours, gfd, a
     fresh UUID `ref_id`, and the merged-config `account_number`. The premium
     must not exceed available buying power (shared with the equity sleeve).
   - **CLOSE** (holding + exit/DTE): `review_option_order`, then
     `place_option_order` to sell the open contract — one leg, side `sell` /
     position_effect `close`, `type` `limit` at the contract's current
     mark/bid, `quantity` = the held contracts, a fresh UUID `ref_id`.
   - **HOLD / NONE:** no order.
   - If `place_option_order` is blocked by the option gate hook, that is final —
     do NOT retry with altered parameters. In dry-run mode the gate blocks every
     placement by design: journal the option order you *would* have placed,
     prefixed `DRY-RUN:`.
   - **Reconcile (always**, including HOLD/NONE, no-contract, review-block,
     dry-run block): wait ~10s only if a placement reached the broker, then call
     `get_option_orders` (today) and pass the raw JSON to:
     `uv run scripts/reconcile_state.py --kind option --date <YYYY-MM-DD ET> --decision <OPEN|CLOSE|HOLD|NONE> --action <strategy name> --orders-json '<raw get_option_orders JSON>'`
     This is the canonical writer for `last_option_action`.

10. **Paper fleet update.** Get quotes for every distinct underlying in the
    enabled strategies (at least SPY, QQQ, IWM, XLF, AAPL, MSFT, NVDA, GOOGL,
    AMZN) in one `get_equity_quotes` call, then run:
    `uv run scripts/run_strategies.py --quotes '{"SPY": <px>, "QQQ": <px>, ...}'`
    This updates every paper strategy book in `state/paper.json` and appends to
    `logs/paper.md` on its own — do not edit those files yourself. It places no
    real orders. If it errors, journal the error text and continue; the paper
    fleet must never block the live run. Include each strategy's `action` and
    `value` from its JSON output in the journal entry (one line per strategy).

11. **Allocator verdict.** Run:
    `uv run scripts/allocate.py --pick --record`
    This Thompson-samples today's champion across the paper books and appends
    the verdict to untracked `state/allocator.json` (a same-day re-run is a
    no-op — do not add `--force`). Include its one-line verdict in the journal
    entry (step 12), format: "champion today: <name> (score <decayed sharpe>,
    weight <w>)". The verdict is recommend-only: it changes no config and is
    never read by the order gates. If it errors, journal the error text and
    continue; the allocator must never block the live run.

12. **Journal.** Append one entry to `logs/journal.md`:
    date/time ET, portfolio value, equity signal JSON, equity action taken (or
    DRY-RUN/blocked reason), order id + fill state if any. Then add the option
    sleeve lines: selected strategy + basis, option decision, the contract you
    would have / did place (or DRY-RUN/blocked/no-contract reason), and its
    order id + fill state if any. When describing a dollar-sized equity order,
    always write it as notional with the share estimate and quote, e.g.
    "BUY $510.43 notional of SPY (~0.70 sh at $727.08), market order" — never
    "BUY SPY market $510.43", which reads like a limit price.

13. **Do not manually update state.** `state/state.json` was already written by
    the deterministic reconcilers in steps 7 and 9. Never overwrite
    `last_action` / `last_option_action` from journal prose, memory, or believed
    session state.

14. **Notify.** `osascript -e 'display notification "<equity decision + option decision + value>" with title "Agentic Trader"'`.

Hard rules: trade only the symbols and account in the merged config; at most one
equity order and one option order per run; never use margin features; never
place an order the review step flagged; if any MCP call errors twice in a row on
the equity path, journal the error, notify, and stop without trading (the option
sleeve instead skips itself per step 8).
