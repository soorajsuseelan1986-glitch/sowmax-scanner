"""Groww Trade API data source (https://groww.in/trade-api).

Login is automatic: with a TOTP key the server makes its own access token, so there's no daily
login click. Env vars:
  GROWW_API_KEY      the TOTP token (or an approval-type API key)
  GROWW_TOTP_SECRET  the TOTP secret shown next to it        <- recommended, fully automatic
  GROWW_API_SECRET   only for approval-type keys (needs a daily "approve" on groww.in/trade-api/api-keys)
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from datetime import date, datetime, timedelta

import pandas as pd
import requests

IST = "Asia/Kolkata"
NAME = "groww"
API = "https://api.groww.in/v1"
INSTRUMENT_CSV = "https://growwapi-assets.groww.in/instruments/instrument.csv"
def _env(name):
    """Env value with pasted-in junk removed: spaces, line breaks and surrounding quotes."""
    return "".join(os.environ.get(name, "").split()).strip("'\"")


API_KEY = _env("GROWW_API_KEY")
TOTP_SECRET = _env("GROWW_TOTP_SECRET").upper().rstrip("=")
API_SECRET = _env("GROWW_API_SECRET")
# Common mix-up: the long "eyJ..." token pasted as the secret and the short secret as the key.
if TOTP_SECRET.upper().startswith("EYJ") and not API_KEY.startswith("eyJ"):
    API_KEY, TOTP_SECRET = _env("GROWW_TOTP_SECRET"), API_KEY.upper().rstrip("=")
    print("groww: GROWW_API_KEY and GROWW_TOTP_SECRET look swapped; using them the other way round")


def _check_secret():
    bad = sorted({c for c in TOTP_SECRET if c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"})
    if bad:
        return (f"GROWW_TOTP_SECRET isn't a valid TOTP secret: it contains {' '.join(bad[:8])!s} "
                f"({len(TOTP_SECRET)} characters). A secret has only letters A-Z and digits 2-7, "
                "about 32 characters. Check you pasted the TOTP *secret*, not the token.")
    if not API_KEY.startswith("eyJ"):
        return "GROWW_API_KEY doesn't look like a Groww TOTP token (it should start with eyJ)."
    return None

INDEX_MAP = {  # Yahoo tickers used in the app -> Groww symbol
    "^NSEI": "NSE-NIFTY", "^NSEBANK": "NSE-BANKNIFTY", "^CNXIT": "NSE-NIFTYIT",
    "^INDIAVIX": "NSE-INDIAVIX", "^BSESN": "BSE-SENSEX",
}
UNDERLYING = {"NIFTY": ("NSE", "NIFTY"), "BANKNIFTY": ("NSE", "BANKNIFTY"), "SENSEX": ("BSE", "SENSEX")}
INTERVALS = {"1m": "1minute", "5m": "5minute", "15m": "15minute", "30m": "30minute",
             "60m": "1hour", "1d": "1day", "1wk": "1week"}
MAX_DAYS = {"1minute": 30, "5minute": 30, "15minute": 90, "30minute": 90, "1hour": 180, "1day": 180, "1week": 180}
LEGACY_MINUTES = {"1day": 1440, "1week": 10080}  # older endpoint: whole range in one request
PERIOD_DAYS = {"5d": 10, "1mo": 29, "60d": 60, "1y": 365, "2y": 730, "5y": 1825, "10y": 3650}
EARLIEST = datetime(2020, 1, 1)  # Groww history starts in 2020


class NotConnected(Exception):
    pass


class GrowwError(Exception):
    pass


_state = {"token": None, "since": 0.0, "failed_at": 0.0, "error": None}
_auth_lock = threading.Lock()
_call_lock = threading.Lock()
_last_call = [0.0]


def configured():
    return bool(API_KEY and (TOTP_SECRET or API_SECRET))


def _last_reset():
    now = pd.Timestamp.now(tz=IST)
    six = now.normalize() + pd.Timedelta(hours=6)
    return (six if now >= six else six - pd.Timedelta(days=1)).timestamp()


def _headers(tok):
    return {"Authorization": "Bearer " + tok, "Content-Type": "application/json",
            "x-api-version": "1.0", "x-client-id": "growwapi"}


def _token(force=False):
    """Access token, re-made each day after 06:00 IST (and on auth errors). Auth is limited to
    150 requests/day, so after a failure we wait 10 minutes before trying again."""
    with _auth_lock:
        if _state["token"] and not force and _state["since"] >= _last_reset():
            return _state["token"]
        if time.time() - _state["failed_at"] < 600:
            raise NotConnected()
        try:
            if TOTP_SECRET:
                import pyotp
                problem = _check_secret()
                if problem:
                    raise GrowwError(problem)
                body = {"key_type": "totp", "totp": pyotp.TOTP(TOTP_SECRET).now()}
            else:
                import hashlib
                ts = int(time.time())
                body = {"key_type": "approval", "timestamp": ts,
                        "checksum": hashlib.sha256((API_SECRET + str(ts)).encode()).hexdigest()}
            r = requests.post(API + "/token/api/access", headers=_headers(API_KEY), json=body, timeout=15)
            if not r.ok:
                try:
                    msg = r.json().get("error", {}).get("displayMessage") or r.text[:200]
                except Exception:
                    msg = r.text[:200]
                raise GrowwError(f"token {r.status_code}: {msg}")
            _state.update(token=r.json()["token"], since=time.time(), failed_at=0.0, error=None)
            return _state["token"]
        except Exception as e:
            print("groww login failed:", e)
            _state.update(token=None, failed_at=time.time(), error=str(e))
            raise NotConnected()


def connected():
    return configured() and time.time() - _state["failed_at"] >= 600


def status():
    if not configured():
        return {"configured": False, "connected": False}
    try:
        _token()
        return {"configured": True, "connected": True}
    except NotConnected:
        return {"configured": True, "connected": False, "error": _state["error"]}


def _get(path, params=None, retry=True):
    tok = _token()
    with _call_lock:  # stay well under Groww's per-second limits
        wait = 0.12 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    r = requests.get(API + path, params=params, headers=_headers(tok), timeout=20)
    if r.status_code in (401, 403) and retry:
        _token(force=True)
        return _get(path, params, retry=False)
    if r.status_code == 429 and retry:
        time.sleep(1.0)
        return _get(path, params, retry=False)
    try:
        j = r.json()
    except ValueError:
        raise GrowwError(f"{path}: HTTP {r.status_code}")
    if j.get("status") == "FAILURE" or not r.ok:
        err = j.get("error") or {}
        raise GrowwError(f"{path}: {err.get('code', r.status_code)} {err.get('message', '')}".strip())
    return j.get("payload", j)


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------

def _symbol(ticker):
    if ticker in INDEX_MAP:
        return INDEX_MAP[ticker]
    if ticker.endswith(".NS"):
        return "NSE-" + ticker[:-3]
    return None


_last_raw = {}  # symbol -> a few raw candle rows, for /api/debug


def _times(t):
    """Groww timestamps -> IST DatetimeIndex. Handles epoch s/ms and ISO strings with or without offset."""
    num = pd.to_numeric(t, errors="coerce")
    if num.notna().all():
        unit = "ms" if num.max() > 1e11 else "s"
        return pd.DatetimeIndex(pd.to_datetime(num, unit=unit, utc=True)).tz_convert(IST)
    idx = pd.DatetimeIndex(pd.to_datetime(t.astype(str), errors="coerce"))
    return idx.tz_localize(IST) if idx.tz is None else idx.tz_convert(IST)


def _frame(rows, daily, symbol=""):
    if not rows:
        return None
    if symbol:
        _last_raw[symbol] = {"first": rows[:2], "last": rows[-3:], "count": len(rows)}
    df = pd.DataFrame([list(r[:6]) + [None] * (6 - len(r[:6])) for r in rows],
                      columns=["t", "Open", "High", "Low", "Close", "Volume"])
    idx = _times(df["t"])
    df.index = idx.normalize() if daily else idx
    df = df[["Open", "High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
    df = df[df.index.notna()].dropna(subset=["Close"])
    for c in ("Open", "High", "Low"):  # a missing O/H/L must not leave holes in the candles
        df[c] = df[c].fillna(df["Close"])
    df["High"] = df[["Open", "High", "Low", "Close"]].max(axis=1)
    df["Low"] = df[["Open", "High", "Low", "Close"]].min(axis=1)
    df["Volume"] = df["Volume"].fillna(0)
    if not daily:  # keep the regular session only (Groww also sends flat post-close candles)
        mins = df.index.hour * 60 + df.index.minute
        df = df[(mins >= 9 * 60 + 15) & (mins < 15 * 60 + 30)]
    return df[~df.index.duplicated(keep="last")].sort_index()


def candles(groww_symbol, interval, days, segment="CASH"):
    exch = groww_symbol.split("-", 1)[0]
    end = pd.Timestamp.now(tz=IST).tz_localize(None).to_pydatetime()
    start = max(end - timedelta(days=days), EARLIEST)
    daily = interval in ("1day", "1week")
    fmt = "%Y-%m-%d %H:%M:%S"

    if interval in LEGACY_MINUTES:  # one request for years of daily/weekly data
        try:
            p = _get("/historical/candle/range", {
                "exchange": exch, "segment": segment, "trading_symbol": groww_symbol.split("-", 1)[1],
                "start_time": start.strftime(fmt), "end_time": end.strftime(fmt),
                "interval_in_minutes": LEGACY_MINUTES[interval]})
            df = _frame(p.get("candles") or [], daily, groww_symbol)
            if df is not None and len(df):
                return df
        except NotConnected:
            raise
        except Exception as e:
            print(f"[groww] legacy candles {groww_symbol}: {e}; using chunked endpoint")

    rows, a, step = [], start, timedelta(days=MAX_DAYS[interval])
    while a < end:
        b = min(a + step, end)
        p = _get("/historical/candles", {
            "exchange": exch, "segment": segment, "groww_symbol": groww_symbol,
            "start_time": a.strftime(fmt), "end_time": b.strftime(fmt), "candle_interval": interval})
        rows += p.get("candles") or []
        a = b + timedelta(seconds=1)
    return _frame(rows, daily, groww_symbol)


def download(tickers, period, interval):
    """Same contract as scanner._download: {ticker: DataFrame}; missing tickers are left out."""
    gint = INTERVALS[interval]
    days = PERIOD_DAYS.get(period, 365)

    def fetch(t):
        sym = _symbol(t)
        return candles(sym, gint, days) if sym else None

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
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(tickers)))) as ex:
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

_inst = {"day": None, "df": None}
_inst_lock = threading.Lock()


def _options_table():
    """Index option contracts (NIFTY / BANKNIFTY / SENSEX) from Groww's public instrument file, daily."""
    with _inst_lock:
        if _inst["day"] != date.today() or _inst["df"] is None:
            cols = ["exchange", "trading_symbol", "groww_symbol", "instrument_type", "segment",
                    "underlying_symbol", "expiry_date", "strike_price", "lot_size"]
            parts = []
            for chunk in pd.read_csv(INSTRUMENT_CSV, dtype=str, usecols=cols, chunksize=200_000):
                parts.append(chunk[(chunk["segment"] == "FNO") & chunk["instrument_type"].isin(["CE", "PE"])
                                   & chunk["underlying_symbol"].isin([u for _, u in UNDERLYING.values()])])
            df = pd.concat(parts, ignore_index=True)
            df["expiry_date"] = pd.to_datetime(df["expiry_date"]).dt.date
            df["strike_price"] = df["strike_price"].astype(float)
            _inst.update(day=date.today(), df=df)
        return _inst["df"]


def option_chain(key, kind, strikes, now):
    """Nearest-expiry contracts for the given strikes, with live LTPs.

    Returns {"expiry": date, "lot": int, "rows": {strike: {"symbol", "handle", "ltp"}}} or None.
    """
    exch, und = UNDERLYING[key]
    df = _options_table()
    df = df[(df["exchange"] == exch) & (df["underlying_symbol"] == und) & (df["instrument_type"] == kind)]
    today = now.date()
    cutoff_passed = now.hour * 60 + now.minute > 15 * 60 + 30
    exps = sorted(e for e in df["expiry_date"].unique() if e > today or (e == today and not cutoff_passed))
    if not exps:
        return None
    exp = exps[0]
    df = df[df["expiry_date"] == exp]
    chain = _get(f"/option-chain/exchange/{exch}/underlying/{und}", {"expiry_date": exp.isoformat()})
    by_strike = {float(k): v for k, v in (chain.get("strikes") or {}).items()}
    want = {float(s) for s in strikes}
    rows = {}
    for _, r in df[df["strike_price"].isin(want)].iterrows():
        q = (by_strike.get(r["strike_price"]) or {}).get(kind) or {}
        if q.get("ltp"):
            rows[r["strike_price"]] = {"symbol": r["trading_symbol"], "handle": r["groww_symbol"], "ltp": float(q["ltp"])}
    if not rows:
        return None
    lot = int(float(df["lot_size"].iloc[0] or 0))
    return {"expiry": exp, "lot": lot, "rows": rows}


def option_candles(handle):
    return candles(handle, "5minute", 10, segment="FNO")
