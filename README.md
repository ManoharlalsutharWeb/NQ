# NQ Impact — Real Event Engine

This build is deliberately **not a demo**. It contains no hard-coded weekly economic values and does not fabricate live prices.

## Live data providers

- Trading Economics Calendar: real event date/time, Actual, Previous, Forecast, revisions and importance. The provider documents country/date calendar endpoints and these fields.
- Massive Futures API: optional live futures snapshot/historical futures bars. Set `MASSIVE_API_KEY` and the active `NQ_TICKER`.
- The Event Impact Engine is reusable: event rules are permanent, while the week's Forecast/Previous/Actual come from the live calendar.

## Required environment variables

`TRADING_ECONOMICS_KEY` — required for live economic calendar.
`MASSIVE_API_KEY` — required for live futures market data.
`NQ_TICKER` — the active Nasdaq-100 E-mini futures contract identifier for your market-data plan.

Do not put API keys inside `index.html`.

## Run

```bash
pip install -r requirements.txt
export TRADING_ECONOMICS_KEY="..."
export MASSIVE_API_KEY="..."
export NQ_TICKER="..."
python app.py
```

For Render, use `render.yaml` and enter the same values as secret environment variables.

## Truthfulness rule

If an API is unavailable or a key is missing, the dashboard says OFFLINE / WAITING. It does not replace missing live data with demo numbers.

## Historical engine

The UI reserves the historical reaction layer. Real historical hit-rates should only be populated after pairing real calendar releases with real NQ bars (1m/5m/15m/30m/1h). No synthetic statistics are inserted.
