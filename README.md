# Sowmax Scanner

A web app that scans Nifty 50, Nifty Next 50 or Nifty 100 stocks for **intraday** and **swing** setups,
plus a **Magic Indicator** that suggests NIFTY / BANK NIFTY / SENSEX option trades.

- **Live site:** https://sowmax-scanner.onrender.com (sign-in required)
- **Code:** https://github.com/soorajsuseelan1986-glitch/sowmax-scanner
- **Data:** Groww or Zerodha when connected, otherwise Yahoo Finance (delayed). See [Data sources](#data-sources).

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

- Server: `waitress` with one process and 8 threads. Keep it to **one process**: the scan cache and the
  data-download lock live in memory.
- Free plan: the site sleeps after 15 minutes idle, so the first visit afterwards takes 30–60 s.

### Settings (Render → Environment)
| Variable | Needed | What it is |
|---|---|---|
| `APP_USERNAME` | ✅ | Site login user ID |
| `APP_PASSWORD` | ✅ | Site login password |
| `SECRET_KEY` | auto | Signs login cookies (Render generates it) |
| `PYTHON_VERSION` | auto | `3.11.9` (set in `render.yaml`) |
| `GROWW_API_KEY` | optional | Groww TOTP token |
| `GROWW_TOTP_SECRET` | optional | Groww TOTP secret |
| `KITE_API_KEY` | optional | Zerodha Kite Connect API key |
| `KITE_API_SECRET` | optional | Zerodha Kite Connect API secret |

Never put these values in the code: the GitHub repo is public. After changing them, click **Save, rebuild, and deploy**.

## Login
- Every page and API call needs a sign-in. Without `APP_USERNAME` / `APP_PASSWORD`, nobody can sign in.
- A sign-in lasts 7 days per browser. **Sign out** is the arrow button in the top bar.
- One shared login. Broker data is licensed for your personal use, so don't give the login to others.

---

## Data sources
The app uses the first one that works: **Groww → Zerodha → Yahoo**.
The chip in the top bar shows which one is active. If a broker fails (expired login, outage), the app quietly falls back to Yahoo.

| | Groww Trade API | Zerodha Kite Connect | Yahoo Finance |
|---|---|---|---|
| Cost | ₹499/month + GST | ~₹500/month | Free |
| Daily login | **Automatic** (TOTP) | Click **Connect Zerodha** each day | None |
| Option premiums | **Live** | **Live** | Modelled (Black-Scholes) |
| Nifty 50 scan | ~7 s | ~17 s | ~10 s |
| Notes | History from 2020 | 3 requests/s limit | Delayed; may block cloud servers |

### Groww (recommended)
1. Subscribe to the **paid** Groww Trade API. The free trial has no live or historical data.
2. Go to https://groww.in/trade-api/api-keys. In the dropdown next to "Generate API key", choose
   **Generate TOTP token**, then copy the **TOTP token** and the **TOTP secret**.
3. Add them on Render as `GROWW_API_KEY` and `GROWW_TOTP_SECRET`.
4. The top bar shows **Groww live**. If it says **Groww offline**, hover over it to see the error.

### Zerodha (Kite Connect)
1. At https://developers.kite.trade create a **Connect** app (not Personal, which has no data APIs).
   - Redirect URL: `https://sowmax-scanner.onrender.com/kite/callback`
   - Postback URL: leave empty
2. Add `KITE_API_KEY` and `KITE_API_SECRET` on Render.
3. Click **Connect Zerodha** in the top bar and log in. Kite logs you out at 06:00 IST, so do this once each trading day.
   The button turns green (**Zerodha live**) when connected; click it again to disconnect.

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

**With Groww or Zerodha connected**, everything is live:
- the premium, expiry date (including holiday shifts) and lot size come from the broker
- each strike's implied volatility is worked out from its live price, and that drives SL and targets
- the option chart uses real candles

Labels show **live LTP** / **live premiums**.

**With Yahoo only**, premiums are *modelled* (Black-Scholes with India VIX as IV), and expiry and lot size
come from fixed rules in `magic.py` (`INDICES`). Holidays aren't handled. Type your broker's LTP into
**Your broker's LTP** to rescale Buy/SL/Targets.

---

## Links
- Swing mode, one stock: `/?mode=swing&stock=RELIANCE`
- Magic Indicator: `/?mode=magic&index=NIFTY`
- Magic chart full-screen in its own tab: `/?mode=magic&index=NIFTY&full=1`

## Project files
| File | What it does |
|---|---|
| `app.py` | Flask server: routes, login, Zerodha connect |
| `scanner.py` | Indicators, intraday/swing scans, charts, `_download()` (all candle fetching) |
| `magic.py` | Magic Indicator, strike selection, Black-Scholes, live option pricing |
| `feeds.py` | Picks the active data feed (Groww → Zerodha → Yahoo) |
| `groww_data.py` | Groww Trade API client |
| `kite_data.py` | Zerodha Kite Connect client |
| `universe.py` | Stock lists and index tickers |
| `static/` | Frontend (`index.html`, `app.js`, `style.css`, `login.html`) |
| `render.yaml` | Render deployment settings |

## Customise
- Stock lists: `universe.py`
- Rules and thresholds: `scanner.py` (`scan_intraday_one`, `scan_swing_one`)
- Cache: intraday 60 s, swing 15 min (`CACHE_TTL`). **Scan now** skips the cache.

## Troubleshooting
| Problem | Likely cause / fix |
|---|---|
| "Login isn't set up yet" | `APP_USERNAME` / `APP_PASSWORD` missing on Render |
| Scans come back empty | Yahoo is blocking Render's servers. Connect Groww or Zerodha. |
| **Groww offline** | Hover over the chip for the error. Check the TOTP token and secret, and that the subscription is active. Groww logins are limited to 150 a day; after a failure the app waits 10 minutes before retrying. |
| **Connect Zerodha** back after it was green | Kite's daily 06:00 logout. Click it and log in again. |
| Zerodha login doesn't return to the site | The Kite app's Redirect URL must be exactly `https://sowmax-scanner.onrender.com/kite/callback` |
| First page load is very slow | Render free plan waking up (30–60 s) |
