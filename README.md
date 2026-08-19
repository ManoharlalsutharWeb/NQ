# NQ IMPACT — REAL EVENT ENGINE (REBUILD)

## IMPORTANT
The GitHub Pages frontend is now the ROOT `index.html`.
It calls the Render backend explicitly:
`https://nq-w7mk.onrender.com`

The previous failure happened because a GitHub Pages page used a relative `/api/calendar` request. GitHub Pages returned an HTML 404 page, and the browser then tried to parse that HTML as JSON (`Unexpected token '<'`). This build uses an absolute backend URL and explicitly reports non-JSON responses.

## Structure

NQ/
- index.html              <- upload this at repository root for GitHub Pages
- app.py                  <- Render backend
- requirements.txt
- render.yaml
- README.md

A `templates/` copy is not required for the GitHub frontend. Flask serves the root index too.

## LIVE PROVIDERS

### Economic calendar
`TRADING_ECONOMICS_KEY` is required.

Trading Economics documents the calendar fields Actual, Previous, Forecast, revisions, importance and release time. It is the source for the weekly real event values. The event rule library in this project is separate and does not contain weekly values.

### NQ futures
`MASSIVE_API_KEY` and `NQ_TICKER` are required for the futures snapshot. Massive documents `/futures/v1/snapshot` and `/futures/v1/aggs/{ticker}`.

### News
GDELT DOC 2.0 is used for keyless news context. News is not used as a replacement for the economic calendar.

## RENDER ENVIRONMENT

Set:
TRADING_ECONOMICS_KEY=your_real_key
MASSIVE_API_KEY=your_real_key
NQ_TICKER=the_active_NQ_contract_for_your_plan

Never put these keys in index.html or GitHub.

## FIRST DEPLOYMENT CHECK

Open:
https://nq-w7mk.onrender.com/api/health

It must return JSON with:
ok: true
calendar_configured: true
market_configured: true

Then open:
https://nq-w7mk.onrender.com/api/calendar

It must return JSON with:
live: true
events: [...]

If the key is missing, the endpoint intentionally returns a JSON error instead of fake data.

## GITHUB PAGES

Upload ROOT `index.html` to:
https://manoharlalsutharweb.github.io/NQ/

The browser frontend will call the Render backend automatically.

## LOGIN
Username: admin
Password: Guruji@1379

For production, change the password mechanism before public sharing. This browser gate is not a substitute for server-side authentication.

## HISTORICAL ENGINE
No synthetic historical probabilities are included. To calculate real hit rates, store actual release records alongside NQ bars at 1m/5m/15m/30m/60m and calculate outcomes from those observations.
