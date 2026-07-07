# Trading journal

## 2026-06-09 22:20 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: SPY traded today, official close $737.05
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-09", "symbol": "SPY", "price": 737.05, "rsi2": 14.9, "sma_trend": 682.63, "sma_exit": 745.03, "holding": false, "decision": "NONE", "reason": "close 737.05 vs SMA200 682.63, RSI2 14.9 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 14.9 not < 10.0 entry)
- Order: none

## 2026-06-10 15:46 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $727.08 at 15:45 ET (prev close $737.05)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-10", "symbol": "SPY", "price": 727.08, "rsi2": 4.52, "sma_trend": 683.07, "sma_exit": 739.6, "holding": false, "decision": "BUY", "reason": "close 727.08 > SMA200 683.07 and RSI2 4.5 < 10.0"}
- Review: passed — alert EQUITY_SUITABILITY (brokerageAccountType: INDIVIDUAL), informational, non-blocking
- Action: DRY-RUN: would have placed BUY $510.43 notional of SPY (~0.70 sh at $727.08, market order), sized as min(0.95 × $537.30, $550), regular_hours, gfd, ref_id 9b7babf7-9f94-4215-af5f-5fee13f9adf8 — blocked by order gate hook (dry_run=true)
- Order: none placed

## 2026-06-11 15:46 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $738.97 at 15:45 ET (prev close $725.43)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-11", "symbol": "SPY", "price": 738.97, "rsi2": 64.42, "sma_trend": 683.57, "sma_exit": 735.64, "holding": false, "decision": "NONE", "reason": "close 738.97 vs SMA200 683.57, RSI2 64.4 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 64.4 not < 10.0 entry)
- Order: none

## 2026-06-12 15:46 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $740.63 at 15:45 ET (prev close $737.76)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-12", "symbol": "SPY", "price": 740.63, "rsi2": 70.61, "sma_trend": 684.07, "sma_exit": 736.02, "holding": false, "decision": "NONE", "reason": "close 740.63 vs SMA200 684.07, RSI2 70.6 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 70.6 not < 10.0 entry)
- Order: none
- Paper fleet: run_strategies.py skipped — already ran today (2026-06-12, earlier session). Today's recorded results from logs/paper.md:
  - rsi2_spy: NONE, $10,000.00
  - ibs_qqq: NONE, $10,000.00
  - bollinger_spy: NONE, $10,000.00
  - donchian_qqq: NONE, $10,000.00
  - momentum_rotation: BUY, $9,998.10 (bought 13.1483 QQQ @ 722.52)
  - opt_rsi2_call_qqq: NONE, $10,000.00
  - opt_breakout_call_spy: NONE, $10,000.00
  - opt_rsi2_put_spy: NONE, $10,000.00
  - opt_ibs_call_iwm: NONE, $10,000.00
  - opt_breakdown_put_qqq: NONE, $10,000.00

## 2026-06-15 15:47 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $754.48 at 15:45 ET (prev close $741.75)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-15", "symbol": "SPY", "price": 754.48, "rsi2": 90.33, "sma_trend": 684.64, "sma_exit": 739.29, "holding": false, "decision": "NONE", "reason": "close 754.48 vs SMA200 684.64, RSI2 90.3 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 90.3 not < 10.0 entry)
- Order: none
- Reconcile: order_placed=false, status not_found (no same-day gate marker; no broker SPY order today)
- Paper fleet (run_strategies.py):
  - rsi2_spy: NONE, $10,000.00
  - ibs_qqq: NONE, $10,000.00
  - bollinger_spy: NONE, $10,000.00
  - donchian_qqq: NONE, $10,000.00
  - momentum_rotation: HOLD, $10,274.08
  - opt_rsi2_call_qqq: NONE, $10,000.00
  - opt_breakout_call_spy: NONE, $10,000.00
  - opt_rsi2_put_spy: NONE, $10,000.00
  - opt_ibs_call_iwm: OPEN, $9,998.70 (bought 2x IWM 2026-07-10 280C @ 17.49)
  - opt_breakdown_put_qqq: NONE, $10,000.00
  - opt_rsi2_call_xlf: NONE, $10,000.00
- Allocator: champion today: rsi2_spy (score n/a, weight 0.1667)

## 2026-06-16 15:47 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $750.95 at 15:45 ET (prev close $754.83)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-16", "symbol": "SPY", "price": 750.95, "rsi2": 65.34, "sma_trend": 685.18, "sma_exit": 742.14, "holding": false, "decision": "NONE", "reason": "close 750.95 vs SMA200 685.18, RSI2 65.3 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 65.3 not < 10.0 entry)
- Order: none
- Reconcile: order_placed=false, status not_found (no same-day gate marker; no broker SPY order today)
- Paper fleet (run_strategies.py):
  - rsi2_spy: NONE, $10,000.00
  - ibs_qqq: BUY, $9,998.10 (bought 12.9718 QQQ @ 732.36)
  - bollinger_spy: NONE, $10,000.00
  - donchian_qqq: NONE, $10,000.00
  - momentum_rotation: HOLD, $10,127.35
  - opt_rsi2_call_qqq: NONE, $10,000.00
  - opt_breakout_call_spy: NONE, $10,000.00
  - opt_rsi2_put_spy: NONE, $10,000.00
  - opt_ibs_call_iwm: CLOSE, $9,584.15 (sold 2x IWM 280C @ 15.42, exit signal)
  - opt_breakdown_put_qqq: NONE, $10,000.00
  - opt_rsi2_call_xlf: NONE, $10,000.00
- Allocator: champion today: opt_rsi2_put_spy (score n/a, weight 0.1667)

## 2026-06-17 15:47 ET
- Portfolio value: $537.30 (cash $537.30, buying power $537.30); HWM $537.30 (unchanged)
- Market: open — SPY last trade $740.51 at 15:45 ET (prev close $750.33)
- Holding: false (no SPY position)
- Signal: {"date": "2026-06-17", "symbol": "SPY", "price": 740.51, "rsi2": 26.65, "sma_trend": 685.68, "sma_exit": 745.04, "holding": false, "decision": "NONE", "reason": "close 740.51 vs SMA200 685.68, RSI2 26.7 (need < 10.0 and above trend)"}
- Action: NONE — no order (above trend but RSI2 26.7 not < 10.0 entry)
- Order: none
- Reconcile: order_placed=false, status not_found (no same-day gate marker; no broker SPY order today)
- Paper fleet (run_strategies.py):
  - rsi2_spy: NONE, $10,000.00
  - ibs_qqq: HOLD, $9,876.97
  - bollinger_spy: NONE, $10,000.00
  - donchian_qqq: NONE, $10,000.00
  - momentum_rotation: HOLD, $10,004.57
  - opt_rsi2_call_qqq: NONE, $10,000.00
  - opt_breakout_call_spy: NONE, $10,000.00
  - opt_rsi2_put_spy: NONE, $10,000.00
  - opt_ibs_call_iwm: OPEN, $9,583.50 (bought 1x IWM 2026-07-10 275C @ 19.60)
  - opt_breakdown_put_qqq: NONE, $10,000.00
  - opt_rsi2_call_xlf: NONE, $10,000.00
- Allocator: champion today: rsi2_spy (score n/a, weight 0.1667)
