# Stock Move Monitor

Pushes a phone notification (via [ntfy.sh](https://ntfy.sh)) whenever one of a
watchlist of stocks moves **±1% vs. the previous close**, including pre-market
(from 4:00 AM ET) and after-hours (until 8:00 PM ET).

- One alert when a stock first crosses ±1% on the day; it re-alerts only when it
  crosses into a new whole-percent band (±2%, ±3%, …). All movers in a single
  check are bundled into one notification.
- Prices come from Yahoo Finance (no API key). The latest trade including
  pre/post-market is used.

## Where it runs

**GitHub Actions** runs it every 5 minutes, 24/7, independent of any laptop
(`.github/workflows/stocks.yml`). The day's alert state is kept in the Actions
cache so moves aren't re-notified every run.

The ntfy topic is **not** stored in this repo. It is provided via the
`NTFY_TOPIC` GitHub Actions secret (and, for local runs, a gitignored
`.ntfy_topic` file).

## Receiving alerts

Install the **ntfy** app (iOS/Android), then subscribe to the topic stored in
the `NTFY_TOPIC` secret.

## Configuring

Edit the top of `stock_monitor.py`:

- `TICKERS` — the watchlist.
- `ALERT_PCT` — the move threshold (default `1.0`).

## Running locally

```bash
echo "your-ntfy-topic" > .ntfy_topic   # one-time
python3 stock_monitor.py --force       # run now, ignoring market hours
python3 stock_monitor.py --reset       # clear today's alert state
```
