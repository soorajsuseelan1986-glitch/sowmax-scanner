# Sowmax Scanner

A web app that scans Nifty 50, Nifty Next 50 or Nifty 100 stocks for **intraday** and **swing** setups,
plus a **Magic Indicator** that suggests NIFTY / BANK NIFTY / SENSEX option trades.

- **Live site:** https://sowmax-scanner.onrender.com (sign-in required)
- **Code:** https://github.com/soorajsuseelan1986-glitch/sowmax-scanner
- **Data:** Groww when connected, otherwise Yahoo Finance (delayed). See [Data sources](#data-sources).

For personal use: education and finding trade ideas, **not investment advice**.

---

## Run on your Mac
1. Create a file called `.env` in this folder (git ignores it, so it's never uploaded):
   ```bash
   APP_USERNAME=your-user-id
   APP_PASSWORD=your-password
   # optional live data, same keys as on Render:
   # GROWW_API_KEY=...
   # GROWW_TOTP_SECRET=...
   ```
2. Start it:
   ```bash
   ./run.sh   # creates .venv on first run, loads .env, starts the server, opens the browser
   ```
   Then open http://127.0.0.1:5000 and sign in.

## Deploy (Render)
The site runs on Render's free plan and is set up by [`render.yaml`](render.yaml).
**Every `git push` to `main` redeploys it automatically.**

- Server: `waitress` with one process and 8 threads. Keep it to **one process**: the caches and the
  Groww rate limiter live in memory.
- Free plan: the site sleeps after 15 minutes idle, so the first visit afterwards takes 30–60 s.
  To avoid this, ping `https://sowmax-scanner.onrender.com/login` every 10 min during market hours
  (free, e.g. cron-job.org, time zone Asia/Kolkata), or upgrade to Render **Starter** ($7/month, never sleeps,
  more memory).
- On start-up the server warms its caches (intraday scan, Magic summary, swing scan) in the background.
  Set `WARM_UP=0` to turn this off.

### Settings (Render → Environment)
| Variable | Needed | What it is |
|---|---|---|
| `APP_USERNAME` | ✅ | Site login user ID |
| `APP_PASSWORD` | ✅ | Site login password |
| `SECRET_KEY` | auto | Signs login cookies (Render generates it) |
| `PYTHON_VERSION` | auto | `3.11.9` (set in `render.yaml`) |
| `GROWW_API_KEY` | optional | Groww TOTP token |
| `GROWW_TOTP_SECRET` | optional | Groww TOTP secret |

Never put these values in the code: the GitHub repo is public. After changing them, click **Save, rebuild, and deploy**.

## Login
- Every page and API call needs a sign-in. Without `APP_USERNAME` / `APP_PASSWORD`, nobody can sign in.
- A sign-in lasts 7 days per browser. **Sign out** is the arrow button in the top bar.
- One shared login. Broker data is licensed for your personal use, so don't give the login to others.

---

## Data sources
The app uses **Groww** when it's connected and falls back to **Yahoo Finance** if Groww isn't set up or fails
(expired login, outage). The chip in the top bar shows the state: **Groww live** or **Groww offline**.

| | Groww Trade API | Yahoo Finance |
|---|---|---|
| Cost | ₹499/month + GST | Free |
| Daily login | **Automatic** (TOTP) | None |
| Option premiums | **Live** | Modelled (Black-Scholes) |
| Nifty 50 scan | ~7 s | ~10 s |
| Notes | History from 2020 | Delayed; may block cloud servers |

### Setting up Groww
1. Subscribe to the **paid** Groww Trade API. The free trial has no live or historical data.
2. Go to https://groww.in/trade-api/api-keys. In the dropdown next to "Generate API key", choose
   **Generate TOTP token** and give it a name.
3. The screen shows the values **only once**. Use the 📋 copy buttons (the token on screen is cut off):
   - **TOTP Token** (long, starts with `eyJ…`) → Render `GROWW_API_KEY`
   - **secret under the QR code** (about 32 letters A–Z and digits 2–7) → Render `GROWW_TOTP_SECRET`
4. Click **Save, rebuild, and deploy**. The top bar shows **Groww live**.
   To check from the browser, open `/api/feed` while signed in: it should say `"connected":true`.

Keep these keys out of chats, screenshots and code. If one leaks, delete it on the Groww API keys page and make a new one.
Ignore Groww's "static IP" note: that's only for placing orders, and this app only reads data.

### How Groww data is handled
- **Candles:** 5-minute and other intraday candles come from Groww's `/historical/candles`, which includes today's
  candles during market hours. Timestamps are Indian time (IST). Daily and weekly data come from Groww's older
  `/historical/candle/range` endpoint (years in one request); if that fails, the app falls back to the chunked one.
- **Cleaning:** Groww also sends **pre-open (09:00–09:15)** candles, often with an empty *open*, and flat
  **post-close** candles after 15:30. The app keeps only the regular 09:15–15:30 session and fills any missing
  open/high/low from the close, so charts and the opening-range checks aren't distorted.
- **Speed:** up to 8 requests at once, about 8 per second (under Groww's limits). A Nifty 50 scan takes about 7 s.
- **Login:** a fresh access token is made automatically each day after 06:00 IST, and again whenever Groww rejects one.
- **Options:** contract list (expiries, strikes, lot sizes) from Groww's public instrument file, downloaded once a day;
  live premiums from Groww's option chain.

---

## Intraday scan (5-minute candles)
Each stock gets +1 (bullish) or −1 (bearish) on each of 6 checks:
VWAP · EMA 9/21 · Supertrend (10,3) · RSI(14) > 55 / < 45 · 15-min opening range break · previous-day high/low break.

Setups it tags: ORB Breakout/Breakdown, PDH/PDL break, Supertrend flip, EMA cross, Volume surge
(relative volume vs. the same time on previous days), Open = Low / Open = High, Gap up/down.
Stop = Supertrend/ATR. Target = 2× risk (2R).

## Swing scan (daily candles)
6 checks: Price vs EMA 20 · EMA 20/50 · EMA 50/200 · MACD vs signal · RSI · ADX with +DI/−DI.

Setups: 20-day breakout (with volume), 20-day breakdown, pullback to EMA 20 in an uptrend,
MACD cross, golden/death cross, near 52-week high/low, RSI oversold bounce, NR7 squeeze, up/down trend.
Stop = 10-day swing low/high (limited to 1–3× ATR). Target = 2R.

**Signal:** score ≥ +4 → Strong Buy, ≥ +2 → Buy, ≤ −2 → Sell, ≤ −4 → Strong Sell.

## Magic Indicator (index options)
Open the **Magic Indicator** tab, then click NIFTY, BANK NIFTY or SENSEX.

1. **Magic score (−100 to +100)** on 5-min index candles. Weights: Supertrend 35, EMA 9/21 25,
   MACD momentum 15, ADX/DI 15, RSI 10. VWAP (20) is used only when volume exists; index feeds have none.
   ≥ +40 → **BUY CALL**, ≤ −40 → **BUY PUT**, otherwise **WAIT**.
2. **Strike:** the nearest expiry, at the strike whose delta is closest to 0.55 (ATM or one step in the money).
3. **Buy / SL / T1 / T2:** the index stop comes from the Magic line (Supertrend), bounded to 0.15–0.5% of spot.
   T1 = 1.5R and T2 = 3R. Each index level is converted to a premium with Black-Scholes.
4. The Magic indicator also runs on the option-premium chart, with a trailing Magic line and BUY/EXIT markers.

**With Groww connected**, everything is live:
- the premium, expiry date (including holiday shifts) and lot size come from the broker
- each strike's implied volatility is worked out from its live price, and that drives SL and targets
- the option chart uses real candles

Labels show **live LTP** / **live premiums from Groww**.

**With Yahoo only**, premiums are *modelled* (Black-Scholes with India VIX as IV), and expiry and lot size
come from fixed rules in `magic.py` (`INDICES`). Holidays aren't handled. Type your broker's LTP into
**Your broker's LTP** to rescale Buy/SL/Targets.

---

## Links
- Swing mode, one stock: `/?mode=swing&stock=RELIANCE`
- Data-feed status: `/api/feed` (`"source":"groww"` and `"connected":true` = live)
- Chart diagnostics: `/api/debug/chart/TITAN?tf=5m`. Shows candle count, order, duplicates, empty values and raw
  Groww sample rows (no keys). Use it when a chart looks wrong.
- Magic Indicator: `/?mode=magic&index=NIFTY`
- Magic chart full-screen in its own tab: `/?mode=magic&index=NIFTY&full=1`

## Project files
| File | What it does |
|---|---|
| `app.py` | Flask server: routes, login, data-feed status, warm-up |
| `scanner.py` | Indicators, intraday/swing scans, charts, `_download()` (all candle fetching) |
| `magic.py` | Magic Indicator, strike selection, Black-Scholes, live option pricing |
| `feeds.py` | Picks the active data feed (Groww, else Yahoo) |
| `groww_data.py` | Groww Trade API client |
| `universe.py` | Stock lists and index tickers |
| `static/` | Frontend (`index.html`, `app.js`, `style.css`, `login.html`) |
| `render.yaml` | Render deployment settings |

## Customise
- Stock lists: `universe.py`
- Rules and thresholds: `scanner.py` (`scan_intraday_one`, `scan_swing_one`)
- Scan cache: intraday 60 s, swing 15 min (`CACHE_TTL` in `scanner.py`). **Scan now** skips the cache.
  After that, a page refresh shows the last result instantly and updates it in the background; the footer
  says "refreshing…" until the new result appears (up to 20 min old for intraday and 6 h for swing; `MAX_STALE`).
- Chart cache: 30 s for intraday timeframes, 5 min for 1D/1W (`chart_data` in `scanner.py`).
  Charts never wait for a running scan.

## Troubleshooting
| Problem | Likely cause / fix |
|---|---|
| "Login isn't set up yet" | `APP_USERNAME` / `APP_PASSWORD` missing on Render |
| Scans come back empty | Yahoo is blocking Render's servers. Connect Groww. |
| **Groww offline** | Hover over the chip for the error. Check the TOTP token and secret, and that the subscription is active. Groww logins are limited to 150 a day; after a failure the app waits 10 minutes before retrying. |
| `/api/feed` error "Non-base32 digit found" or "isn't a valid TOTP secret" | The wrong value is in `GROWW_TOTP_SECRET`. It must be the short secret, not the `eyJ…` token. |
| First page load is very slow | Render free plan waking up (30–60 s). See the ping / Starter tip under Deploy. |
| Chart is empty, or shows a red "Chart error" | Hard-refresh (Cmd + Shift + R). If it persists, open `/api/debug/chart/<SYMBOL>?tf=5m`: `bad_values` should be 0 and `sorted` true. |
| A stock is listed under "no data" | Its symbol in `universe.py` may be outdated (e.g. after a rename). `ICICIPRULI` isn't in Groww's list. |
