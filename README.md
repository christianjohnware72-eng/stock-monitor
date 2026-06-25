# Stock Move Monitor

Pushes a phone notification (via [ntfy.sh](https://ntfy.sh)) whenever a
watchlisted stock makes a **fresh ±2% intraday move from today's open**,
including pre-market (from 4:00 AM ET) and after-hours (until 8:00 PM ET).

- **One notification per stock** — only the ticker that actually moved.
- The move is measured from **today's regular-session open**, so the morning is
  quiet and you only hear about genuine intraday moves (not how far a stock
  already sits from yesterday's close).
- The first check each day primes silently; alerts fire only on crossings that
  happen afterward. A stock re-alerts when it reaches the next whole percent
  (±3%, ±4%, …), with hysteresis so it won't spam around the threshold.
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
