#!/usr/bin/env python3
"""
Stock move monitor.

Checks a watchlist of tickers and fires a native macOS notification whenever a
stock has moved >= ALERT_PCT (default 1%) away from its baseline price, where the
baseline is the price captured when monitoring first started for the current
trading day ("since monitoring started").

- Re-alerts only when a stock crosses into a *new* whole-percent band (so a stock
  that hit +1% won't re-notify every 5 min, but will notify again at +2%, +3%...).
- Only acts during US market hours (Mon-Fri, 09:30-16:00 America/New_York).
- Baseline resets at the start of each trading day.

Run manually with --force to ignore market hours, or --reset to clear the baseline.
"""

import json
import os
import subprocess
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dtime

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    ET = None

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LOG_FILE = os.path.join(HERE, "monitor.log")

ALERT_PCT = 2.0  # percent move that triggers an alert

# ntfy.sh push topic. Subscribe to this exact topic in the ntfy iPhone app to
# receive alerts. Treat the topic name as a secret, so it is NOT hard-coded here
# (this repo may be public). It is read from the NTFY_TOPIC env var (set as a
# GitHub Actions secret in the cloud) or from a local, gitignored .ntfy_topic
# file for laptop/manual runs.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
if not NTFY_TOPIC:
    try:
        with open(os.path.join(HERE, ".ntfy_topic")) as _f:
            NTFY_TOPIC = _f.read().strip()
    except OSError:
        NTFY_TOPIC = ""

TICKERS = [
    "AAPL", "AMD", "AMZN", "AVGO", "BTDR", "BWXT", "CEG", "CRSP", "DRAM",
    "GOOG", "IBM", "INFQ", "INTC", "IONQ", "MRVL", "MSFT", "MU", "NBIS",
    "NEE", "NOW", "NVDA", "PANW", "PLTR", "QBTS", "RGTI", "SPCX", "UNH", "VRT",
]


def log(msg):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def now_et():
    return datetime.now(ET) if ET else datetime.now()


def active_now(dt):
    """True during US trading + extended hours: Mon-Fri 04:00-20:00 ET.
    Covers pre-market (4:00), regular (9:30-16:00), and after-hours (16:00-20:00).
    Outside this window stock prices don't change, so we skip."""
    if dt.weekday() >= 5:  # Sat/Sun
        return False
    return dtime(4, 0) <= dt.time() <= dtime(20, 0)


def fetch_price(ticker):
    """Return (ticker, last_price, prev_close).

    last_price is the most recent trade INCLUDING pre/post-market, taken from the
    last filled 1-minute bar. prev_close is the previous regular-session close,
    used as the baseline so the move matches the % shown in a stock app.
    """
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        "?interval=1m&range=1d&includePrePost=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        result = data["chart"]["result"][0]
        meta = result["meta"]
        prev_close = meta.get("chartPreviousClose")
        last = meta.get("regularMarketPrice")
        closes = result.get("indicators", {}).get("quote", [{}])[0].get("close")
        if closes:
            filled = [c for c in closes if c is not None]
            if filled:
                last = filled[-1]  # newest trade incl. pre/post-market
        if last is None or prev_close is None:
            return ticker, None, None
        return ticker, float(last), float(prev_close)
    except Exception as e:  # noqa: BLE001
        log(f"fetch error {ticker}: {e}")
        return ticker, None, None


def fetch_all(tickers):
    """ticker -> (last_price, prev_close)."""
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for ticker, price, prev in ex.map(fetch_price, tickers):
            if price is not None and prev:
                out[ticker] = (price, prev)
    return out


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _local_banner(title, message):
    t = title.replace('"', '\\"')
    m = message.replace('"', '\\"')
    script = f'display notification "{m}" with title "{t}" sound name "Glass"'
    subprocess.run(["osascript", "-e", script], check=False, timeout=10)


def _ntfy(title, message):
    """Push the alert to the phone via ntfy.sh."""
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    req = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", "ignore").decode(),
            "Priority": "high",
            "Tags": "chart_with_upwards_trend",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status >= 300:
            raise RuntimeError(f"ntfy HTTP {r.status}")


def notify(title, message):
    """Push the alert to the phone via ntfy; fall back to a local banner."""
    if NTFY_TOPIC:
        try:
            _ntfy(title, message)
            return
        except Exception as e:  # noqa: BLE001
            log(f"ntfy error: {e}; falling back to local banner")
    try:
        _local_banner(title, message)
    except Exception as e:  # noqa: BLE001
        log(f"notify error: {e}")


def band(pct):
    """Signed whole-percent band: +1.7 -> 1, -2.3 -> -2."""
    return int(pct) if pct >= 0 else -int(-pct)


def main():
    args = set(sys.argv[1:])
    force = "--force" in args

    if "--reset" in args:
        save_state({})
        log("state reset")
        return

    dt = now_et()
    if not force and not active_now(dt):
        # Quiet exit outside trading + extended hours (prices don't move).
        return

    today = dt.strftime("%Y-%m-%d")
    state = load_state()

    # Baseline = previous close (fetched live each run). Only the per-ticker
    # alert band is persisted, and it resets each trading day so the day starts
    # fresh.
    if state.get("trading_day") != today:
        state = {"trading_day": today, "alert_band": {}}
        log(f"new trading day {today}: alert bands reset")

    alert_band = state["alert_band"]

    prices = fetch_all(TICKERS)
    if not prices:
        log("no prices fetched; skipping")
        return

    movers = []
    for ticker, (price, prev_close) in prices.items():
        pct = (price - prev_close) / prev_close * 100.0
        b = band(pct)

        if abs(b) >= ALERT_PCT and b != alert_band.get(ticker, 0):
            alert_band[ticker] = b
            sign = "+" if pct >= 0 else ""
            movers.append((ticker, pct, price, f"{sign}{pct:.1f}%"))

    if movers:
        movers.sort(key=lambda x: abs(x[1]), reverse=True)
        summary = "  ".join(f"{t} {lbl} ${p:.2f}" for t, _, p, lbl in movers)
        title = f"📈 Stock Alert — {len(movers)} mover{'s' if len(movers) > 1 else ''}"
        notify(title, summary)
        log(f"ALERT: {summary}")

    state["alert_band"] = alert_band
    save_state(state)


if __name__ == "__main__":
    main()
