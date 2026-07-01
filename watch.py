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


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        return json.load(fh)


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


def format_message(mode, drops, top_n, min_drop_pct):
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
        lines.append(f"{i:>2}. `{d['ticker']:<6}` {d['pct']:+6.2f}%   ${d['price']:,.2f}")
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
    top_n = int(cfg.get("top_n", 12))

    if mode == "daily":
        # Pull ~2 weeks so holidays/half-days never leave us short of 2 closes.
        closes = fetch_closes(tickers, period="15d")
        drops = compute_drops(closes, lookback_rows=1)
        min_drop = float(cfg.get("daily_min_drop_pct", 0.0))
    else:
        # One trading week back = 5 rows; pull ~1 month for a safety margin.
        closes = fetch_closes(tickers, period="1mo")
        drops = compute_drops(closes, lookback_rows=5)
        min_drop = float(cfg.get("weekly_min_drop_pct", 0.0))

    message = format_message(mode, drops, top_n, min_drop)
    print(message, "\n")
    send_to_discord(message)


if __name__ == "__main__":
    main()
