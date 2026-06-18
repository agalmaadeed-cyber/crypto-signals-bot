"""
telegram_scanner.py — Block 2+3: Scan + Send + Deduplication + OKX Execution
==============================================================================
Scans symbols, captures signals from the last 180 minutes,
sends each new signal to Telegram, optionally executes on OKX Demo,
and remembers what was sent to avoid duplicates.

Unique signal key: symbol + direction + candle timestamp
Memory: sent_signals.json in the project folder

Security: all keys read from environment variables only.

Usage:
  python telegram_scanner.py
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent))

from data.fetcher import fetch_ohlcv, TOP_20
from patterns.detector import detect_rsi_divergence

# ─── Config ──────────────────────────────────────────────────────────────────

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")

TF             = "15m"
DAYS           = 3
MAX_CANDLE_AGE = 180    # minutes — scan signals within this window
SENT_FILE      = Path(__file__).parent / "sent_signals.json"
MAX_MEMORY     = 500    # max signals to keep in memory (prevents file bloat)

# OKX execution — enabled only when all three keys are present
OKX_ENABLED = all([
    os.environ.get("OKX_API_KEY"),
    os.environ.get("OKX_SECRET_KEY"),
    os.environ.get("OKX_PASSPHRASE"),
])


# ─── Memory ──────────────────────────────────────────────────────────────────

def load_sent():
    """Load previously sent signals from file."""
    if not SENT_FILE.exists():
        return set()
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()


def save_sent(sent_set):
    """Save sent signals — keeps only the last MAX_MEMORY entries."""
    items = list(sent_set)[-MAX_MEMORY:]
    SENT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def signal_key(symbol, direction, ts):
    """Unique key for each signal: symbol + direction + candle timestamp."""
    return f"{symbol}|{direction}|{ts.strftime('%Y%m%d%H%M')}"


# ─── Formatting ──────────────────────────────────────────────────────────────

def fmt_price(price: float) -> str:
    """Smart price formatter — shows enough decimal places for the magnitude."""
    if price >= 1:
        return f"{price:,.4f}"
    elif price >= 0.01:
        return f"{price:.6f}"
    else:
        return f"{price:.8f}"


def format_signal(symbol, sig, ts):
    direction = "LONG  🟢" if sig["direction"] == "long" else "SHORT 🔴"
    return (
        f"🔔 <b>New Signal — RSI Divergence</b>\n"
        f"\n"
        f"Symbol:    <b>{symbol}</b>\n"
        f"Direction: <b>{direction}</b>\n"
        f"Entry:     {fmt_price(sig['entry'])}\n"
        f"Stop:      {fmt_price(sig['stop'])}\n"
        f"Target 1:  {fmt_price(sig['target1'])}\n"
        f"Target 2:  {fmt_price(sig['target2'])}\n"
        f"RSI:       {sig['rsi']}\n"
        f"Time:      {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    )


# ─── Telegram ────────────────────────────────────────────────────────────────

def send_message(text):
    if not TOKEN:
        print("ERROR: TELEGRAM_TOKEN not set in environment.")
        sys.exit(1)

    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("ok", False)
    except Exception as e:
        print(f"  Send error: {e}")
        return False


# ─── OKX Execution ───────────────────────────────────────────────────────────

def maybe_execute(symbol, sig):
    """Fire-and-forget OKX execution. Skips silently if not enabled."""
    if not OKX_ENABLED:
        return
    try:
        from okx_executor import execute_signal
        execute_signal({
            "symbol":    symbol,
            "direction": sig["direction"],
            "entry":     sig["entry"],
            "stop":      sig["stop"],
            "target1":   sig["target1"],
            "target2":   sig.get("target2", 0),
            "rsi":       sig.get("rsi", 0),
        })
    except Exception as e:
        print(f"  OKX execution error: {e}")


# ─── Scanning ────────────────────────────────────────────────────────────────

def scan():
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=MAX_CANDLE_AGE)
    found  = []

    print(f"Scanning {len(TOP_20)} symbols at {now.strftime('%H:%M UTC')}...")
    for symbol in TOP_20:
        try:
            df = fetch_ohlcv(symbol, TF, days=DAYS)
            if df.empty or len(df) < 60:
                continue
            signals = detect_rsi_divergence(df)
            if signals.empty:
                continue
            recent = signals[signals.index >= cutoff]
            for ts, sig in recent.iterrows():
                found.append((symbol, sig, ts))
        except Exception as e:
            print(f"  {symbol}: skipped ({e})")

    return found


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    signals  = scan()
    sent_set = load_sent()

    if not signals:
        print(f"No new signals in the last {MAX_CANDLE_AGE} minutes.")
        sys.exit(0)

    if OKX_ENABLED:
        print("OKX execution: ENABLED")
    else:
        print("OKX execution: disabled (keys not set)")

    sent_count = skipped = 0

    for symbol, sig, ts in signals:
        key = signal_key(symbol, sig["direction"], ts)

        if key in sent_set:
            skipped += 1
            print(f"  ~ {symbol} {sig['direction']} — already sent, skipped.")
            continue

        msg = format_signal(symbol, sig, ts)
        ok  = send_message(msg)

        if ok:
            sent_set.add(key)
            sent_count += 1
            print(f"  + {symbol} {sig['direction']} @ {fmt_price(sig['entry'])} — sent.")
            maybe_execute(symbol, sig)
        else:
            print(f"  ! {symbol} {sig['direction']} — send failed.")

    save_sent(sent_set)

    print(f"\nDone: {sent_count} sent, {skipped} skipped (already sent).")
