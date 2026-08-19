
import os, re, sqlite3, time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE, "nq_impact.db"))
TE_KEY = os.getenv("TRADING_ECONOMICS_KEY", "").strip()
MASSIVE_KEY = os.getenv("MASSIVE_API_KEY", "").strip()
NQ_TICKER = os.getenv("NQ_TICKER", "").strip()
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "20"))

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/api/*": {"origins": "*"}})
HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "NQ-Impact/2.0"})

# Permanent event intelligence rules. Weekly values NEVER live here.
RULES = {
 "cpi": ("inflation","lower_better",0.10,100,"Inflation surprise changes the expected Fed path; lower-than-consensus is normally supportive for long-duration growth.","US 2Y, US 10Y, DXY, Fed pricing, NQ/SOX"),
 "core cpi": ("inflation","lower_better",0.10,100,"Core inflation is a high-sensitivity policy signal.","US 2Y, US 10Y, DXY, Fed pricing, NQ/SOX"),
 "ppi": ("inflation","lower_better",0.15,70,"Producer-price surprise can change pipeline inflation expectations.","US 2Y/10Y, DXY"),
 "pce": ("inflation","lower_better",0.10,100,"PCE is a major Fed inflation gauge.","US 2Y, US 10Y, DXY, Fed pricing"),
 "nonfarm payroll": ("labor","labor",50,90,"Payrolls are both a growth and policy signal; wages and unemployment must be read with payrolls.","Unemployment, wages, US 2Y/10Y, DXY, NQ/SOX"),
 "unemployment rate": ("labor","unemployment",0.10,65,"A modest rise can support easing expectations; a sharp rise can become growth risk.","NFP, wages, US 2Y, NQ"),
 "average hourly earnings": ("labor","lower_better",0.10,90,"Wage inflation affects inflation persistence and Fed expectations.","NFP, US 2Y, DXY, NQ"),
 "jolts": ("labor","labor",0.10,60,"Cooling labor demand can reduce policy pressure; extreme deterioration can become growth-negative.","Claims, NFP, US 2Y, NQ"),
 "adp": ("labor","labor",25,55,"ADP is contextual labor information, not a substitute for NFP.","NFP, US 2Y, NQ"),
 "jobless claims": ("labor","claims",20,55,"Higher claims can lower yields, but extreme deterioration becomes growth risk.","4-week average, NFP, US 2Y, NQ"),
 "retail sales": ("growth","growth",0.20,60,"Demand can support earnings while also lifting yields; the rates reaction decides the macro direction.","US 10Y, DXY, NQ breadth"),
 "gdp": ("growth","growth",0.50,65,"Growth affects earnings and rates simultaneously.","US 10Y, DXY, NQ/SOX"),
 "ism": ("growth","growth",1.0,60,"PMI/ISM surprises alter growth expectations; Prices Paid can change the inflation interpretation.","Prices Paid, US 10Y, NQ"),
 "pmi": ("growth","growth",1.0,60,"PMI surprises alter growth expectations; rates confirmation matters.","US 10Y, NQ"),
 "fomc": ("policy","policy",25,100,"FOMC is not just the headline rate: statement, projections and press conference can dominate.","US 2Y, US 10Y, DXY, Fed pricing, NQ"),
}

def init_db():
    con=sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS reactions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, calendar_id TEXT, event TEXT, release_time TEXT,
      forecast REAL, actual REAL, previous REAL, surprise REAL, score REAL, bias TEXT,
      nq_before REAL, nq_1m REAL, nq_5m REAL, nq_15m REAL, nq_30m REAL, nq_60m REAL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    con.commit(); con.close()

def num(v):
    if v is None or v == "": return None
    m=re.search(r"-?\d+(?:\.\d+)?", str(v).replace(",",""))
    return float(m.group()) if m else None

def norm(s):
    return re.sub(r"[^a-z0-9 ]+"," ",str(s).lower()).strip()

def rule(event):
    n=norm(event)
    for key,val in RULES.items():
        if key in n:
            fam,direction,scale,maxscore,logic,confirm=val
            return {"family":fam,"direction":direction,"scale":scale,"max":maxscore,"logic":logic,
                    "confirm":[x.strip() for x in confirm.split(",")]}
    return {"family":"other","direction":"unknown","scale":1,"max":25,
            "logic":"No dedicated rule exists yet. The engine will not invent a directional signal.",
            "confirm":["NQ","US 2Y/10Y","DXY","VIX"]}

def classify(event, forecast, actual):
    r=rule(event)
    if forecast is None or actual is None:
        return {"score":None,"bias":"WAITING","zone":"WAITING","confidence":None,"rule":r,"surprise":None}
    surprise=actual-forecast
    z=surprise/max(abs(r["scale"]),1e-9)
    score=0; bias="SIDEWAYS"; zone="IN LINE"
    if r["direction"]=="lower_better":
        if z<=-3: score=r["max"]; bias="STRONG BULLISH"; zone="VERY COOL"
        elif z<=-1: score=round(r["max"]*.50); bias="BULLISH"; zone="COOL"
        elif z>=3: score=-r["max"]; bias="STRONG BEARISH"; zone="VERY HOT"
        elif z>=1: score=-round(r["max"]*.50); bias="BEARISH"; zone="HOT"
    elif r["direction"]=="policy":
        if z<=-1: score=round(r["max"]*.75); bias="BULLISH"; zone="DOVISH"
        elif z>=1: score=-round(r["max"]*.85); bias="BEARISH"; zone="HAWKISH"
    elif r["direction"]=="unemployment":
        if z>=3: score=-round(r["max"]*.70); bias="BEARISH / GROWTH RISK"; zone="SHARP RISE"
        elif z>=1: score=round(r["max"]*.35); bias="BULLISH"; zone="COOLING LABOR"
        elif z<=-1: score=-round(r["max"]*.25); bias="BEARISH"; zone="TIGHT LABOR"
    elif r["direction"]=="claims":
        if z>=3: score=-round(r["max"]*.70); bias="BEARISH / GROWTH RISK"; zone="VERY WEAK"
        elif z>=1: score=round(r["max"]*.30); bias="BULLISH"; zone="COOLING"
        elif z<=-1: score=-round(r["max"]*.25); bias="BEARISH"; zone="TIGHT LABOR"
    elif r["direction"]=="labor":
        if z<=-3: score=round(r["max"]*.65); bias="BULLISH / GROWTH RISK"; zone="VERY WEAK"
        elif z<=-1: score=round(r["max"]*.30); bias="BULLISH"; zone="COOLING"
        elif z>=3: score=-round(r["max"]*.45); bias="BEARISH / HAWKISH"; zone="VERY HOT"
        elif z>=1: score=-round(r["max"]*.22); bias="MIXED-BEARISH"; zone="STRONG"
    elif r["direction"]=="growth":
        if z<=-3: score=-r["max"]; bias="BEARISH / GROWTH RISK"; zone="VERY WEAK"
        elif z<=-1: score=-round(r["max"]*.25); bias="MIXED"; zone="WEAK"
        elif z>=3: score=round(r["max"]*.55); bias="BULLISH / GROWTH"; zone="VERY STRONG"
        elif z>=1: score=round(r["max"]*.25); bias="BULLISH"; zone="STRONG"
    conf=min(96,max(50,round(50+min(42,abs(z)*13))))
    return {"score":score,"bias":bias,"zone":zone,"confidence":conf,"rule":r,"surprise":surprise}

def normalize_event(x):
    f=num(x.get("Forecast")); a=num(x.get("Actual")); p=num(x.get("Previous"))
    c=classify(x.get("Event",""),f,a)
    return {
      "id":str(x.get("CalendarId") or x.get("Ticker") or f"{x.get('Date')}|{x.get('Event')}"),
      "date":x.get("Date"),"event":x.get("Event") or x.get("Category"),
      "category":x.get("Category"),"country":x.get("Country"),"source":x.get("Source"),
      "actual":x.get("Actual"),"previous":x.get("Previous"),"forecast":x.get("Forecast"),
      "revised":x.get("Revised"),"importance":int(x.get("Importance") or 0),
      "unit":x.get("Unit"),"currency":x.get("Currency"),"last_update":x.get("LastUpdate"),
      "actual_value":num(x.get("ActualValue")) if x.get("ActualValue") is not None else a,
      "forecast_value":num(x.get("ForecastValue")) if x.get("ForecastValue") is not None else f,
      "previous_value":num(x.get("PreviousValue")) if x.get("PreviousValue") is not None else p,
      "actual_ready":a is not None,
      "score":c["score"],"bias":c["bias"],"zone":c["zone"],"confidence":c["confidence"],
      "surprise":c["surprise"],"rule":c["rule"]
    }

def te_calendar():
    if not TE_KEY:
        raise RuntimeError("TRADING_ECONOMICS_KEY is missing in Render Environment.")
    url="https://api.tradingeconomics.com/calendar/country/united%20states"
    r=HTTP.get(url,params={"c":TE_KEY,"f":"json","values":"true"},timeout=20)
    if r.status_code>=400:
        raise RuntimeError(f"Trading Economics HTTP {r.status_code}: {r.text[:300]}")
    if "application/json" not in r.headers.get("content-type","").lower():
        raise RuntimeError(f"Calendar provider returned non-JSON: {r.text[:120]}")
    return r.json()

def market_snapshot():
    if not MASSIVE_KEY or not NQ_TICKER:
        raise RuntimeError("MASSIVE_API_KEY or NQ_TICKER is missing.")
    url="https://api.massive.com/futures/v1/snapshot"
    r=HTTP.get(url,params={"apiKey":MASSIVE_KEY,"ticker":NQ_TICKER,"limit":1},timeout=15)
    if r.status_code>=400: raise RuntimeError(f"Massive HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

@app.route("/")
def root():
    return send_from_directory(BASE,"index.html")

@app.route("/api/health")
def health():
    return jsonify({
      "ok":True,
      "calendar_configured":bool(TE_KEY),
      "market_configured":bool(MASSIVE_KEY and NQ_TICKER),
      "timestamp":datetime.now(timezone.utc).isoformat()
    })

@app.route("/api/calendar")
def calendar():
    try:
        raw=te_calendar()
        events=[normalize_event(x) for x in raw if str(x.get("Country","")).lower()=="united states"]
        events.sort(key=lambda e:(e["date"] or "",-e["importance"]))
        return jsonify({"live":True,"provider":"Trading Economics","updated_at":datetime.now(timezone.utc).isoformat(),"events":events})
    except Exception as e:
        return jsonify({"live":False,"provider":"Trading Economics","error":str(e),"events":[]}),503

@app.route("/api/market")
def market():
    try:
        raw=market_snapshot()
        return jsonify({"live":True,"provider":"Massive","ticker":NQ_TICKER,"data":raw})
    except Exception as e:
        return jsonify({"live":False,"provider":"Massive","error":str(e)}),503

@app.route("/api/analyze",methods=["POST"])
def analyze():
    p=request.get_json(silent=True) or {}
    event=p.get("event",""); f=num(p.get("forecast")); a=num(p.get("actual")); prev=num(p.get("previous"))
    if f is None or a is None: return jsonify({"error":"Forecast and Actual are required."}),400
    c=classify(event,f,a)
    return jsonify({"event":event,"forecast":f,"actual":a,"previous":prev,**c})

@app.route("/api/history/<path:event>")
def history(event):
    con=sqlite3.connect(DB_PATH); con.row_factory=sqlite3.Row
    rows=con.execute("SELECT * FROM reactions WHERE event LIKE ? ORDER BY release_time DESC LIMIT 100",
                     ("%"+event+"%",)).fetchall(); con.close()
    return jsonify({"event":event,"count":len(rows),"rows":[dict(x) for x in rows]})

@app.route("/api/news")
def news():
    # Keyless GDELT DOC 2.0 article search. This is news context only, never an economic-calendar substitute.
    q=request.args.get("q","Nasdaq futures OR CPI OR Federal Reserve OR NFP")
    try:
        url="https://api.gdeltproject.org/api/v2/doc/doc"
        r=HTTP.get(url,params={"query":q,"mode":"ArtList","format":"json","timespan":"24h","maxrecords":30,"sort":"datedesc"},timeout=20)
        if r.status_code>=400: raise RuntimeError(f"GDELT HTTP {r.status_code}")
        data=r.json()
        return jsonify({"live":True,"provider":"GDELT DOC 2.0","articles":data.get("articles",[])})
    except Exception as e:
        return jsonify({"live":False,"provider":"GDELT DOC 2.0","error":str(e),"articles":[]}),503

@app.route("/api/rules")
def rules():
    return jsonify({"rules":{k:{"family":v[0],"direction":v[1],"scale":v[2],"max_score":v[3],"logic":v[4],"confirmation":[x.strip() for x in v[5].split(",")]} for k,v in RULES.items()}})

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","10000")))
