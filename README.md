# Sowmax Scanner

A local web app that scans Nifty 50, Nifty Next 50 or Nifty 100 stocks for **intraday** and **swing** setups.

## Run
```bash
./run.sh            # creates .venv on first run, starts the server, opens the browser
# or: .venv/bin/python app.py   →  http://127.0.0.1:5000
```

## Intraday scan (5-minute candles)
Each stock gets one point for bullish (+1) or bearish (−1) on each of 6 checks:
VWAP · EMA 9/21 · Supertrend (10,3) · RSI(14) > 55 / < 45 · 15-min opening range break · previous-day high/low break.

Setups it tags: ORB Breakout/Breakdown, PDH/PDL break, Supertrend flip, EMA cross, Volume surge
(relative volume vs. same time on previous days), Open = Low / Open = High, Gap up/down.
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
2. **Strike:** the next expiry (NIFTY weekly Tue, SENSEX weekly Thu, BANK NIFTY monthly last Tue)
   at the strike whose delta is closest to 0.55 (ATM or one step in the money).
3. **Buy / SL / T1 / T2:** the index stop comes from the Magic line (Supertrend), bounded to 0.15–0.5% of spot.
   T1 = 1.5R and T2 = 3R. Each index level is converted to a premium with Black-Scholes.
4. The Magic indicator is also run on the option-premium chart, with a trailing Magic line and BUY/EXIT markers.

**Important:** option premiums are *modelled* (Black-Scholes with India VIX as IV), not live quotes.
Free data sources don't provide NSE/BSE option prices. Type your broker's live LTP into the
"Your broker's LTP" box to rescale Buy/SL/Targets. Expiry holidays and lot-size changes are not
handled automatically. Check them in `magic.py` (`INDICES`).
Deep link: `http://127.0.0.1:5000/?mode=magic&index=NIFTY`

## Live data from Zerodha (Kite Connect)
With Kite connected, all candles, index levels and **option premiums** come from Zerodha; otherwise the app uses Yahoo.

1. Subscribe at https://developers.kite.trade and create an app.
   Redirect URL: `https://sowmax-scanner.onrender.com/kite/callback`
2. On Render → Environment, add `KITE_API_KEY` and `KITE_API_SECRET`.
3. In the app, click **Connect Zerodha** (top bar) and log in. Kite tokens expire at 06:00 IST,
   so do this once each trading day. The button turns green ("Zerodha live") when connected.

Kite allows ~3 history requests/second, so a Nifty 100 scan takes ~35 s on Kite (Nifty 50 ~17 s).
Kite data is for your personal use only; don't share the site login with others.

## Customise
- Stock lists: `universe.py`
- Rules and thresholds: `scanner.py` (`scan_intraday_one`, `scan_swing_one`)
- Cache: intraday 60 s, swing 15 min (`CACHE_TTL`). "Scan now" skips the cache.
- Deep link: `http://127.0.0.1:5000/?mode=swing&stock=RELIANCE`

Data comes from Yahoo Finance via `yfinance` and may be delayed. Use it for education and finding trade ideas, not as investment advice.
