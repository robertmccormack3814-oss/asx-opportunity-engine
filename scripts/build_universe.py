import io,re,requests,pandas as pd
from common import DATA,save_json,now_iso
URL="https://www.asx.com.au/content/dam/asx/issuers/ISIN.xls";H={"User-Agent":"Mozilla/5.0 Chrome/151 Safari/537.36"}
def n(c):return re.sub(r"\s+"," ",str(c)).strip().lower()
def fc(df,keys):
    for c in df.columns:
        if any(k in n(c) for k in keys):return c
def main():
    r=requests.get(URL,headers=H,timeout=60);r.raise_for_status();raw=r.content;frames=[]
    try:frames.append(pd.read_excel(io.BytesIO(raw),engine="xlrd"))
    except:pass
    for enc in ("utf-8-sig","utf-8","latin1"):
        try:t=raw.decode(enc)
        except:continue
        for sep in ("\t",",","|"):
            try:
                d=pd.read_csv(io.StringIO(t),sep=sep)
                if len(d)>20 and len(d.columns)>=2:frames.append(d)
            except:pass
    if not frames:raise RuntimeError("Could not parse official ASX directory")
    df=max(frames,key=len);cc=fc(df,["asx code","security code","code"]);nc=fc(df,["company name","issuer name","issuer","name"]);dc=fc(df,["security description","description","security type"])
    rows={}
    for _,x in df.iterrows():
        code=re.sub(r"[^A-Z0-9]","",str(x.get(cc) or "").upper());name=str(x.get(nc) or "").strip();desc=str(x.get(dc) or "").strip() if dc is not None else ""
        if not re.fullmatch(r"[A-Z0-9]{3}",code) or not name or name.lower()=="nan":continue
        if any(k in desc.upper() for k in ["WARRANT","OPTION","RIGHT","BOND","NOTE","PREFERENCE SHARE","CAPITAL NOTE","CONVERTIBLE"]):continue
        rows[code]={"symbol":code,"ticker":code+".AX","company":name,"description":desc or None,"source":"Official ASX ISIN directory","discovered_at":now_iso()}
    u=sorted(rows.values(),key=lambda x:x["symbol"])
    if len(u)<1000:raise RuntimeError(f"Universe unexpectedly small: {len(u)}")
    save_json(DATA/"universe.json",u);print("ASX primary equity universe:",len(u))
if __name__=="__main__":main()
