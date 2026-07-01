# Stock dip watch

A tiny, free stock-dip notifier. Once a day (and once a week) it checks a
watchlist of ~100 large-cap US stocks, finds the biggest price drops, and posts
a summary to a Discord channel. It runs in the cloud on GitHub Actions — your
computer doesn't need to be on.

- **Data:** [yfinance](https://pypi.org/project/yfinance/) (free, no API key)
- **Schedule:** GitHub Actions cron, after US market close
- **Alerts:** a Discord webhook
- **Cost:** free (GitHub Actions free tier; the job runs ~30s/day)

## What it reports

- **Daily** (Mon–Fri): the top `N` biggest drops vs. the previous trading day's close.
- **Weekly** (Fri): the top `N` biggest drops vs. ~one week ago (5 trading days).

`N` defaults to **12** and is configurable.

## One-time setup

### 1. Create a Discord webhook
In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, pick the
channel you want alerts in, then **Copy Webhook URL**.

### 2. Put your repo on GitHub and add the webhook as a secret
Push this folder to a GitHub repo, then:
**Repo → Settings → Secrets and variables → Actions → New repository secret**
- **Name:** `DISCORD_WEBHOOK`
- **Value:** the webhook URL you copied

> Keep it in Secrets, never in the code. `.gitignore` already excludes `.env`.

### 3. Done
GitHub will run it automatically on schedule. To test immediately, go to the
**Actions** tab → **Stock dip watch** → **Run workflow** and pick `daily` or `weekly`.

## Configuring

Edit [`config.json`](config.json):

| Field | Meaning |
|-------|---------|
| `top_n` | How many of the biggest drops to report (10–15 is a good range). |
| `daily_min_drop_pct` | Only report daily drops at least this big (`0` = just show the biggest drops). |
| `weekly_min_drop_pct` | Same, for the weekly report. |
| `tickers` | The watchlist. Edit freely — add/remove any symbols. |

## Run locally

```bash
pip install -r requirements.txt
python watch.py daily     # or: weekly
```

Without `DISCORD_WEBHOOK` set, it just prints the report to your terminal —
handy for testing. To test the Discord send locally:

```bash
# Windows PowerShell
$env:DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."; python watch.py daily
```

## Notes / limitations

- **"Down vs. yesterday" is not a buy signal.** It's a radar to make you *look* —
  a stock can drop for a real reason and keep falling. Pair it with your own research.
- GitHub cron runs in **UTC** and ignores US daylight saving; the schedule is set
  to 22:00 UTC so it's always after the 4pm ET close. Cron can also be delayed a
  few minutes under load — fine for a daily close check.
- The watchlist is a static snapshot of large-caps; market-cap rankings drift over
  time, so edit `config.json` whenever you like.
