"""Stock dip watcher.

Runs once a day (and once a week) via GitHub Actions, pulls daily closing
prices for a watchlist of large-cap stocks with yfinance, finds the biggest
drops, and posts a summary to a Discord channel via a webhook.

Usage:
    python watch.py daily     # top-N drops vs the previous trading day
    python watch.py weekly    # top-N drops vs ~one week (5 trading days) ago

The Discord webhook URL is read from the DISCORD_WEBHOOK environment variable
so it never has to live in the repo. If it is unset, the report is only
printed to stdout (handy for local testing).
"""

import json
import os
import sys
from pathlib import Path

import requests
import yfinance as yf

CONFIG_PATH = Path(__file__).with_name("config.json")

# Company names are cached per run so a ticker is only looked up once even if it
# shows up in more than one section of the report.
_NAME_CACHE = {}


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def get_name(ticker, overrides):
    """Return a human-friendly company name for a ticker.

    Prefers the config `names` override, then Yahoo Finance's short/long name,
    and finally falls back to None so callers can show the bare symbol.
    """
    if ticker in overrides:
        return overrides[ticker]
    if ticker in _NAME_CACHE:
        return _NAME_CACHE[ticker]
    name = None
    try:
        info = yf.Ticker(ticker).info
        name = info.get("shortName") or info.get("longName")
    except Exception:
        name = None  # network hiccup / unknown symbol — fall back to the ticker
    _NAME_CACHE[ticker] = name
    return name


def label(ticker, overrides):
    """Format a ticker as 'Company (TICKER)', or just 'TICKER' if no name."""
    name = get_name(ticker, overrides)
    return f"{name} ({ticker})" if name else ticker


def fetch_closes(tickers, period):
    """Return a DataFrame of daily closes (columns = tickers, index = dates)."""
    data = yf.download(
        tickers,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    # With multiple tickers yfinance returns a column-multiindex; "Close" is the
    # top level. With a single ticker it returns a flat frame.
    closes = data["Close"] if "Close" in data else data
    if hasattr(closes, "to_frame") and closes.ndim == 1:
        closes = closes.to_frame()
    return closes.dropna(axis=1, how="all")


def compute_drops(closes, lookback_rows):
    """Compare the latest close to the close `lookback_rows` trading days back.

    Returns a list of dicts sorted from biggest drop to smallest, containing
    only stocks that actually fell.
    """
    drops = []
    for ticker in closes.columns:
        series = closes[ticker].dropna()
        if len(series) <= lookback_rows:
            continue  # not enough history for this ticker
        latest = float(series.iloc[-1])
        prior = float(series.iloc[-1 - lookback_rows])
        if prior <= 0:
            continue
        pct = (latest - prior) / prior * 100.0
        if pct < 0:  # only care about drops
            drops.append({"ticker": ticker, "pct": pct, "price": latest})
    drops.sort(key=lambda d: d["pct"])  # most negative first
    return drops


def compute_changes(closes, lookback_rows, tickers):
    """Like compute_drops, but for a specific list of tickers and keeping every
    move — up or down. Preserves the order the tickers were given in, and notes
    any that couldn't be priced.
    """
    changes = []
    for ticker in tickers:
        if ticker not in closes.columns:
            changes.append({"ticker": ticker, "pct": None, "price": None})
            continue
        series = closes[ticker].dropna()
        if len(series) <= lookback_rows:
            changes.append({"ticker": ticker, "pct": None, "price": None})
            continue
        latest = float(series.iloc[-1])
        prior = float(series.iloc[-1 - lookback_rows])
        if prior <= 0:
            changes.append({"ticker": ticker, "pct": None, "price": None})
            continue
        pct = (latest - prior) / prior * 100.0
        changes.append({"ticker": ticker, "pct": pct, "price": latest})
    return changes


def format_watching(mode, changes, names):
    if not changes:
        return None
    span = "vs previous close" if mode == "daily" else "vs ~1 week ago"
    lines = [f"\n👀 **Watching** — {span}"]
    for c in changes:
        name = label(c["ticker"], names)
        if c["pct"] is None:
            lines.append(f" ⬜ {name} (no data)")
        else:
            mark = "❌" if c["pct"] < 0 else "✅"
            lines.append(f" {mark} {name} {c['pct']:+.2f}%   ${c['price']:,.2f}")
    return "\n".join(lines)


def format_message(mode, drops, top_n, min_drop_pct, names):
    date = None
    filtered = [d for d in drops if d["pct"] <= -abs(min_drop_pct)]
    top = filtered[:top_n]

    if mode == "daily":
        header = "📉 **Daily dip report** — top drops vs previous close"
        empty = "No stocks in the watchlist dropped today. 📈"
    else:
        header = "📉 **Weekly dip report** — top drops vs ~1 week ago"
        empty = "No stocks in the watchlist dropped over the past week. 📈"

    if not top:
        return f"{header}\n{empty}"

    lines = [header]
    for i, d in enumerate(top, 1):
        name = label(d["ticker"], names)
        lines.append(f"{i:>2}. ❌ {name} {d['pct']:+.2f}%   ${d['price']:,.2f}")
    return "\n".join(lines)


def send_to_discord(message):
    url = os.environ.get("DISCORD_WEBHOOK", "").strip()
    if not url:
        print("DISCORD_WEBHOOK not set — printing report instead of sending:\n")
        print(message)
        return
    resp = requests.post(url, json={"content": message}, timeout=30)
    resp.raise_for_status()
    print("Report sent to Discord.")


def main():
    # Windows consoles default to cp1252, which can't encode the emoji in the
    # report. GitHub Actions (Linux) is already UTF-8; this makes local runs work.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if mode not in ("daily", "weekly"):
        sys.exit(f"Unknown mode {mode!r}. Use 'daily' or 'weekly'.")

    cfg = load_config()
    tickers = cfg["tickers"]
    watching = cfg.get("watching", [])
    names = cfg.get("names", {})
    top_n = int(cfg.get("top_n", 12))

    # Fetch the watchlist and the actively-watched names together so the watched
    # ones are priced even if they aren't in the main list.
    all_tickers = list(dict.fromkeys(tickers + watching))

    if mode == "daily":
        # Pull ~2 weeks so holidays/half-days never leave us short of 2 closes.
        period = "15d"
        lookback = 1
        min_drop = float(cfg.get("daily_min_drop_pct", 0.0))
    else:
        # One trading week back = 5 rows; pull ~1 month for a safety margin.
        period = "1mo"
        lookback = 5
        min_drop = float(cfg.get("weekly_min_drop_pct", 0.0))

    closes = fetch_closes(all_tickers, period=period)

    # Rank drops over the main watchlist only, so watch-only names (an index,
    # Bitcoin, etc.) can't crowd into the top-drops list.
    main_cols = [t for t in tickers if t in closes.columns]
    drops = compute_drops(closes[main_cols], lookback_rows=lookback)
    message = format_message(mode, drops, top_n, min_drop, names)

    if watching:
        changes = compute_changes(closes, lookback, watching)
        # yfinance's big batch download occasionally drops a valid symbol; retry
        # any watched names that came back empty by fetching them on their own.
        missing = [c["ticker"] for c in changes if c["pct"] is None]
        if missing:
            retry = fetch_closes(missing, period=period)
            retried = {c["ticker"]: c for c in compute_changes(retry, lookback, missing)}
            changes = [
                retried[c["ticker"]] if c["pct"] is None else c for c in changes
            ]
        watching_section = format_watching(mode, changes, names)
        if watching_section:
            message = f"{message}\n{watching_section}"

    print(message, "\n")
    send_to_discord(message)


if __name__ == "__main__":
    main()
