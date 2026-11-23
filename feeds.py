"""Picks the live data feed: Groww when connected, else None (the app then uses Yahoo).

A feed module offers: configured(), connected(), download(tickers, period, interval),
option_chain(key, kind, strikes, now) and option_candles(handle). Add another broker by
writing a module with those functions and listing it in FEEDS.
"""
import groww_data

FEEDS = [groww_data]


def active():
    for f in FEEDS:
        if f.configured() and f.connected():
            return f
    return None


def name():
    f = active()
    return f.NAME if f else "yahoo"
