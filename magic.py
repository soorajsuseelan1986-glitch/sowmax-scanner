"""Magic Indicator: composite index signal -> auto-selected option strike with Buy / SL / Targets.

When Groww is connected, premiums, expiry and lot size are LIVE from Groww, and each strike's
implied volatility is backed out of its live price. Otherwise premiums are MODELLED with Black-Scholes
(India VIX as implied volatility). Always check the live premium in your broker before trading.
"""
import math
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

import feeds
from scanner import IST, _download, _r, adx, atr, ema, macd, rsi, supertrend

RISK_FREE = 0.065

INDICES = {
    # key: yahoo ticker, strike step, lot size, IV multiplier vs India VIX, expiry rule
    "NIFTY": {"name": "NIFTY 50", "ticker": "^NSEI", "step": 50, "lot": 65, "iv_mult": 1.0,
              "expiry": ("weekly", 1), "exchange": "NSE"},
    "BANKNIFTY": {"name": "BANK NIFTY", "ticker": "^NSEBANK", "step": 100, "lot": 30, "iv_mult": 1.15,
                  "expiry": ("monthly", 1), "exchange": "NSE"},
    "SENSEX": {"name": "SENSEX", "ticker": "^BSESN", "step": 100, "lot": 20, "iv_mult": 1.0,
               "expiry": ("weekly", 3), "exchange": "BSE"},
}
VIX = "^INDIAVIX"

# Magic score weights (sum = 100)
WEIGHTS = {"Supertrend": 25, "VWAP": 20, "EMA 9/21": 15, "MACD momentum": 15, "ADX / DI": 15, "RSI": 10}
THRESHOLD = 40


# --------------------------------------------------------------------------
# Black-Scholes
# --------------------------------------------------------------------------

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs(S, K, T, iv, kind):
    """Black-Scholes price and delta. T in years."""
    T = max(T, 1 / (365 * 24 * 4))  # floor at ~15 minutes
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (RISK_FREE + iv * iv / 2) * T) / sq
    d2 = d1 - sq
    if kind == "CE":
        return S * _ncdf(d1) - K * math.exp(-RISK_FREE * T) * _ncdf(d2), _ncdf(d1)
    return K * math.exp(-RISK_FREE * T) * _ncdf(-d2) - S * _ncdf(-d1), _ncdf(d1) - 1


def implied_vol(price, S, K, T, kind):
    """Bisection for the IV that reproduces a live premium; None if the price is below intrinsic."""
    lo, hi = 0.01, 3.0
    if not (bs(S, K, T, lo, kind)[0] <= price <= bs(S, K, T, hi, kind)[0]):
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, kind)[0] < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _live_chain(key, cfg, spot, kind, now):
    feed = feeds.active()
    if not feed:
        return None
    try:
        atm = round(spot / cfg["step"]) * cfg["step"]
        chain = feed.option_chain(key, kind, [atm + k * cfg["step"] for k in range(-5, 6)], now)
        if chain:
            chain["feed"] = feed
        return chain
    except Exception as e:  # fall back to the model
        print(f"live option chain ({feed.NAME}) failed:", type(e).__name__, e)
        return None


# --------------------------------------------------------------------------
# Expiry
# --------------------------------------------------------------------------

def next_expiry(rule, now):
    kind, weekday = rule  # weekday: Mon=0 ... Tue=1, Thu=3
    today = now.date()
    cutoff_passed = now.hour * 60 + now.minute > 15 * 60 + 30

    def ok(d):
        return d > today or (d == today and not cutoff_passed)

    if kind == "weekly":
        d = today + timedelta(days=(weekday - today.weekday()) % 7)
        return d if ok(d) else d + timedelta(days=7)
    # monthly: last <weekday> of the month
    for m in range(0, 3):
        y, mo = today.year + (today.month - 1 + m) // 12, (today.month - 1 + m) % 12 + 1
        last = date(y + (mo // 12), mo % 12 + 1, 1) - timedelta(days=1)
        d = last - timedelta(days=(last.weekday() - weekday) % 7)
        if ok(d):
            return d
    return today


def years_to_expiry(exp, at):
    """Trading-time aware-ish: remaining calendar time until 15:30 IST on expiry day."""
    end = pd.Timestamp(datetime(exp.year, exp.month, exp.day, 15, 30), tz=IST)
    secs = (end - at).total_seconds()
    return max(secs, 900) / (365 * 24 * 3600)


# --------------------------------------------------------------------------
# Magic score (works for index or option candles)
# --------------------------------------------------------------------------

def magic_frame(df, use_vwap=True):
    df = df.copy()
    c = df["Close"]
    df["ema9"], df["ema21"] = ema(c, 9), ema(c, 21)
    df["rsi"] = rsi(c)
    df["macd"], df["macds"], df["macdh"] = macd(c)
    df["adx"], df["pdi"], df["mdi"] = adx(df)
    df["st"], df["st_dir"] = supertrend(df, 10, 3)
    df["atr"] = atr(df)
    if use_vwap and df["Volume"].sum() > 0:
        g = pd.Series(df.index.date, index=df.index)
        tp = (df["High"] + df["Low"] + c) / 3
        df["vwap"] = (tp * df["Volume"]).groupby(g).cumsum() / df["Volume"].groupby(g).cumsum().replace(0, np.nan)
        df["vwap"] = df["vwap"].fillna(tp)
    else:
        df["vwap"] = np.nan

    comp = pd.DataFrame(index=df.index)
    comp["Supertrend"] = df["st_dir"]
    comp["EMA 9/21"] = np.sign(df["ema9"] - df["ema21"])
    h = df["macdh"]
    comp["MACD momentum"] = np.where((h > 0) & (h >= h.shift()), 1, np.where((h < 0) & (h <= h.shift()), -1, np.sign(h) * 0.5))
    comp["RSI"] = np.where(df["rsi"] > 55, 1, np.where(df["rsi"] < 45, -1, 0))
    comp["ADX / DI"] = np.where(df["adx"] > 20, np.sign(df["pdi"] - df["mdi"]), 0)
    weights = dict(WEIGHTS)
    if df["vwap"].notna().any():
        comp["VWAP"] = np.sign(c - df["vwap"])
    else:  # no volume (option candles): give VWAP's weight to Supertrend + EMA
        weights.pop("VWAP")
        weights["Supertrend"] += 10
        weights["EMA 9/21"] += 10
    raw = sum(comp[k] * w for k, w in weights.items())
    df["magic"] = raw.ewm(span=3, adjust=False).mean()
    return df, comp, weights


def magic_markers(df, ts):
    m = df["magic"].values
    out = []
    for i in range(1, len(m)):
        if m[i - 1] < THRESHOLD <= m[i]:
            out.append({"time": ts[i], "side": "buy"})
        elif m[i - 1] > -THRESHOLD >= m[i]:
            out.append({"time": ts[i], "side": "sell"})
    return out


def _ts(index):
    return [int(i.timestamp()) + 19800 for i in index]  # IST wall-clock for the chart


def _candles(df, ts):
    return [{"time": t, "open": _r(o), "high": _r(h), "low": _r(l), "close": _r(c)}
            for t, o, h, l, c in zip(ts, df["Open"], df["High"], df["Low"], df["Close"])]


def _line(ts, s):
    return [{"time": t, "value": _r(v)} for t, v in zip(ts, s.values) if _r(v) is not None]


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def clean_index(df):
    """Remove closing-auction / bad-tick spikes (common in BSE 5-min data around 15:20-15:30)."""
    df = df.copy()
    rng = (df["High"] - df["Low"]).median() or 1.0
    c = df["Close"].values.copy()
    for i in range(1, len(c) - 1):
        nb = (c[i - 1] + c[i + 1]) / 2
        if abs(c[i] - nb) > 5 * rng and abs(c[i - 1] - c[i + 1]) < 3 * rng:
            c[i] = nb
    df["Close"] = c
    df["Open"] = np.where((df["Open"] - df["Close"].shift()).abs() > 5 * rng, df["Close"].shift(), df["Open"])
    df["Open"] = df["Open"].fillna(df["Close"])
    body_hi = df[["Open", "Close"]].max(axis=1)
    body_lo = df[["Open", "Close"]].min(axis=1)
    df["High"] = np.minimum(np.maximum(df["High"], body_hi), body_hi + 3 * rng)
    df["Low"] = np.maximum(np.minimum(df["Low"], body_lo), body_lo - 3 * rng)
    return df


def _data():
    def fetch():
        tickers = [v["ticker"] for v in INDICES.values()] + [VIX]
        raw = _download(tickers, "5d", "5m")
        return {t: (clean_index(df) if t != VIX else df) for t, df in raw.items()}
    return _cached("data", 55, fetch)


def summary():
    data = _data()
    out = []
    for key, cfg in INDICES.items():
        df = data.get(cfg["ticker"])
        if df is None or len(df) < 40:
            continue
        mf, _, _ = magic_frame(df)
        days = sorted(set(mf.index.date))
        today = mf[mf.index.date == days[-1]]
        prev = mf[mf.index.date == days[-2]]["Close"].iloc[-1] if len(days) > 1 else today["Open"].iloc[0]
        ltp = today["Close"].iloc[-1]
        score = float(mf["magic"].iloc[-1])
        step = max(1, len(today) // 60)
        out.append({
            "key": key, "name": cfg["name"], "exchange": cfg["exchange"], "ltp": _r(ltp),
            "chg": _r((ltp / prev - 1) * 100), "pts": _r(ltp - prev), "score": round(score),
            "signal": "CALL" if score >= THRESHOLD else "PUT" if score <= -THRESHOLD else "WAIT",
            "spark": [_r(v) for v in today["Close"].values[::step]] + [_r(ltp)], "prev": _r(prev),
        })
    return out


def detail(key):
    cfg = INDICES[key]
    data = _data()
    df = data.get(cfg["ticker"])
    vix_df = data.get(VIX)
    if df is None or len(df) < 40:
        return None
    vix = float(vix_df["Close"].iloc[-1]) if vix_df is not None and len(vix_df) else 14.0
    iv = vix / 100 * cfg["iv_mult"]

    mf, comp, weights = magic_frame(df)
    last = mf.iloc[-1]
    spot = float(last["Close"])
    score = float(last["magic"])
    signal = "CALL" if score >= THRESHOLD else "PUT" if score <= -THRESHOLD else "WAIT"
    kind = "CE" if score >= 0 else "PE"  # when WAIT we still show the leaning side

    now = pd.Timestamp.now(tz=IST)
    live = _live_chain(key, cfg, spot, kind, now)
    exp = live["expiry"] if live else next_expiry(cfg["expiry"], now)
    lot = (live and live["lot"]) or cfg["lot"]
    T_now = years_to_expiry(exp, now)

    # Strike ladder around ATM; pick the strike whose |delta| is closest to 0.55 (slightly ITM:
    # moves well with the index but costs less theta than deep ITM).
    step = cfg["step"]
    atm = round(spot / step) * step
    ladder, ivs = [], {}
    for k in range(-5, 6):
        K = atm + k * step
        q = live["rows"].get(float(K)) if live else None
        k_iv = (q and implied_vol(q["ltp"], spot, K, T_now, kind)) or iv
        prem, delta = bs(spot, K, T_now, k_iv, kind)
        if q:
            prem = q["ltp"]
        ivs[K] = k_iv
        ladder.append({"strike": K, "premium": _r(prem), "delta": _r(abs(delta)), "live": bool(q),
                       "iv": _r(k_iv * 100, 1),
                       "moneyness": "ATM" if K == atm else ("ITM" if (K < spot) == (kind == "CE") else "OTM")})
    pick = min(ladder, key=lambda r: abs(r["delta"] - 0.55))
    K = pick["strike"]
    for r in ladder:
        r["picked"] = r["strike"] == K
    pick_q = live["rows"].get(float(K)) if live else None
    iv = ivs[K]  # the picked strike's IV (live-implied when connected) drives SL/targets and the model chart

    # Index-level stop & targets from the Magic line (Supertrend), bounded by ATR
    a = float(last["atr"])
    sign = 1 if kind == "CE" else -1
    st_level = float(last["st"])
    idx_risk = abs(spot - st_level) if (st_level - spot) * sign < 0 else 1.5 * a
    idx_risk = min(max(idx_risk, a, spot * 0.0015), spot * 0.005)
    idx_sl = spot - sign * idx_risk
    idx_t1 = spot + sign * idx_risk * 1.5
    idx_t2 = spot + sign * idx_risk * 3

    T_exit = years_to_expiry(exp, now + pd.Timedelta(minutes=45))  # assume ~45 min holding for exits
    entry = pick_q["ltp"] if pick_q else bs(spot, K, T_now, iv, kind)[0]
    sl = bs(idx_sl, K, T_exit, iv, kind)[0]
    t1 = bs(idx_t1, K, T_exit, iv, kind)[0]
    t2 = bs(idx_t2, K, T_exit, iv, kind)[0]
    # keep the option stop sensible: 12%–35% of premium
    sl = min(max(sl, entry * 0.65), entry * 0.88)
    risk = entry - sl
    t1 = max(t1, entry + 1.5 * risk)
    t2 = max(t2, entry + 3 * risk)

    # Option candles for the last 2 sessions (live from Groww, else modelled), then the Magic indicator on them
    days = sorted(set(df.index.date))
    view = df[df.index.date >= days[-2]] if len(days) > 1 else df
    odf = None
    if pick_q:
        try:
            odf = live["feed"].option_candles(pick_q["handle"])
            odf = odf[odf.index.date >= view.index[0].date()] if odf is not None else None
            if odf is not None and len(odf) < 30:  # too little history (e.g. a brand-new weekly) -> model
                odf = None
        except Exception as e:
            print("live option candles failed:", e)
            odf = None
    source = "live" if pick_q else "model"
    chart_source = "live" if odf is not None else "model"
    if odf is None:
        o_rows = []
        for t, row in view.iterrows():
            T = years_to_expiry(exp, t)
            p = lambda s: bs(s, K, T, iv, kind)[0]
            hi_src, lo_src = (row["High"], row["Low"]) if kind == "CE" else (row["Low"], row["High"])
            o_rows.append((t, p(row["Open"]), p(hi_src), p(lo_src), p(row["Close"])))
        odf = pd.DataFrame(o_rows, columns=["t", "Open", "High", "Low", "Close"]).set_index("t")
    odf = odf.copy()
    odf["Volume"] = 0  # keep VWAP out of the option score, as before
    omf, ocomp, oweights = magic_frame(odf, use_vwap=False)
    ots = _ts(omf.index)
    o_last = omf.iloc[-1]
    opt_trend = "Up" if o_last["st_dir"] == 1 else "Down"

    # index chart (last 2 sessions)
    iv_mask = mf.index.date >= (days[-2] if len(days) > 1 else days[0])
    imf = mf[iv_mask]
    its = _ts(imf.index)

    comps = []
    for name, w in weights.items():
        v = float(comp[name].iloc[-1])
        detail_txt = {
            "Supertrend": f"{'Green' if last['st_dir'] == 1 else 'Red'} · Magic line {last['st']:.2f}",
            "VWAP": f"Spot {'above' if spot > last['vwap'] else 'below'} VWAP {last['vwap']:.2f}",
            "EMA 9/21": f"EMA9 {last['ema9']:.2f} vs EMA21 {last['ema21']:.2f}",
            "MACD momentum": f"Histogram {last['macdh']:.2f} ({'rising' if mf['macdh'].iloc[-1] >= mf['macdh'].iloc[-2] else 'falling'})",
            "ADX / DI": f"ADX {last['adx']:.1f} · +DI {last['pdi']:.1f} / −DI {last['mdi']:.1f}",
            "RSI": f"RSI(14) {last['rsi']:.1f}",
        }[name]
        comps.append({"label": name, "weight": w, "v": v, "pts": round(v * w, 1), "detail": detail_txt})

    sym = f"{key} {exp.strftime('%d %b').upper()} {int(K)} {kind}"
    return {
        "key": key, "name": cfg["name"], "exchange": cfg["exchange"], "spot": _r(spot),
        "score": round(score), "signal": signal, "kind": kind, "threshold": THRESHOLD,
        "components": comps, "vix": _r(vix), "iv": _r(iv * 100, 1),
        "expiry": exp.isoformat(), "days_to_expiry": (exp - now.date()).days,
        "strike": int(K), "symbol": sym, "lot": lot, "delta": pick["delta"],
        "source": source, "chart_source": chart_source, "feed": live["feed"].NAME if live else None, "tradingsymbol": pick_q["symbol"] if pick_q else None,
        "plan": {
            "entry": _r(entry), "sl": _r(sl), "t1": _r(t1), "t2": _r(t2),
            "idx_sl": _r(idx_sl), "idx_t1": _r(idx_t1), "idx_t2": _r(idx_t2),
            "risk_lot": _r((entry - sl) * lot), "reward_lot": _r((t1 - entry) * lot),
            "trail": _r(o_last["st"]),
        },
        "option": {
            "trend": opt_trend, "score": round(float(o_last["magic"])),
            "candles": _candles(omf, ots),
            "lines": {"Magic line": [{"time": t, "value": _r(v), "up": bool(d == 1)} for t, v, d in
                                     zip(ots[10:], omf["st"].values[10:], omf["st_dir"].values[10:])],
                      "EMA 9": _line(ots, omf["ema9"]), "EMA 21": _line(ots, omf["ema21"])},
            "magic": [{"time": t, "value": _r(v, 1)} for t, v in zip(ots, omf["magic"].values)],
            "markers": magic_markers(omf, ots),
        },
        "index": {
            "candles": _candles(imf, its),
            "lines": {"Magic line": [{"time": t, "value": _r(v), "up": bool(d == 1)} for t, v, d in
                                     zip(its, imf["st"].values, imf["st_dir"].values)],
                      "EMA 9": _line(its, imf["ema9"]), "EMA 21": _line(its, imf["ema21"])},
            "magic": [{"time": t, "value": _r(v, 1)} for t, v in zip(its, imf["magic"].values)],
            "markers": magic_markers(imf, its),
            "levels": {"SL": _r(idx_sl), "T1": _r(idx_t1), "T2": _r(idx_t2)},
        },
        "ladder": ladder,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
