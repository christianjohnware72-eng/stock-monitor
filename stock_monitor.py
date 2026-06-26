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


def session_label(dt):
    """Which US session we're in, by ET time, for the alert's top line."""
    t = dt.time()
    if dtime(9, 30) <= t < dtime(16, 0):
        return "Market hours"
    if dtime(4, 0) <= t < dtime(9, 30):
        return "Pre-market"
    if dtime(16, 0) <= t <= dtime(20, 0):
        return "After-hours"
    return "Market closed"


def fetch_quote(ticker):
    """Return a dict of reference prices for the ticker, or None on failure.

    Keys: last (latest trade incl. pre/post-market), day_open (today's regular
    open), prev_close (prior regular-session close), reg_close (today's regular
    close), day_high, day_low. The caller picks the baseline by session:
    regular -> day_open, pre-market -> prev_close, after-hours -> reg_close.
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
        ts = result.get("timestamp") or []
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        opens = quote.get("open") or []
        closes = quote.get("close") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []

        # Latest trade, including pre/post-market.
        last = meta.get("regularMarketPrice")
        filled = [c for c in closes if c is not None]
        if filled:
            last = filled[-1]

        reg = meta.get("currentTradingPeriod", {}).get("regular", {})
        reg_start, reg_end = reg.get("start"), reg.get("end")

        # First bar index at/after the regular-session start.
        start_idx = 0
        if reg_start and ts:
            for i, t in enumerate(ts):
                if t >= reg_start:
                    start_idx = i
                    break

        # Today's open = first traded price at/after the regular start
        # (pre-market fallback: first traded price of the day).
        day_open = next((o for o in opens[start_idx:] if o is not None), None)
        if day_open is None:
            day_open = next((o for o in opens if o is not None), None)

        # Today's regular close = last traded price at/before the regular end.
        reg_close = None
        if reg_end and ts:
            for i in range(len(ts) - 1, -1, -1):
                if ts[i] <= reg_end and i < len(closes) and closes[i] is not None:
                    reg_close = closes[i]
                    break
        if reg_close is None:
            reg_close = last

        prev_close = meta.get("chartPreviousClose")

        # Full-day high/low across all bars (so it's sensible in any session).
        hi = [h for h in highs if h is not None]
        lo = [x for x in lows if x is not None]
        day_high = max(hi) if hi else last
        day_low = min(lo) if lo else last

        if last is None or not day_open:
            return ticker, None
        return ticker, {
            "last": float(last),
            "day_open": float(day_open),
            "prev_close": float(prev_close) if prev_close else float(day_open),
            "reg_close": float(reg_close),
            "day_high": float(day_high),
            "day_low": float(day_low),
        }
    except Exception as e:  # noqa: BLE001
        log(f"fetch error {ticker}: {e}")
        return ticker, None


def fetch_all(tickers):
    """ticker -> dict of reference prices (see fetch_quote)."""
    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for ticker, q in ex.map(fetch_quote, tickers):
            if q is not None:
                out[ticker] = q
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


def alert_card(ticker, pct, price, base, base_label, day_high, day_low, session):
    """A bordered 'card' for the notification body.

    Phone notifications use a proportional font, so we avoid vertical borders
    (they'd look ragged) and instead use full-width horizontal rules as the
    border, with emoji-labeled rows for structure.
    """
    arrow = "📈" if pct >= 0 else "📉"
    sign = "+" if pct >= 0 else ""
    rule = "━" * 20

    def row(emoji, label, value):
        # Pad the label so values line up about as well as a proportional font
        # allows.
        return f"{emoji} {label:<10} {value}"

    return "\n".join([
        rule,
        f"{arrow} {ticker}   {sign}{pct:.1f}%   ({session})",
        rule,
        row("💵", "Price", f"${price:,.2f}"),
        row("📊", f"vs {base_label}", f"${base:,.2f}"),
        row("🔼", "Day high", f"${day_high:,.2f}"),
        row("🔽", "Day low", f"${day_low:,.2f}"),
        rule,
    ])


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
    session = session_label(dt)
    state = load_state()

    # Each session uses its own baseline (regular -> today's open, pre-market ->
    # prior close, after-hours -> today's close). Because the baseline changes at
    # each session boundary, the alert bands are reset and re-primed whenever the
    # day OR the session changes — so alerts reflect moves *within* the current
    # session only.
    if state.get("trading_day") != today or state.get("session") != session:
        state = {"trading_day": today, "session": session, "alert_band": {}}
        log(f"{today} {session}: bands reset for new session")

    alert_band = state["alert_band"]

    prices = fetch_all(TICKERS)
    if not prices:
        log("no prices fetched; skipping")
        return

    # Which reference price this session compares against, and its short label.
    base_key, base_label = {
        "Market hours": ("day_open", "Open"),
        "Pre-market": ("prev_close", "Prev close"),
        "After-hours": ("reg_close", "Close"),
    }.get(session, ("day_open", "Open"))

    # First check of this session: record current bands silently so we don't
    # blast alerts for moves that happened before we started watching it.
    # Exception: pre-market does NOT prime, so an existing overnight gap (already
    # >=2% vs the prior close at the first check) still alerts.
    priming = (not alert_band) and session != "Pre-market"

    for ticker, q in prices.items():
        base = q[base_key]
        if not base:
            continue
        price = q["last"]
        pct = (price - base) / base * 100.0
        b = band(pct)

        if priming:
            alert_band[ticker] = b
            continue

        # Alert only when this stock crosses into a new whole-percent band at or
        # beyond the threshold (e.g. first hit of +/-2%, then +/-3%...).
        if abs(b) >= ALERT_PCT and b != alert_band.get(ticker, 0):
            alert_band[ticker] = b
            arrow = "📈" if pct >= 0 else "📉"
            sign = "+" if pct >= 0 else ""
            # One notification per stock — just the one that moved. The body is a
            # "card" with horizontal rule borders + emoji-labeled rows, which
            # renders cleanly in a phone's proportional font.
            title = f"{arrow} {ticker} {sign}{pct:.1f}% · {session}"
            message = alert_card(
                ticker, pct, price, base, base_label,
                q["day_high"], q["day_low"], session,
            )
            notify(title, message)
            log(f"ALERT {session} {ticker} {sign}{pct:.1f}% ({base_label} ${base:.2f} -> ${price:.2f})")

    if priming:
        log(f"primed {len(alert_band)} bands ({session} start — no alerts)")

    state["alert_band"] = alert_band
    save_state(state)


if __name__ == "__main__":
    main()
