import os,json,smtplib,requests,pandas as pd,yfinance as yf
from datetime import date
from email.message import EmailMessage
from common import DATA,CONFIG,load_json,save_json,now_iso
from indicators import compute,pct_last
from engine import regime,route,micro
from risk import screen,plan

def email(sub,p):
    if not CONFIG["alerts"]["enable_email"]: return
    u=os.getenv("SMTP_USERNAME","").strip();pw=os.getenv("SMTP_APP_PASSWORD","").strip();to=CONFIG["alerts"]["email_to"].strip()
    if not u or not pw:return
    m=EmailMessage();m["From"]=u;m["To"]=to;m["Subject"]=sub;m.set_content(json.dumps(p,indent=2))
    with smtplib.SMTP("smtp.gmail.com",587,timeout=30) as s:s.starttls();s.login(u,pw);s.send_message(m)

def emit(kind,p):
    email(f"ASX {kind}: {p.get('symbol')} {p.get('strategy','')}",p)
    url=os.getenv("ALERT_WEBHOOK_URL","").strip()
    if CONFIG["alerts"]["enable_webhook"] and url:
        try:requests.post(url,json={"type":kind.lower(),"payload":p},timeout=20)
        except Exception as e:print("Webhook failed:",e)

def hist(df,t,multi):
    try:
        x=df[t].copy() if multi else df.copy();needed=["Open","High","Low","Close","Volume"]
        if any(c not in x.columns for c in needed):return pd.DataFrame()
        for c in needed:x[c]=pd.to_numeric(x[c],errors="coerce")
        return x.dropna(subset=["Open","High","Low","Close"])
    except Exception:return pd.DataFrame()

def exit_check(tr,x):
    r=x.iloc[-1];low=float(r.Low);high=float(r.High);cl=float(r.Close);days=sum(x.index.date>pd.Timestamp(tr["entry_date"]).date())
    if low<=tr["stop_loss"]:return "STOP_LOSS",tr["stop_loss"]
    if high>=tr["profit_target"]:return "PROFIT_TARGET",tr["profit_target"]
    if tr["strategy"]=="TREND_BREAKOUT":
        atr=float(r.ATR14);peak=max(tr.get("peak_price",tr["entry_price"]),high);tr["peak_price"]=peak;trail=peak-CONFIG["exit"]["trend_trailing_atr"]*atr
        tr["trailing_stop"]=max(tr.get("trailing_stop",tr["stop_loss"]),trail)
        if low<=tr["trailing_stop"]:return "ATR_TRAILING_STOP",tr["trailing_stop"]
    if days>=CONFIG["exit"]["max_holding_days"]:return "TIME_EXIT",cl
    return None,None

def provisional_score(x,rg):
    r=x.iloc[-1];rel=float(r.RELVOL20) if pd.notna(r.RELVOL20) else 0;score=0
    if rg=="TREND":
        if float(r.Close)>float(r.HIGH20_PREV):score+=30
        if float(r.Close)>float(r.EMA20)>float(r.EMA50)>float(r.EMA200):score+=20
        if float(r.ADX14)>=CONFIG["trend"]["adx_min"]:score+=20
        if rel>=CONFIG["trend"]["relative_volume_min"]:score+=20
        if float(r.CLV)>=.65:score+=10
    elif rg=="RANGE":
        if float(r.ZS20)<=CONFIG["mean_reversion"]["zscore_entry"]:score+=35
        if float(r.RSI5)<=CONFIG["mean_reversion"]["rsi5_max"]:score+=25
        if float(r.ADX14)<=CONFIG["mean_reversion"]["adx_max"]:score+=20
        if float(r.CLV)>=.3:score+=10
        if float(r.SVP10)>-.75:score+=10
    elif rg=="SQUEEZE":
        bb=pct_last(x.BBWIDTH,120)
        if pd.notna(bb) and bb<=CONFIG["squeeze"]["bb_width_percentile_max"]:score+=35
        if rel>=CONFIG["squeeze"]["relative_volume_breakout_min"]:score+=25
        if float(r.Close)>float(r.HIGH20_PREV):score+=25
        if float(r.CLV)>=.7:score+=15
    return score

def main():
    u=load_json(DATA/"universe.json",[])
    if not u:raise RuntimeError("Universe is empty. Run build_universe.py first.")
    cur=load_json(DATA/"cursor.json",{"next_index":0});st=load_json(DATA/"state.json",{"active_trades":{},"closed_trades":[]});active=st.get("active_trades",{});closed=st.get("closed_trades",[])
    old=load_json(DATA/"scanner.json",{});assets={x.get("symbol"):x for x in old.get("assets",[]) if x.get("symbol")}
    black=str(date.today()) in set(load_json(DATA/"macro_blackouts.json",{"dates":[]}).get("dates",[]))
    start=int(cur.get("next_index",0))%len(u);items=[u[(start+i)%len(u)] for i in range(min(CONFIG["universe_batch_size"],len(u)))];ts=[x["ticker"] for x in items]
    d=yf.download(ts,period=CONFIG["history_period"],interval="1d",group_by="ticker",auto_adjust=False,threads=True,progress=False);multi=len(ts)>1
    signals=[];exits=[];errs=0;proc=0
    for i,item in enumerate(items,1):
        sym=item["symbol"];ticker=item["ticker"]
        try:
            x0=hist(d,ticker,multi)
            if len(x0)<CONFIG["required_history_days"]:assets[sym]={"symbol":sym,"company":item["company"],"status":"error","reason":f"Only {len(x0)} raw daily bars"};errs+=1;continue
            x=compute(x0).dropna(subset=["ATR14","ADX14","EMA200","ZS20","BBWIDTH"])
            if len(x)<5:assets[sym]={"symbol":sym,"company":item["company"],"status":"error","reason":f"Only {len(x)} usable indicator rows"};errs+=1;continue
            r=x.iloc[-1];proc+=1;rg,meta=regime(x,CONFIG);ms=micro(r)
            if sym in active:
                why,px=exit_check(active[sym],x)
                if why:
                    tr=active.pop(sym);payload={**tr,"exit_date":str(x.index[-1].date()),"exit_price":px,"exit_reason":why,"pnl_pct":((px/tr["entry_price"])-1)*100};exits.append(payload);closed.append(payload);emit("EXIT",payload)
            sig=route(x,rg,CONFIG);cand=None
            if sig and sym not in active:
                sig["microstructure_score"]=ms;sig["score"]=min(100,sig["score"]+max(-10,(ms-50)*0.2))
                if sig["score"]>=CONFIG["minimum_signal_score"]:
                    sc=screen(ticker,r,CONFIG,black);pl=plan(sig,r,CONFIG);payload={"symbol":sym,"company":item["company"],"direction":sig["direction"],"strategy":sig["strategy"],"regime":rg,"signal_score":round(sig["score"],1),"microstructure_score":round(ms,1),"reasons":sig["reasons"],"regime_details":meta,"execution_risk":sc,**pl,"signal_date":str(x.index[-1].date()),"timestamp":now_iso()}
                    if sc["pass"]:active[sym]={**payload,"entry_date":payload["signal_date"],"peak_price":payload["entry_price"],"trailing_stop":payload["stop_loss"]};signals.append(payload);emit("ENTRY",payload);cand=payload
                    else:cand={**payload,"rejected":True}
            raw=provisional_score(x,rg);near=min(100,raw+max(-10,(ms-50)*0.2))
            assets[sym]={"symbol":sym,"company":item["company"],"status":"ok","regime":rg,"price":float(r.Close),"adx14":float(r.ADX14),"atr14":float(r.ATR14),"relative_volume":float(r.RELVOL20) if pd.notna(r.RELVOL20) else None,"zscore20":float(r.ZS20),"microstructure_score":round(ms,1),"near_signal_score":round(near,1),"active":sym in active,"candidate":cand,"updated_at":now_iso()}
            print(f"[{i}/{len(items)}] {sym} -> ok regime={rg} near={near:.1f}")
        except Exception as e:assets[sym]={"symbol":sym,"company":item["company"],"status":"error","reason":f"{type(e).__name__}: {e}"};errs+=1;print(f"[{i}/{len(items)}] {sym} -> error: {e}")
    nxt=(start+len(items))%len(u);save_json(DATA/"cursor.json",{"next_index":nxt,"last_run":now_iso()});save_json(DATA/"state.json",{"active_trades":active,"closed_trades":closed[-500:],"updated_at":now_iso()})
    stats={"universe":len(u),"known_assets":len(assets),"active_trades":len(active),"signals_this_batch":len(signals),"exits_this_batch":len(exits),"batch_processed":proc,"batch_errors":errs,"next_index":nxt,"macro_blackout_today":black,"near_signals":sum(1 for a in assets.values() if a.get("near_signal_score",0)>=60 and not a.get("active"))}
    save_json(DATA/"scanner.json",{"generated_at":now_iso(),"stats":stats,"signals":signals,"exits":exits,"active_trades":sorted(active.values(),key=lambda x:x["symbol"]),"assets":sorted(assets.values(),key=lambda x:(0 if x.get("active") else 1,x.get("symbol","")))})
    print(stats)
if __name__=="__main__":main()
