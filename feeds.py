"""Picks the live data feed: Groww first, then Zerodha Kite, else None (the app then uses Yahoo).

Each feed module offers the same functions: connected(), download(tickers, period, interval),
option_chain(key, kind, strikes, now) and option_candles(handle).
"""
import groww_data
import kite_data

FEEDS = [groww_data, kite_data]


def active():
    for f in FEEDS:
        if f.configured() and f.connected():
            return f
    return None


def name():
    f = active()
    return f.NAME if f else "yahoo"
