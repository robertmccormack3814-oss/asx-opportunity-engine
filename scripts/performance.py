from datetime import datetime

from common import DATA, load_json, save_json, now_iso

LEDGER_PATH = DATA / "signal_ledger.json"


def signal_id(trade):
    symbol = str(trade.get("symbol") or "").upper()
    strategy = str(trade.get("strategy") or "UNKNOWN").upper()
    signal_date = str(trade.get("signal_date") or trade.get("entry_date") or "")
    try:
        entry = f"{float(trade.get('entry_price') or 0):.6f}"
    except Exception:
        entry = "0.000000"
    return f"{symbol}|{signal_date}|{strategy}|{entry}"


def trading_days_between(start_date, end_date):
    try:
        a = datetime.strptime(str(start_date), "%Y-%m-%d").date()
        b = datetime.strptime(str(end_date), "%Y-%m-%d").date()
    except Exception:
        return None
    if b <= a:
        return 0
    days = 0
    cur = a
    while cur < b:
        cur = cur.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5:
            days += 1
    return days


def _normalise(trade, status):
    row = dict(trade)
    row["signal_id"] = signal_id(row)
    row["status"] = status
    row["strategy"] = str(row.get("strategy") or "UNKNOWN").upper()
    row["signal_date"] = row.get("signal_date") or row.get("entry_date")
    row["entry_date"] = row.get("entry_date") or row.get("signal_date")
    row["last_ledger_update"] = now_iso()

    if status == "COMPLETED":
        pnl = row.get("pnl_pct")
        if pnl is None and row.get("entry_price") and row.get("exit_price"):
            try:
                pnl = ((float(row["exit_price"]) / float(row["entry_price"])) - 1.0) * 100.0
            except Exception:
                pnl = None
        row["pnl_pct"] = pnl
        if pnl is None:
            row["outcome"] = "UNKNOWN"
        elif float(pnl) > 0:
            row["outcome"] = "WIN"
        elif float(pnl) < 0:
            row["outcome"] = "LOSS"
        else:
            row["outcome"] = "BREAKEVEN"
        if row.get("holding_trading_days") is None:
            row["holding_trading_days"] = trading_days_between(row.get("entry_date"), row.get("exit_date"))
    else:
        row["outcome"] = "PENDING"
    return row


def sync_signal_ledger(active, closed):
    existing = load_json(LEDGER_PATH, {"signals": []})
    rows = existing.get("signals", []) if isinstance(existing, dict) else []
    by_id = {str(r.get("signal_id") or signal_id(r)): dict(r) for r in rows if isinstance(r, dict)}

    # Recover every signal still preserved in state. Closed trades take precedence
    # over active records if the same identity is encountered.
    for trade in active.values():
        row = _normalise(trade, "ACTIVE")
        old = by_id.get(row["signal_id"], {})
        by_id[row["signal_id"]] = {**old, **row}

    for trade in closed:
        row = _normalise(trade, "COMPLETED")
        old = by_id.get(row["signal_id"], {})
        by_id[row["signal_id"]] = {**old, **row}

    ledger = sorted(
        by_id.values(),
        key=lambda r: (str(r.get("signal_date") or ""), str(r.get("timestamp") or ""), str(r.get("symbol") or "")),
    )
    save_json(LEDGER_PATH, {"generated_at": now_iso(), "signals": ledger})
    return ledger


def performance_stats(ledger):
    completed = [r for r in ledger if r.get("status") == "COMPLETED"]
    active = [r for r in ledger if r.get("status") == "ACTIVE"]
    wins = [r for r in completed if r.get("outcome") == "WIN"]
    losses = [r for r in completed if r.get("outcome") == "LOSS"]
    returns = [float(r.get("pnl_pct") or 0.0) for r in completed if r.get("pnl_pct") is not None]

    by_strategy = {}
    for strategy in sorted({str(r.get("strategy") or "UNKNOWN") for r in ledger}):
        s_all = [r for r in ledger if str(r.get("strategy") or "UNKNOWN") == strategy]
        s_done = [r for r in s_all if r.get("status") == "COMPLETED"]
        s_wins = [r for r in s_done if r.get("outcome") == "WIN"]
        vals = [float(r.get("pnl_pct") or 0.0) for r in s_done if r.get("pnl_pct") is not None]
        by_strategy[strategy] = {
            "signals": len(s_all),
            "active": sum(1 for r in s_all if r.get("status") == "ACTIVE"),
            "completed": len(s_done),
            "wins": len(s_wins),
            "losses": sum(1 for r in s_done if r.get("outcome") == "LOSS"),
            "success_rate_pct": (len(s_wins) / len(s_done) * 100.0) if s_done else None,
            "average_return_pct": (sum(vals) / len(vals)) if vals else None,
            "cumulative_return_pct": sum(vals) if vals else 0.0,
        }

    return {
        "total_signals": len(ledger),
        "active_signals": len(active),
        "completed_signals": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "success_rate_pct": (len(wins) / len(completed) * 100.0) if completed else None,
        "average_return_pct": (sum(returns) / len(returns)) if returns else None,
        "cumulative_return_pct": sum(returns) if returns else 0.0,
        "by_strategy": by_strategy,
    }
