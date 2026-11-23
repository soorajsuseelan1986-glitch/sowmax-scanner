"""Scanner engine: data download, indicators, intraday + swing setups."""
import math
import threading
import time
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

import feeds
from universe import INDEX_TICKERS, UNIVERSES

IST = "Asia/Kolkata"

# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def atr(df, n=14):
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def macd(close, f=12, s=26, sig=9):
    line = ema(close, f) - ema(close, s)
    signal = ema(line, sig)
    return line, signal, line - signal


def adx(df, n=14):
    h, l = df["High"], df["Low"]
    up, dn = h.diff(), -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0), pdi, mdi


def supertrend(df, n=10, mult=3.0):
    a = atr(df, n).values
    hl2 = ((df["High"] + df["Low"]) / 2).values
    close = df["Close"].values
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    st = np.zeros(len(df))
    dirn = np.ones(len(df))
    for i in range(1, len(df)):
        fub[i] = ub[i] if (ub[i] < fub[i - 1] or close[i - 1] > fub[i - 1]) else fub[i - 1]
        flb[i] = lb[i] if (lb[i] > flb[i - 1] or close[i - 1] < flb[i - 1]) else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if close[i] < flb[i] else 1
        else:
            dirn[i] = 1 if close[i] > fub[i] else -1
        st[i] = flb[i] if dirn[i] == 1 else fub[i]
    st[0] = flb[0]
    return pd.Series(st, index=df.index), pd.Series(dirn, index=df.index)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

_dl_lock = threading.RLock()  # yfinance downloads are not thread-safe; run them one at a time


def _download(tickers, period, interval):
    with _dl_lock:
        feed = feeds.active()  # Groww when connected; Yahoo otherwise or if it fails
        if feed:
            try:
                data = feed.download(tickers, period, interval)
                if data:
                    return data
            except Exception as e:
                print(f"{feed.NAME} download failed, using Yahoo:", type(e).__name__, e)
        return _download_unlocked(tickers, period, interval)


def data_source():
    return feeds.name()


def _download_unlocked(tickers, period, interval):
    raw = yf.download(tickers, period=period, interval=interval, group_by="ticker",
                      auto_adjust=False, progress=False, threads=True)
    out = {}
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        if df.empty:
            continue
        idx = df.index
        df.index = idx.tz_localize("UTC").tz_convert(IST) if idx.tz is None else idx.tz_convert(IST)
        out[t] = df
    return out


def market_status():
    now = pd.Timestamp.now(tz=IST)
    pre_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now.weekday() >= 5:
        return "closed", "Weekend"
    if now < pre_t:
        return "closed", "Market closed"
    if now < open_t:
        return "pre", "Pre-open"
    if now > close_t:
        return "closed", "Market closed"
    return "open", "Market open"


def _r(x, d=2):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return round(float(x), d)


def _signal(score, max_score):
    pct = score / max_score
    if pct >= 0.6:
        return "Strong Buy"
    if pct >= 0.25:
        return "Buy"
    if pct <= -0.6:
        return "Strong Sell"
    if pct <= -0.25:
        return "Sell"
    return "Neutral"


# --------------------------------------------------------------------------
# Intraday scan (5-minute candles)
# --------------------------------------------------------------------------

def scan_intraday_one(sym, df):
    if len(df) < 30:
        return None
    df = df.copy()
    df["date"] = df.index.date
    days = sorted(df["date"].unique())
    if len(days) < 2:
        return None
    today, prev = days[-1], days[-2]
    td = df[df["date"] == today]
    pdf = df[df["date"] == prev]
    if td.empty:
        return None

    close = df["Close"]
    df["ema9"], df["ema21"] = ema(close, 9), ema(close, 21)
    df["rsi"] = rsi(close)
    df["atr"] = atr(df)
    df["st"], df["st_dir"] = supertrend(df, 10, 3)

    tp = (td["High"] + td["Low"] + td["Close"]) / 3
    cumvol = td["Volume"].cumsum().replace(0, np.nan)
    vwap_series = (tp * td["Volume"]).cumsum() / cumvol
    vwap = vwap_series.iloc[-1] if not math.isnan(vwap_series.iloc[-1]) else tp.iloc[-1]

    last = df.iloc[-1]
    ltp = last["Close"]
    pdh, pdl, pdc = pdf["High"].max(), pdf["Low"].min(), pdf["Close"].iloc[-1]
    day_open, day_high, day_low = td["Open"].iloc[0], td["High"].max(), td["Low"].min()
    orb = td.iloc[:3]  # first 15 minutes
    orh, orl = orb["High"].max(), orb["Low"].min()

    # relative volume: today's cumulative volume vs avg of prior days up to the same bar count
    n = len(td)
    prior = [df[df["date"] == d]["Volume"].iloc[:n].sum() for d in days[:-1]]
    prior = [p for p in prior if p > 0]
    rvol = td["Volume"].sum() / np.mean(prior) if prior else None

    checks = []  # (label, +1 / -1 / 0, detail)

    def chk(label, bull, bear, detail):
        checks.append({"label": label, "v": 1 if bull else (-1 if bear else 0), "detail": detail})

    chk("VWAP", ltp > vwap, ltp < vwap, f"LTP {'above' if ltp > vwap else 'below'} VWAP {vwap:.2f}")
    chk("EMA 9/21", last["ema9"] > last["ema21"], last["ema9"] < last["ema21"],
        f"EMA9 {last['ema9']:.2f} vs EMA21 {last['ema21']:.2f}")
    chk("Supertrend", last["st_dir"] == 1, last["st_dir"] == -1,
        f"{'Green' if last['st_dir'] == 1 else 'Red'} @ {last['st']:.2f}")
    chk("RSI", last["rsi"] > 55, last["rsi"] < 45, f"RSI(14) {last['rsi']:.1f}")
    chk("Opening range", ltp > orh, ltp < orl, f"15-min range {orl:.2f} – {orh:.2f}")
    chk("Prev day H/L", ltp > pdh, ltp < pdl, f"PDH {pdh:.2f} / PDL {pdl:.2f}")

    score = sum(c["v"] for c in checks)
    max_score = len(checks)

    tags = []
    if ltp > orh and n > 3:
        tags.append("ORB Breakout")
    if ltp < orl and n > 3:
        tags.append("ORB Breakdown")
    if ltp > pdh:
        tags.append("PDH Breakout")
    if ltp < pdl:
        tags.append("PDL Breakdown")
    st_flip = df["st_dir"].iloc[-6:].nunique() > 1
    if st_flip:
        tags.append("Supertrend Flip " + ("↑" if last["st_dir"] == 1 else "↓"))
    e_prev = (df["ema9"] - df["ema21"]).iloc[-6:]
    if (e_prev.iloc[0] < 0) and (e_prev.iloc[-1] > 0):
        tags.append("EMA Bull Cross")
    if (e_prev.iloc[0] > 0) and (e_prev.iloc[-1] < 0):
        tags.append("EMA Bear Cross")
    if rvol and rvol >= 1.5:
        tags.append("Volume Surge")
    if abs(day_open - day_low) / ltp < 0.001 and ltp > day_open:
        tags.append("Open = Low")
    if abs(day_open - day_high) / ltp < 0.001 and ltp < day_open:
        tags.append("Open = High")
    gap = (day_open - pdc) / pdc * 100
    if gap > 1:
        tags.append("Gap Up")
    if gap < -1:
        tags.append("Gap Down")

    a = last["atr"]
    if score > 0:
        sl = max(min(last["st"], ltp - 0.5 * a), ltp - 2.5 * a) if last["st_dir"] == 1 else ltp - 1.5 * a
        tgt = ltp + 2 * (ltp - sl)
    elif score < 0:
        sl = min(max(last["st"], ltp + 0.5 * a), ltp + 2.5 * a) if last["st_dir"] == -1 else ltp + 1.5 * a
        tgt = ltp - 2 * (sl - ltp)
    else:
        sl = tgt = None

    return {
        "symbol": sym, "ltp": _r(ltp), "chg": _r((ltp - pdc) / pdc * 100),
        "open": _r(day_open), "high": _r(day_high), "low": _r(day_low), "prev_close": _r(pdc),
        "vwap": _r(vwap), "rsi": _r(last["rsi"], 1), "rvol": _r(rvol),
        "score": score, "max_score": max_score, "signal": _signal(score, max_score),
        "tags": tags, "checks": checks,
        "entry": _r(ltp) if sl else None, "sl": _r(sl), "target": _r(tgt),
        "trend": "Up" if last["st_dir"] == 1 else "Down",
    }


# --------------------------------------------------------------------------
# Swing scan (daily candles)
# --------------------------------------------------------------------------

def scan_swing_one(sym, df):
    if len(df) < 60:
        return None
    df = df.copy()
    c = df["Close"]
    df["e20"], df["e50"], df["e200"] = ema(c, 20), ema(c, 50), ema(c, 200)
    df["rsi"] = rsi(c)
    df["atr"] = atr(df)
    df["macd"], df["macds"], df["macdh"] = macd(c)
    df["adx"], df["pdi"], df["mdi"] = adx(df)
    df["vavg"] = df["Volume"].rolling(20).mean()
    df["hh20"] = df["High"].rolling(20).max().shift()
    df["ll20"] = df["Low"].rolling(20).min().shift()

    last, prev = df.iloc[-1], df.iloc[-2]
    ltp = last["Close"]
    hi52 = df["High"].iloc[-252:].max()
    lo52 = df["Low"].iloc[-252:].min()
    vol_ratio = last["Volume"] / last["vavg"] if last["vavg"] else None
    chg_1w = (ltp / c.iloc[-6] - 1) * 100 if len(c) > 6 else None
    chg_1m = (ltp / c.iloc[-22] - 1) * 100 if len(c) > 22 else None

    checks = []

    def chk(label, bull, bear, detail):
        checks.append({"label": label, "v": 1 if bull else (-1 if bear else 0), "detail": detail})

    chk("Price vs EMA 20", ltp > last["e20"], ltp < last["e20"], f"EMA20 {last['e20']:.2f}")
    chk("EMA 20/50", last["e20"] > last["e50"], last["e20"] < last["e50"],
        f"EMA20 {last['e20']:.2f} vs EMA50 {last['e50']:.2f}")
    chk("EMA 50/200", last["e50"] > last["e200"], last["e50"] < last["e200"],
        f"EMA50 {last['e50']:.2f} vs EMA200 {last['e200']:.2f}")
    chk("MACD", last["macd"] > last["macds"], last["macd"] < last["macds"],
        f"MACD {last['macd']:.2f} / Signal {last['macds']:.2f}")
    chk("RSI", 50 < last["rsi"] < 75, last["rsi"] < 45, f"RSI(14) {last['rsi']:.1f}")
    chk("ADX / DI", last["adx"] > 20 and last["pdi"] > last["mdi"], last["adx"] > 20 and last["mdi"] > last["pdi"],
        f"ADX {last['adx']:.1f}, +DI {last['pdi']:.1f}, −DI {last['mdi']:.1f}")

    score = sum(x["v"] for x in checks)
    max_score = len(checks)

    tags = []
    uptrend = ltp > last["e50"] > last["e200"]
    downtrend = ltp < last["e50"] < last["e200"]
    if ltp > last["hh20"] and vol_ratio and vol_ratio > 1.3:
        tags.append("20D Breakout + Vol")
    elif ltp > last["hh20"]:
        tags.append("20D Breakout")
    if ltp < last["ll20"]:
        tags.append("20D Breakdown")
    recent_low = df["Low"].iloc[-3:].min()
    if uptrend and recent_low <= last["e20"] * 1.015 and ltp > last["e20"] and 40 <= last["rsi"] <= 60:
        tags.append("Pullback to EMA20")
    hist = df["macdh"].iloc[-4:]
    if hist.iloc[0] < 0 < hist.iloc[-1]:
        tags.append("MACD Bull Cross")
    if hist.iloc[0] > 0 > hist.iloc[-1]:
        tags.append("MACD Bear Cross")
    gx = (df["e50"] - df["e200"]).iloc[-10:]
    if gx.iloc[0] < 0 < gx.iloc[-1]:
        tags.append("Golden Cross")
    if gx.iloc[0] > 0 > gx.iloc[-1]:
        tags.append("Death Cross")
    if ltp >= hi52 * 0.97:
        tags.append("Near 52W High")
    if ltp <= lo52 * 1.03:
        tags.append("Near 52W Low")
    if prev["rsi"] < 30 <= last["rsi"]:
        tags.append("RSI Oversold Bounce")
    if last["rsi"] > 75:
        tags.append("Overbought")
    rng = (df["High"] - df["Low"]).iloc[-7:]
    if rng.iloc[-1] == rng.min():
        tags.append("NR7 (Squeeze)")
    if uptrend:
        tags.append("Uptrend")
    if downtrend:
        tags.append("Downtrend")

    a = last["atr"]
    if score > 0:
        swing_low = df["Low"].iloc[-10:].min()
        sl = max(min(swing_low, ltp - a), ltp - 3 * a)
        tgt = ltp + 2 * (ltp - sl)
    elif score < 0:
        swing_high = df["High"].iloc[-10:].max()
        sl = min(max(swing_high, ltp + a), ltp + 3 * a)
        tgt = ltp - 2 * (sl - ltp)
    else:
        sl = tgt = None

    return {
        "symbol": sym, "ltp": _r(ltp), "chg": _r((ltp / prev["Close"] - 1) * 100),
        "chg_1w": _r(chg_1w), "chg_1m": _r(chg_1m),
        "rsi": _r(last["rsi"], 1), "adx": _r(last["adx"], 1), "rvol": _r(vol_ratio),
        "hi52": _r(hi52), "lo52": _r(lo52), "from_hi52": _r((ltp / hi52 - 1) * 100),
        "score": score, "max_score": max_score, "signal": _signal(score, max_score),
        "tags": tags, "checks": checks,
        "entry": _r(ltp) if sl else None, "sl": _r(sl), "target": _r(tgt),
        "trend": "Up" if uptrend else ("Down" if downtrend else "Sideways"),
    }


# --------------------------------------------------------------------------
# Cached scan runner
# --------------------------------------------------------------------------

_cache = {}
_lock = threading.Lock()
CACHE_TTL = {"intraday": 60, "swing": 900}
MAX_STALE = {"intraday": 20 * 60, "swing": 6 * 3600}  # older than this -> make the user wait for fresh data
_refreshing = set()
CONFIG = {"intraday": ("5d", "5m", scan_intraday_one), "swing": ("2y", "1d", scan_swing_one)}


def run_scan(mode, universe="nifty50", force=False):
    key = (mode, universe)
    with _lock:
        hit = _cache.get(key)
        if hit and not force:
            age = time.time() - hit["ts"]
            if age < CACHE_TTL[mode]:
                return hit["data"]
            if age < MAX_STALE[mode]:  # answer instantly with the last result, refresh in the background
                if key not in _refreshing:
                    _refreshing.add(key)
                    threading.Thread(target=_bg_refresh, args=(mode, universe, key), daemon=True).start()
                return {**hit["data"], "stale": True}

    with _dl_lock:  # one scan at a time; a request that waited gets the fresh cached result
        with _lock:
            hit = _cache.get(key)
            if hit and time.time() - hit["ts"] < 5:
                return hit["data"]
        return _compute_scan(mode, universe, key)


def _bg_refresh(mode, universe, key):
    try:
        with _dl_lock:
            _compute_scan(mode, universe, key)
    except Exception as e:
        print(f"background {mode} refresh failed:", e)
    finally:
        with _lock:
            _refreshing.discard(key)


def _compute_scan(mode, universe, key):
    period, interval, fn = CONFIG[mode]
    symbols = UNIVERSES[universe]["symbols"]
    tickers = [s + ".NS" for s in symbols]
    t0 = time.time()
    data = _download(tickers, period, interval)
    results, failed = [], []
    for s, t in zip(symbols, tickers):
        df = data.get(t)
        try:
            r = fn(s, df) if df is not None else None
        except Exception as e:  # keep the scan alive if one symbol misbehaves
            print(f"[{mode}] {s}: {e}")
            r = None
        (results.append(r) if r else failed.append(s))

    status, label = market_status()
    payload = {
        "mode": mode, "universe": universe, "universe_name": UNIVERSES[universe]["name"],
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed": round(time.time() - t0, 1), "source": data_source(),
        "market": {"status": status, "label": label},
        "indices": get_indices(),
        "results": results, "failed": failed,
    }
    with _lock:
        _cache[key] = {"ts": time.time(), "data": payload}
    return payload


_idx_cache = {"ts": 0, "data": []}


def get_indices():
    if time.time() - _idx_cache["ts"] < 60:
        return _idx_cache["data"]
    out = []
    try:
        data = _download(list(INDEX_TICKERS.values()), "5d", "5m")
        for name, t in INDEX_TICKERS.items():
            df = data.get(t)
            if df is None or df.empty:
                continue
            dates = df.index.date
            days = sorted(set(dates))
            if len(days) < 2:
                continue
            today = df[dates == days[-1]]["Close"]
            prev = df[dates == days[-2]]["Close"].iloc[-1]
            l = today.iloc[-1]
            step = max(1, len(today) // 60)
            out.append({"name": name, "ltp": _r(l), "chg": _r((l / prev - 1) * 100), "pts": _r(l - prev),
                        "prev": _r(prev), "spark": [_r(v) for v in today.values[::step]] + [_r(l)]})
    except Exception as e:
        print("index fetch failed:", e)
    if out:
        _idx_cache.update(ts=time.time(), data=out)
    return out


# timeframe -> (yahoo interval, period, label, intraday?)
TIMEFRAMES = {
    "1m": ("1m", "5d", "1 min", True),
    "5m": ("5m", "1mo", "5 min", True),
    "15m": ("15m", "60d", "15 min", True),
    "30m": ("30m", "60d", "30 min", True),
    "1h": ("60m", "1y", "1 hour", True),
    "1d": ("1d", "5y", "Daily", False),
    "1wk": ("1wk", "10y", "Weekly", False),
}


def _series(ts, s, d=2, gaps=False):
    if gaps:  # whitespace points break the line instead of bridging across NaNs
        return [{"time": t, "value": _r(v, d)} if _r(v, d) is not None else {"time": t} for t, v in zip(ts, s.values)]
    return [{"time": t, "value": _r(v, d)} for t, v in zip(ts, s.values) if _r(v, d) is not None]


def chart_data(symbol, tf="1d"):
    if tf not in TIMEFRAMES:
        tf = "1d"
    interval, period, label, intraday = TIMEFRAMES[tf]
    t = symbol + ".NS"
    df = _download([t], period, interval).get(t)
    if df is None or df.empty:
        return None
    ts = [int(i.timestamp()) + 19800 for i in df.index]  # shift so the chart axis shows IST wall-clock time
    c = df["Close"]
    candles = [{"time": t_, "open": _r(o), "high": _r(h), "low": _r(l), "close": _r(cl)}
               for t_, o, h, l, cl in zip(ts, df["Open"], df["High"], df["Low"], c)]
    vol = [{"time": t_, "value": float(v), "up": bool(cl >= o)}
           for t_, v, o, cl in zip(ts, df["Volume"], df["Open"], c)]

    overlays = {}
    if intraday:
        overlays["EMA 9"] = _series(ts, ema(c, 9))
        overlays["EMA 21"] = _series(ts, ema(c, 21))
        g = pd.Series(df.index.date, index=df.index)
        tp = (df["High"] + df["Low"] + c) / 3
        if tf != "1h":
            overlays["VWAP"] = _series(ts, (tp * df["Volume"]).groupby(g).cumsum() /
                                       df["Volume"].groupby(g).cumsum().replace(0, np.nan))
    else:
        overlays["EMA 20"] = _series(ts, ema(c, 20))
        overlays["EMA 50"] = _series(ts, ema(c, 50))
        overlays["EMA 200"] = _series(ts, ema(c, 200))
    st, sd = supertrend(df, 10, 3)
    overlays["Supertrend"] = [{"time": t_, "value": _r(v), "up": bool(dd == 1)}
                              for t_, v, dd in zip(ts[10:], st.values[10:], sd.values[10:])]
    mid = c.rolling(20).mean()
    sd20 = c.rolling(20).std()
    overlays["BB upper"] = _series(ts, mid + 2 * sd20)
    overlays["BB mid"] = _series(ts, mid)
    overlays["BB lower"] = _series(ts, mid - 2 * sd20)

    m, ms, mh = macd(c)
    osc = {
        "rsi": _series(ts, rsi(c), 1),
        "macd": _series(ts, m), "macd_signal": _series(ts, ms),
        "macd_hist": [{"time": t_, "value": _r(v)} for t_, v in zip(ts, mh.values) if _r(v) is not None],
    }

    # Supertrend flips as buy/sell markers
    markers = []
    dirs = sd.values
    for i in range(1, len(dirs)):
        if dirs[i] != dirs[i - 1]:
            markers.append({"time": ts[i], "side": "buy" if dirs[i] == 1 else "sell", "price": _r(c.iloc[i])})

    levels = {}
    if intraday:
        dates = sorted(set(df.index.date))
        if len(dates) >= 2:
            pdf = df[df.index.date == dates[-2]]
            levels = {"PDH": _r(pdf["High"].max()), "PDL": _r(pdf["Low"].min()), "PDC": _r(pdf["Close"].iloc[-1])}
    else:
        last = df.iloc[-260:] if tf == "1d" else df.iloc[-52:]
        levels = {"52W H": _r(last["High"].max()), "52W L": _r(last["Low"].min())}

    return {"symbol": symbol, "tf": tf, "label": label, "intraday": intraday,
            "candles": candles, "volume": vol, "overlays": overlays, "osc": osc,
            "markers": markers[-30:], "levels": levels}
