
import os, re, math, sqlite3
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request
import requests

app = Flask(__name__)
DB = os.getenv("DB_PATH", "nq_impact.db")
TE_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()
MASSIVE_KEY = os.getenv("MASSIVE_API_KEY", "").strip()
NQ_TICKER = os.getenv("NQ_TICKER", "").strip()
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "30"))

session = requests.Session()
session.headers.update({"User-Agent":"NQ-Impact-Real-Engine/1.0"})

# Reusable event intelligence rules. These are rules, not weekly forecast values.
RULES = {
    "cpi": {"keys":["cpi","consumer price index"],"family":"inflation","direction":"lower_better","scale":0.10,"max":95,
            "logic":"Lower-than-consensus inflation is generally supportive for NQ through lower rate pressure; hotter inflation can pressure long-duration growth valuations.",
            "confirm":["US 2Y/10Y","DXY","Fed rate expectations","SOX/NVDA","NQ first 5–15m"]},
    "core cpi": {"keys":["core cpi"],"family":"inflation","direction":"lower_better","scale":0.10,"max":100,
            "logic":"Core inflation is a key underlying inflation signal. Surprise direction matters more than the previous reading alone.",
            "confirm":["US 2Y","US 10Y","DXY","Fed pricing","NQ/SOX"]},
    "ppi": {"keys":["ppi","producer price"],"family":"inflation","direction":"lower_better","scale":0.15,"max":75,
            "logic":"PPI can alter pipeline-inflation expectations and the expected policy path.",
            "confirm":["US 2Y/10Y","DXY","Core PCE implications"]},
    "pce": {"keys":["pce price","core pce"],"family":"inflation","direction":"lower_better","scale":0.10,"max":95,
            "logic":"PCE is a major Fed inflation gauge; a surprise can materially reprice policy expectations.",
            "confirm":["US 2Y","US 10Y","Fed pricing","DXY"]},
    "nfp": {"keys":["non farm payroll","nonfarm payroll"],"family":"labor","direction":"complex","scale":50,"max":90,
            "logic":"Payrolls are a growth and policy signal. Strong payrolls can be bearish for NQ if they lift rate expectations; very weak payrolls can become recessionary.",
            "confirm":["Unemployment","Average Hourly Earnings","US 2Y/10Y","DXY","NQ/SOX"]},
    "unemployment": {"keys":["unemployment rate"],"family":"labor","direction":"complex","scale":0.20,"max":65,
            "logic":"A modest rise can support easing expectations, while a sharp deterioration can become a growth/recession warning.",
            "confirm":["NFP","Wages","US 2Y","NQ"]},
    "wages": {"keys":["average hourly earnings","hourly earnings"],"family":"labor","direction":"lower_better","scale":0.10,"max":85,
            "logic":"Wage inflation affects inflation persistence and Fed expectations.",
            "confirm":["NFP","US 2Y","DXY","NQ"]},
    "jolts": {"keys":["jolts","job openings"],"family":"labor","direction":"complex","scale":0.10,"max":60,
            "logic":"Cooling labor demand can reduce policy pressure; extreme deterioration can become growth-negative.",
            "confirm":["Claims","NFP","US 2Y","NQ"]},
    "adp": {"keys":["adp employment"],"family":"labor","direction":"complex","scale":25,"max":55,
            "logic":"ADP is a private payroll signal and contextual input before NFP; it should not be treated as identical to NFP.",
            "confirm":["NFP","US 2Y","NQ"]},
    "jobless": {"keys":["initial jobless claims","jobless claims"],"family":"labor","direction":"complex","scale":20,"max":55,
            "logic":"Higher claims can lower yields, but extreme deterioration can create growth risk.",
            "confirm":["4-week average","NFP","US 2Y","NQ"]},
    "retail": {"keys":["retail sales"],"family":"growth","direction":"complex","scale":0.20,"max":60,
            "logic":"Strong demand can support earnings but can also lift yields; very weak demand can become growth-negative.",
            "confirm":["US 10Y","DXY","NQ breadth"]},
    "gdp": {"keys":["gdp"],"family":"growth","direction":"complex","scale":0.50,"max":60,
            "logic":"Growth data affects both earnings expectations and rates, so the yield response is a key confirmation.",
            "confirm":["US 10Y","DXY","NQ/SOX"]},
    "ism": {"keys":["ism manufacturing","ism services","pmi"],"family":"growth","direction":"complex","scale":1.0,"max":55,
            "logic":"PMI/ISM surprises shift growth expectations; strong growth can be positive for earnings but negative for rates.",
            "confirm":["Prices paid","US 10Y","NQ"]},
    "fomc": {"keys":["fomc","federal funds rate","interest rate decision"],"family":"policy","direction":"policy","scale":25,"max":100,
            "logic":"FOMC impact is not just the rate. Statement, projections and press conference can dominate the headline decision.",
            "confirm":["US 2Y","US 10Y","DXY","Fed pricing","NQ"]},
}

def db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS event_reactions(
        id INTEGER PRIMARY KEY AUTOINCREMENT, calendar_id TEXT, event TEXT, dt TEXT,
        forecast REAL, actual REAL, previous REAL, surprise REAL,
        impact REAL, bias TEXT, nq_before REAL, nq_5m REAL, nq_15m REAL, nq_30m REAL, nq_60m REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit()
    return c

def parse_num(v):
    if v is None or v == "": return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",",""))
    return float(m.group()) if m else None

def norm(s): return re.sub(r"[^a-z0-9 ]+"," ",str(s).lower()).strip()

def rule_for(event):
    n = norm(event)
    for key,r in RULES.items():
        if any(k in n for k in r["keys"]): return r
    return {"family":"other","direction":"complex","scale":1,"max":35,
            "logic":"No event-specific rule is configured yet. Do not force a directional NQ signal.",
            "confirm":["NQ price","US 2Y/10Y","DXY","VIX"]}

def scenario(rule, surprise):
    scale = max(abs(rule.get("scale",1)), 1e-9)
    z = surprise / scale
    if abs(z) < .5: return 0, "SIDEWAYS", "IN-LINE"
    if rule["direction"] == "lower_better":
        if z <= -3: return rule["max"], "STRONG BULLISH", "VERY COOL"
        if z <= -1: return round(rule["max"]*.52), "BULLISH", "COOL"
        if z >= 3: return -rule["max"], "STRONG BEARISH", "VERY HOT"
        if z >= 1: return -round(rule["max"]*.52), "BEARISH", "HOT"
    if rule["direction"] == "policy":
        if z <= -1: return round(rule["max"]*.75), "BULLISH", "DOVISH"
        if z >= 1: return -round(rule["max"]*.85), "BEARISH", "HAWKISH"
    if rule["direction"] == "complex":
        if rule["family"] == "labor":
            if z <= -3: return round(rule["max"]*.65), "BULLISH / GROWTH RISK", "VERY WEAK"
            if z <= -1: return round(rule["max"]*.30), "BULLISH", "COOLING"
            if z >= 3: return -round(rule["max"]*.40), "BEARISH / HAWKISH", "VERY HOT"
            if z >= 1: return -round(rule["max"]*.20), "MIXED-BEARISH", "STRONG"
        elif rule["family"] == "growth":
            if z <= -3: return -rule["max"], "BEARISH / GROWTH RISK", "VERY WEAK"
            if z <= -1: return -round(rule["max"]*.25), "MIXED", "WEAK"
            if z >= 3: return round(rule["max"]*.55), "BULLISH / GROWTH", "VERY STRONG"
            if z >= 1: return round(rule["max"]*.25), "BULLISH", "STRONG"
    return 0, "SIDEWAYS", "IN-LINE"

def score_event(e):
    f,a,p = parse_num(e.get("Forecast")), parse_num(e.get("Actual")), parse_num(e.get("Previous"))
    if f is None or a is None:
        return {**e, "has_actual":False, "score":None, "bias":"WAITING", "surprise":None}
    r=rule_for(e.get("Event",""))
    s,b,z=scenario(r,a-f)
    conf=min(95, max(50, round(50+min(38,abs(a-f)/(abs(r["scale"]) or 1)*14))))
    return {**e,"has_actual":True,"score":s,"bias":b,"zone":z,"surprise":a-f,"confidence":conf,"rule":r}

def te_calendar(start=None,end=None):
    if not TE_KEY:
        raise RuntimeError("TRADING_ECONOMICS_KEY is not configured")
    if start and end:
        url=f"https://api.tradingeconomics.com/calendar/country/united%20states/{start}/{end}"
    else:
        url="https://api.tradingeconomics.com/calendar/country/united%20states"
    r=session.get(url,params={"c":TE_KEY,"f":"json"},timeout=15)
    r.raise_for_status()
    return r.json()

@app.get("/")
def index(): return render_template("index.html")

@app.get("/api/health")
def health():
    return jsonify({"ok":True,"economic_calendar":bool(TE_KEY),"market_data":bool(MASSIVE_KEY and NQ_TICKER),"timestamp":datetime.now(timezone.utc).isoformat()})

@app.get("/api/calendar")
def calendar():
    try:
        data=te_calendar()
        important=[x for x in data if x.get("Country")=="United States" and int(x.get("Importance") or 0)>=2]
        out=[score_event(x) for x in important]
        return jsonify({"live":True,"source":"Trading Economics","events":out})
    except Exception as ex:
        return jsonify({"live":False,"error":str(ex),"events":[]}), 503

@app.get("/api/event/<path:name>")
def event_history(name):
    if not TE_KEY: return jsonify({"live":False,"error":"TRADING_ECONOMICS_KEY is not configured"}),503
    try:
        today=datetime.now(timezone.utc).date()
        start=(today-timedelta(days=365*5)).isoformat()
        end=today.isoformat()
        data=te_calendar(start,end)
        matches=[x for x in data if norm(name) in norm(x.get("Event",""))]
        return jsonify({"live":True,"event":name,"history":[score_event(x) for x in matches]})
    except Exception as ex:
        return jsonify({"live":False,"error":str(ex)}),503

@app.get("/api/market")
def market():
    if not (MASSIVE_KEY and NQ_TICKER):
        return jsonify({"live":False,"error":"MASSIVE_API_KEY and NQ_TICKER are required"}),503
    url=f"https://api.massive.com/futures/v1/snapshot"
    try:
        r=session.get(url,params={"apiKey":MASSIVE_KEY,"ticker":NQ_TICKER},timeout=12)
        r.raise_for_status()
        return jsonify({"live":True,"source":"Massive","data":r.json()})
    except Exception as ex:
        return jsonify({"live":False,"error":str(ex)}),503

@app.post("/api/analyze")
def analyze():
    payload=request.get_json(force=True)
    event=payload.get("event","")
    f=parse_num(payload.get("forecast")); a=parse_num(payload.get("actual")); p=parse_num(payload.get("previous"))
    if f is None or a is None: return jsonify({"error":"Forecast and Actual are required"}),400
    r=rule_for(event); score,bias,zone=scenario(r,a-f)
    conf=min(95,max(50,round(50+min(40,abs(a-f)/(abs(r["scale"]) or 1)*14))))
    return jsonify({"event":event,"forecast":f,"actual":a,"previous":p,"surprise":a-f,
                    "score":score,"bias":bias,"zone":zone,"confidence":conf,"rule":r})

if __name__=="__main__":
    db()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
