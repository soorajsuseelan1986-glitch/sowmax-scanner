"""Zerodha Kite Connect data source.

When you're connected (one Zerodha login per day, via the "Connect Zerodha" button), all candles,
index levels and option premiums come from Kite. When you're not, the app falls back to Yahoo.

Needs env vars KITE_API_KEY and KITE_API_SECRET (from https://developers.kite.trade), and the app's
Redirect URL in the Kite developer console set to  https://<your-site>/kite/callback
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import date, datetime, timedelta

import pandas as pd

IST = "Asia/Kolkata"
NAME = "kite"
API_KEY = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")

# Yahoo index tickers used across the app -> (Kite exchange, tradingsymbol)
INDEX_MAP = {
    "^NSEI": ("NSE", "NIFTY 50"),
    "^NSEBANK": ("NSE", "NIFTY BANK"),
    "^CNXIT": ("NSE", "NIFTY IT"),
    "^INDIAVIX": ("NSE", "INDIA VIX"),
    "^BSESN": ("BSE", "SENSEX"),
}
INTERVALS = {"1m": "minute", "5m": "5minute", "15m": "15minute", "30m": "30minute",
             "60m": "60minute", "1d": "day", "1wk": "day"}
MAX_DAYS = {"minute": 60, "5minute": 100, "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000}
PERIOD_DAYS = {"5d": 10, "1mo": 31, "60d": 60, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650}


class NotConnected(Exception):
    pass


_lock = threading.RLock()
_state = {"kite": None, "token": None, "user": None, "since": None}
_bad_tokens = set()


def configured():
    return bool(API_KEY and API_SECRET)


def connected():
    return _state["kite"] is not None


def _client():
    from kiteconnect import KiteConnect
    return KiteConnect(api_key=API_KEY, timeout=20)


def login_url():
    return _client().login_url()


def _use(token, user, since):
    k = _client()
    k.set_access_token(token)
    with _lock:
        _state.update(kite=k, token=token, user=user, since=since)


def connect(request_token):
    """Exchange the request_token from Kite's redirect for an access token."""
    data = _client().generate_session(request_token, api_secret=API_SECRET)
    user = data.get("user_name") or data.get("user_id") or ""
    since = time.time()
    _use(data["access_token"], user, since)
    _instruments.clear()
    return data["access_token"], user, since


def last_reset():
    """Kite access tokens expire at 06:00 IST every day."""
    now = pd.Timestamp.now(tz=IST)
    six = now.normalize() + pd.Timedelta(hours=6)
    return (six if now >= six else six - pd.Timedelta(days=1)).timestamp()


def token_fresh(since):
    return bool(since) and since >= last_reset()


def restore(token, user, since):
    """Re-attach a token saved in the user's session (the server forgets it when Render sleeps)."""
    if connected() or not token or token in _bad_tokens or not token_fresh(since):
        return False
    _use(token, user, since)
    return True


def disconnect(bad=False):
    with _lock:
        if bad and _state["token"]:
            _bad_tokens.add(_state["token"])
        _state.update(kite=None, token=None, user=None, since=None)


def status():
    if connected() and not token_fresh(_state["since"]):
        disconnect()
    return {"configured": configured(), "connected": connected(), "user": _state["user"]}


# ---------------------------------------------------------------------------
# Calls (throttled to Kite's ~3 requests/second limit)
# ---------------------------------------------------------------------------

_call_lock = threading.Lock()
_last_call = [0.0]


def _call(fn_name, *args, **kw):
    from kiteconnect import exceptions as kex
    k = _state["kite"]
    if k is None:
        raise NotConnected()
    with _call_lock:
        wait = 0.35 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    try:
        return getattr(k, fn_name)(*args, **kw)
    except kex.TokenException:
        disconnect(bad=True)
        raise NotConnected()


_instruments = {}
_inst_lock = threading.RLock()


def _inst(exchange):
    """Instrument dump for one exchange, cached for the day."""
    with _inst_lock:
        return _inst_unlocked(exchange)


def _inst_unlocked(exchange):
    key = (exchange, date.today())
    if key not in _instruments:
        for k in [k for k in _instruments if k[-1] != date.today()]:  # drop yesterday's dumps
            del _instruments[k]
        _instruments[key] = _call("instruments", exchange)
    return _instruments[key]


def _token_map(exchange):
    key = ("map", exchange, date.today())
    with _inst_lock:
        return _token_map_unlocked(exchange, key)


def _token_map_unlocked(exchange, key):
    if key not in _instruments:
        _instruments[key] = {r["tradingsymbol"]: r["instrument_token"] for r in _inst(exchange)
                             if r["segment"] in (exchange, "INDICES")}
    return _instruments[key]


def resolve(ticker):
    """Yahoo-style ticker ('RELIANCE.NS', '^NSEI') -> Kite instrument token, or None."""
    if ticker in INDEX_MAP:
        exch, sym = INDEX_MAP[ticker]
    elif ticker.endswith(".NS"):
        exch, sym = "NSE", ticker[:-3]
    else:
        return None
    return _token_map(exch).get(sym)


def candles(token, interval, days):
    """Historical candles as a DataFrame (Open/High/Low/Close/Volume, IST index)."""
    end = datetime.now()
    start = end - timedelta(days=days)
    step = timedelta(days=MAX_DAYS[interval])
    rows, a = [], start
    while a < end:
        b = min(a + step, end)
        rows += _call("historical_data", token, a, b, interval)
        a = b + timedelta(seconds=1)
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(columns={"open": "Open", "high": "High", "low": "Low",
                                            "close": "Close", "volume": "Volume"})
    idx = pd.DatetimeIndex(pd.to_datetime(df["date"]))
    df.index = idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


def download(tickers, period, interval):
    """Same contract as scanner._download: {ticker: DataFrame}; missing tickers are left out."""
    kint = INTERVALS[interval]
    days = PERIOD_DAYS.get(period, 365)

    def fetch(t):
        tok = resolve(t)
        return candles(tok, kint, days) if tok else None

    def one(t):
        try:
            return t, fetch(t)
        except NotConnected:
            raise
        except Exception as e:  # one bad symbol shouldn't kill the scan
            print(f"[{NAME}] {t}: {e}")
            return t, None

    out = {}
    # parallel: the per-second rate limit is enforced in the request helper, so this just hides network latency
    with ThreadPoolExecutor(max_workers=min(3, max(1, len(tickers)))) as ex:
        for t, df in ex.map(one, tickers):
            if df is None or df.empty:
                continue
            if interval == "1wk":
                df = df.resample("W-MON", label="left", closed="left").agg(
                    {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
            out[t] = df
    return out


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

UNDERLYING = {"NIFTY": ("NFO", "NIFTY"), "BANKNIFTY": ("NFO", "BANKNIFTY"), "SENSEX": ("BFO", "SENSEX")}


def option_candles(handle):
    return candles(handle, "5minute", 10)


def option_chain(key, kind, strikes, now):
    """Nearest-expiry contracts for the given strikes, with live LTPs.

    Returns {"expiry": date, "lot": int, "rows": {strike: {"symbol", "handle", "ltp"}}} or None.
    """
    exchange, name = UNDERLYING[key]
    opts = [r for r in _inst(exchange) if r["name"] == name and r["instrument_type"] == kind and r["expiry"]]
    if not opts:
        return None
    cutoff_passed = now.hour * 60 + now.minute > 15 * 60 + 30
    today = now.date()
    expiries = sorted({r["expiry"] for r in opts if r["expiry"] > today or (r["expiry"] == today and not cutoff_passed)})
    if not expiries:
        return None
    exp = expiries[0]
    want = {float(s) for s in strikes}
    chosen = {float(r["strike"]): r for r in opts if r["expiry"] == exp and float(r["strike"]) in want}
    if not chosen:
        return None
    keys = [f"{exchange}:{r['tradingsymbol']}" for r in chosen.values()]
    quotes = _call("ltp", keys)
    rows = {}
    for s, r in chosen.items():
        q = quotes.get(f"{exchange}:{r['tradingsymbol']}")
        if q and q.get("last_price"):
            rows[s] = {"symbol": r["tradingsymbol"], "handle": r["instrument_token"], "ltp": float(q["last_price"])}
    lot = int(next(iter(chosen.values())).get("lot_size") or 0)
    return {"expiry": exp, "lot": lot, "rows": rows} if rows else None
