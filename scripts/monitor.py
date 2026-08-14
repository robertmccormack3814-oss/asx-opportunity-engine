import json, os, smtplib, requests, pandas as pd, yfinance as yf
from datetime import date
from email.message import EmailMessage
from common import DATA, CONFIG, load_json, save_json, now_iso
from indicators import compute
from engine import regime, route, micro
from risk import screen, plan
from scan import hist, exit_check
from performance import sync_signal_ledger, performance_stats

NEAR_SIGNAL_FLOOR = 60


def email(sub, p):
    if not CONFIG["alerts"]["enable_email"]:
        return
    u = os.getenv("SMTP_USERNAME", "").strip(); pw = os.getenv("SMTP_APP_PASSWORD", "").strip(); to = CONFIG["alerts"]["email_to"].strip()
    if not u or not pw:
        return
    m = EmailMessage(); m["From"] = u; m["To"] = to; m["Subject"] = sub; m.set_content(json.dumps(p, indent=2))
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(); s.login(u, pw); s.send_message(m)


def emit(kind, p):
    email(f"ASX {kind}: {p.get('symbol')} {p.get('strategy','')}", p)
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if CONFIG["alerts"]["enable_webhook"] and url:
        try:
            requests.post(url, json={"type": kind.lower(), "payload": p}, timeout=20)
        except Exception as e:
            print("Webhook failed:", e)


def provisional_score(x, rg):
    r = x.iloc[-1]; rel = float(r.RELVOL20) if pd.notna(r.RELVOL20) else 0; score = 0
    if rg == "TREND":
        if float(r.Close) > float(r.HIGH20_PREV): score += 30
        if float(r.Close) > float(r.EMA20) > float(r.EMA50) > float(r.EMA200): score += 20
        if float(r.ADX14) >= CONFIG["trend"]["adx_min"]: score += 20
        if rel >= CONFIG["trend"]["relative_volume_min"]: score += 20
        if float(r.CLV) >= .65: score += 10
    elif rg == "RANGE":
        if float(r.ZS20) <= CONFIG["mean_reversion"]["zscore_entry"]: score += 35
        if float(r.RSI5) <= CONFIG["mean_reversion"]["rsi5_max"]: score += 25
        if float(r.ADX14) <= CONFIG["mean_reversion"]["adx_max"]: score += 20
        if float(r.CLV) >= .3: score += 10
        if float(r.SVP10) > -.75: score += 10
    elif rg == "SQUEEZE":
        from indicators import pct_last
        bb = pct_last(x.BBWIDTH, 120)
        if pd.notna(bb) and bb <= CONFIG["squeeze"]["bb_width_percentile_max"]: score += 35
        if rel >= CONFIG["squeeze"]["relative_volume_breakout_min"]: score += 25
        if float(r.Close) > float(r.HIGH20_PREV): score += 25
        if float(r.CLV) >= .7: score += 15
    return score


def main():
    universe = load_json(DATA / "universe.json", [])
    scanner = load_json(DATA / "scanner.json", {})
    state = load_json(DATA / "state.json", {"active_trades": {}, "closed_trades": []})
    active = state.get("active_trades", {}); closed = state.get("closed_trades", [])
    by_symbol = {x["symbol"]: x for x in universe if x.get("symbol")}
    scanner_assets = {x.get("symbol"): x for x in scanner.get("assets", []) if x.get("symbol")}

    watch = set(active)
    for a in scanner_assets.values():
        if a.get("status") != "ok" or a.get("active"):
            continue
        score = a.get("near_signal_score")
        if score is None:
            score = (a.get("candidate") or {}).get("signal_score")
        if score is not None and float(score) >= NEAR_SIGNAL_FLOOR:
            watch.add(a.get("symbol"))
    watch.discard(None)

    if not watch:
        ledger = sync_signal_ledger(active, closed); performance = performance_stats(ledger)
        scanner["performance"] = performance; scanner["signal_ledger"] = ledger
        scanner.setdefault("stats", {})["total_signals_recorded"] = performance["total_signals"]
        scanner["stats"]["completed_trades"] = performance["completed_signals"]
        scanner["stats"]["successful_trades"] = performance["wins"]
        scanner["stats"]["signal_success_rate_pct"] = performance["success_rate_pct"]
        save_json(DATA / "scanner.json", scanner)
        save_json(DATA / "monitor.json", {"generated_at": now_iso(), "watched": 0, "message": "No active or near-signal securities yet"})
        print("Fast monitor: nothing to watch yet"); return

    tickers = [by_symbol[s]["ticker"] for s in sorted(watch) if s in by_symbol]
    d = yf.download(tickers, period=CONFIG["history_period"], interval="1d", group_by="ticker", auto_adjust=False, threads=True, progress=False)
    multi = len(tickers) > 1
    black = str(date.today()) in set(load_json(DATA / "macro_blackouts.json", {"dates": []}).get("dates", []))
    exits = []; signals = []; rows = []

    for sym in sorted(watch):
        item = by_symbol.get(sym)
        if not item:
            continue
        try:
            x0 = hist(d, item["ticker"], multi)
            if len(x0) < CONFIG["required_history_days"]:
                continue
            x = compute(x0).dropna(subset=["ATR14", "ADX14", "EMA200", "ZS20", "BBWIDTH"])
            if len(x) < 5:
                continue
            r = x.iloc[-1]; rg, meta = regime(x, CONFIG); ms = micro(r); cand = None

            if sym in active:
                why, px = exit_check(active[sym], x)
                if why:
                    tr = active.pop(sym)
                    payload = {**tr, "exit_date": str(x.index[-1].date()), "exit_price": px, "exit_reason": why, "pnl_pct": ((px / tr["entry_price"]) - 1) * 100}
                    exits.append(payload); closed.append(payload); emit("EXIT", payload)

            sig = route(x, rg, CONFIG)
            if sig and sym not in active:
                sig["microstructure_score"] = ms; sig["score"] = min(100, sig["score"] + max(-10, (ms - 50) * .2))
                if sig["score"] >= CONFIG["minimum_signal_score"]:
                    sc = screen(item["ticker"], r, CONFIG, black); pl = plan(sig, r, CONFIG)
                    payload = {"symbol": sym, "company": item["company"], "direction": sig["direction"], "strategy": sig["strategy"], "regime": rg, "signal_score": round(sig["score"], 1), "microstructure_score": round(ms, 1), "reasons": sig["reasons"], "regime_details": meta, "execution_risk": sc, **pl, "signal_date": str(x.index[-1].date()), "timestamp": now_iso()}
                    if sc["pass"]:
                        active[sym] = {**payload, "entry_date": payload["signal_date"], "peak_price": payload["entry_price"], "trailing_stop": payload["stop_loss"]}
                        signals.append(payload); emit("ENTRY", payload); cand = payload
                    else:
                        cand = {**payload, "rejected": True}

            raw = provisional_score(x, rg); adjusted = min(100, raw + max(-10, (ms - 50) * .2)); stamp = now_iso()
            rows.append({"symbol": sym, "company": item["company"], "regime": rg, "price": float(r.Close), "near_signal_score": round(adjusted, 1), "active": sym in active, "updated_at": stamp})
            if sym in active:
                cand = active[sym]
            old = scanner_assets.get(sym, {})
            scanner_assets[sym] = {**old, "symbol": sym, "company": item["company"], "status": "ok", "regime": rg, "price": float(r.Close), "adx14": float(r.ADX14), "atr14": float(r.ATR14), "relative_volume": float(r.RELVOL20) if pd.notna(r.RELVOL20) else None, "zscore20": float(r.ZS20), "microstructure_score": round(ms, 1), "near_signal_score": round(adjusted, 1), "active": sym in active, "candidate": cand, "updated_at": stamp}
        except Exception as e:
            print(f"Monitor {sym}: {type(e).__name__}: {e}")

    closed = closed[-500:]
    state["active_trades"] = active; state["closed_trades"] = closed; state["updated_at"] = now_iso(); save_json(DATA / "state.json", state)
    save_json(DATA / "monitor.json", {"generated_at": now_iso(), "watched": len(rows), "signals": signals, "exits": exits, "assets": rows})

    ledger = sync_signal_ledger(active, closed); performance = performance_stats(ledger)
    stats = dict(scanner.get("stats", {}))
    stats["active_trades"] = len(active); stats["signals_this_batch"] = len(signals); stats["exits_this_batch"] = len(exits); stats["fast_monitor_watched"] = len(rows)
    stats["near_signals"] = sum(1 for a in scanner_assets.values() if a.get("near_signal_score", 0) >= NEAR_SIGNAL_FLOOR and not a.get("active"))
    stats["total_signals_recorded"] = performance["total_signals"]; stats["completed_trades"] = performance["completed_signals"]; stats["successful_trades"] = performance["wins"]; stats["signal_success_rate_pct"] = performance["success_rate_pct"]
    scanner["generated_at"] = now_iso(); scanner["stats"] = stats; scanner["performance"] = performance; scanner["signals"] = signals; scanner["exits"] = exits
    scanner["active_trades"] = sorted(active.values(), key=lambda x: x["symbol"]); scanner["completed_trades"] = [x for x in ledger if x.get("status") == "COMPLETED"]; scanner["signal_ledger"] = ledger
    scanner["assets"] = sorted(scanner_assets.values(), key=lambda x: (0 if x.get("active") else 1, x.get("symbol", "")))
    save_json(DATA / "scanner.json", scanner)
    print({"watched": len(rows), "signals": len(signals), "exits": len(exits), "active": len(active), "total_signals": performance["total_signals"], "completed": performance["completed_signals"], "success_rate_pct": performance["success_rate_pct"]})


if __name__ == "__main__":
    main()
