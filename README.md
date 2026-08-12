# ASX Regime-Aware Opportunity Engine

A systematic ASX scanner that classifies market regime and routes securities to trend/breakout, mean-reversion, or squeeze/expansion logic. It combines price structure, volume/price behaviour, statistical deviation, execution-risk screening, and ATR-based trade planning.

## Monitoring architecture

The engine now has two layers:

- **Broad universe scanner** — rotates through the ASX universe in batches to discover new opportunities.
- **Fast opportunity monitor** — checks ACTIVE trades and securities with a provisional setup score of 60+ every 30 minutes during the core ASX session.

The 60-point fast-monitor threshold is only a watchlist threshold. It does **not** lower the trading rules: an entry still needs to satisfy the original strategy conditions, the configured minimum signal score of 72, and the execution-risk screen.

The fast monitor uses the same stop-loss, profit-target, trend trailing-stop, time-exit and email/webhook alert logic as the broad scanner.

## Important

This is a research and decision-support system, not a guarantee of trading performance. Backtesting and paper trading should be used before automated live execution.
