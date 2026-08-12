import pandas as pd,yfinance as yf
def screen(ticker,r,c,blackout):
 reasons=[];dv=float(r.DOLLARVOL20) if pd.notna(r.DOLLARVOL20) else 0;atr=float(r.ATR14);cl=float(r.Close);ap=atr/cl*100 if cl else 999;gp=abs(float(r.GAP))*100 if pd.notna(r.GAP) else 0;sp=None
 if dv<c["minimum_average_dollar_volume_20d"]:reasons.append(f"20d dollar volume A${dv:,.0f}")
 if ap>c["maximum_atr_pct"]:reasons.append(f"ATR% {ap:.1f}")
 if gp>c["maximum_gap_pct"]:reasons.append(f"gap {gp:.1f}%")
 if blackout:reasons.append("macro blackout")
 try:
  fi=yf.Ticker(ticker).fast_info;bid=getattr(fi,"bid",None);ask=getattr(fi,"ask",None)
  if bid and ask and ask>bid:
   sp=(ask-bid)/((ask+bid)/2)*100
   if sp>c["maximum_estimated_spread_pct"]:reasons.append(f"spread {sp:.2f}%")
 except:pass
 return {"pass":not reasons,"reasons":reasons,"average_dollar_volume_20d":dv,"atr_pct":ap,"gap_pct":gp,"estimated_spread_pct":sp}
def plan(sig,r,c):
 st=sig["strategy"];cl=float(r.Close);atr=float(r.ATR14)
 if st=="TREND_BREAKOUT":sm,tr=c["trend"]["stop_atr"],c["trend"]["target_r"]
 elif st=="MEAN_REVERSION":sm,tr=c["mean_reversion"]["stop_atr"],c["mean_reversion"]["target_r"]
 else:sm,tr=c["squeeze"]["stop_atr"],c["squeeze"]["target_r"]
 risk=atr*sm;stop=cl-risk;target=cl+risk*tr;u100=(100000*(c["risk_per_trade_pct"]/100))/risk if risk>0 else 0;actual=None
 if c.get("account_equity_aud"):actual=(c["account_equity_aud"]*(c["risk_per_trade_pct"]/100))/risk
 return {"entry_price":cl,"atr14":atr,"stop_loss":stop,"profit_target":target,"risk_per_share":risk,"target_r":tr,"units_per_100k":u100,"actual_units":actual,"risk_per_trade_pct":c["risk_per_trade_pct"]}
