"""SPREX NOVA — ML Engine: adapts to any spare parts dataset."""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error

_DATE   = ["date","time","month","year","period","week","day","timestamp","dt"]
_DEMAND = ["demand","qty","quantity","sales","usage","consumption","units","orders","sold","issued","consumed","count","volume"]
_PART   = ["part","item","sku","product","component","material","name","description","code","id","ref","type","category","article"]

def _score(col, kws):
    c = col.lower().replace("_"," ").replace("-"," ")
    return sum(1 for k in kws if k in c)

def detect_columns(df):
    cols = {"date":None,"demand":None,"part":None}
    for col in df.columns:
        if df[col].dtype == "datetime64[ns]": cols["date"]=col; break
        if _score(col,_DATE):
            try:
                p=pd.to_datetime(df[col],errors="coerce")
                if p.notna().mean()>.6: cols["date"]=col; break
            except: pass
    if not cols["date"]:
        for col in df.columns:
            try:
                p=pd.to_datetime(df[col],errors="coerce")
                if p.notna().mean()>.7: cols["date"]=col; break
            except: pass
    nums=df.select_dtypes(include=[np.number]).columns.tolist()
    best,bsc=None,-1
    for c in nums:
        s=_score(c,_DEMAND)
        if s>bsc: best,bsc=c,s
    cols["demand"]=best or (nums[0] if nums else None)
    cats=df.select_dtypes(include=["object","category"]).columns.tolist()
    best,bsc=None,-1
    for c in cats:
        if c==cols["date"]: continue
        s=_score(c,_PART)
        if s>bsc: best,bsc=c,s
    cols["part"]=best or (cats[0] if cats else None)
    return cols

def _build(grp,date_col,demand_col):
    g=grp.copy().reset_index(drop=True)
    if date_col:
        g[date_col]=pd.to_datetime(g[date_col],errors="coerce")
        g=g.sort_values(date_col).reset_index(drop=True)
        g["_t"]=(g[date_col]-g[date_col].min()).dt.days.fillna(0)
        g["_mo"]=g[date_col].dt.month.fillna(1)
        g["_q"]=g[date_col].dt.quarter.fillna(1)
    else:
        g["_t"]=np.arange(len(g),dtype=float); g["_mo"]=0; g["_q"]=0
    g[demand_col]=pd.to_numeric(g[demand_col],errors="coerce").fillna(0).clip(lower=0)
    g["_l1"]=g[demand_col].shift(1).fillna(0)
    g["_l2"]=g[demand_col].shift(2).fillna(0)
    g["_r3"]=g[demand_col].rolling(3,min_periods=1).mean()
    return g

FEAT=["_t","_mo","_q","_l1","_l2","_r3"]

def _future(last,steps):
    rows,prev,prev2=[],last.get("_l1",0),last.get("_l2",0)
    buf=[last.get("_r3",0)]*3; t0=last["_t"]+1; mo=int(last.get("_mo",1))
    for i in range(steps):
        r3=float(np.mean(buf[-3:]))
        rows.append({"_t":t0+i,"_mo":((mo+i-1)%12)+1,"_q":(((mo+i-1)%12)//3)+1,"_l1":prev,"_l2":prev2,"_r3":r3})
        prev2=prev; prev=r3; buf.append(r3)
    return pd.DataFrame(rows)

def _status(f,h):
    a=float(np.mean(h)) if len(h) else 1; fv=float(np.mean(f)); r=fv/(a+1e-9)
    if r>1.25: return "High Demand Expected","red"
    if r<0.75: return "Overstock Risk","sky"
    if fv>a*1.05: return "Purchase Recommended","green"
    if fv<a*0.90: return "Risk of Stockout","amber"
    return "Demand Stable","grey"

def run_forecast(filepath,steps=6):
    ext=filepath.rsplit(".",1)[-1].lower()
    df=pd.read_excel(filepath,engine="openpyxl") if ext in("xlsx","xls") else pd.read_csv(filepath)
    df.columns=[str(c).strip() for c in df.columns]; df=df.dropna(how="all")
    cols=detect_columns(df); warns=[]
    if not cols["demand"]: raise ValueError("Cannot find a numeric demand/quantity column.")
    if not cols["part"]: warns.append("No part column found — treating whole file as one group."); df["__part__"]="All Parts"; cols["part"]="__part__"
    if not cols["date"]: warns.append("No date column found — using row index as time axis.")
    results={}
    for pname,grp in df.groupby(cols["part"]):
        pname=str(pname)
        try:
            g=_build(grp,cols["date"],cols["demand"]); y=g[cols["demand"]].values.astype(float)
            if len(y)<2: continue
            X=g[FEAT].values
            mdl=RandomForestRegressor(100,random_state=42) if len(y)>=8 else LinearRegression()
            mdl.fit(X,y); yp=mdl.predict(X).clip(0); mae=round(float(mean_absolute_error(y,yp)),2)
            fX=_future({f:g[f].iloc[-1] for f in FEAT}|{"_l1":y[-1]},steps)
            yf=mdl.predict(fX[FEAT].values).clip(0)
            if cols["date"]:
                hd=g[cols["date"]].dt.strftime("%Y-%m-%d").tolist()
                ld=pd.to_datetime(g[cols["date"]].iloc[-1])
                dx=max(int((pd.to_datetime(g[cols["date"]].iloc[-1])-pd.to_datetime(g[cols["date"]].iloc[-2])).days) if len(g)>1 else 30,1)
                fd=[(ld+pd.Timedelta(days=dx*(i+1))).strftime("%Y-%m-%d") for i in range(steps)]
            else:
                hd=[str(i) for i in range(len(y))]; fd=[str(len(y)+i) for i in range(steps)]
            status,color=_status(yf,y)
            results[pname]=dict(part=pname,model="Random Forest" if len(y)>=8 else "Linear Regression",
                mae=mae,status=status,color=color,hist_dates=hd,hist_actual=[round(float(v),2) for v in y],
                hist_pred=[round(float(v),2) for v in yp],fut_dates=fd,fut_vals=[round(float(v),2) for v in yf],
                avg_hist=round(float(np.mean(y)),2),avg_fore=round(float(np.mean(yf)),2))
        except Exception as e:
            results[pname]=dict(part=pname,error=str(e))
    return dict(parts=results,warnings=warns,cols=cols,total=len(results))
