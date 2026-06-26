# Stock Move Monitor

Pushes a phone notification (via [ntfy.sh](https://ntfy.sh)) whenever a
watchlisted stock makes a **fresh ±2% move**, across pre-market (from 4:00 AM
ET), regular hours, and after-hours (until 8:00 PM ET).

- **One notification per stock** — only the ticker that actually moved. The top
  line is tagged with the session (Pre-market / Market hours / After-hours).
- The move is measured against a **session-appropriate baseline**:
  - **Market hours** → today's regular-session **open**
  - **Pre-market** → the **prior session's close**
  - **After-hours** → **today's regular close**
- Bands re-prime silently at the start of each session, so you only hear about
  moves that happen *within* the current session, and a stock re-alerts at each
  further whole percent (±3%, ±4%, …) with hysteresis (no threshold spam).
- Prices come from Yahoo Finance (no API key); the latest trade including
  pre/post-market is used. The body also shows the day's high/low.

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
