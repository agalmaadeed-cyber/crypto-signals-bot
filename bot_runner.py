"""
bot_runner.py — Production 24/7 runner for Render
===================================================
Runs the RSI Divergence scanner every 15 minutes, continuously.
Designed for Render's Background Worker service (always-on process).

No cron, no GitHub Actions scheduler — just a clean loop with sleep.
State persists via sent_signals.json and open_positions.json (committed
back to GitHub after each cycle via git push).

Environment variables required (set in Render dashboard):
    TELEGRAM_TOKEN
    OKX_API_KEY
    OKX_SECRET_KEY
    OKX_PASSPHRASE
    GITHUB_TOKEN     — for pushing state files back to the repo
    GITHUB_REPO      — e.g. agalmaadeed-cyber/crypto-signals-bot
"""

import os
import sys
import json
import time
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent))

from data.fetcher import fetch_ohlcv, TOP_20
from patterns.detector import detect_rsi_divergence
from okx_executor import execute_signal, check_and_close_positions

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN         = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID       = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO   = os.environ.get("GITHUB_REPO", "agalmaadeed-cyber/crypto-signals-bot")

SCAN_INTERVAL_SEC      = 900    # 15 minutes
MAX_CANDLE_AGE_MINUTES = 20     # slightly wider than interval to handle drift
TF                     = "15m"
DAYS                   = 3

SENT_FILE      = Path(__file__).parent / "sent_signals.json"
POSITIONS_FILE = Path(__file__).parent / "open_positions.json"


# ─── State helpers ────────────────────────────────────────────────────────────

def load_sent():
    if not SENT_FILE.exists():
        return set()
    try:
        return set(json.loads(SENT_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_sent(sent_set):
    items = list(sent_set)[-500:]
    SENT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def signal_key(symbol, direction, ts):
    return f"{symbol}|{direction}|{ts.strftime('%Y%m%d%H%M')}"


# ─── Telegram ─────────────────────────────────────────────────────────────────

def send_telegram(text):
    if not TOKEN:
        return False
    url  = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception as e:
        print(f"  Telegram error: {e}")
        return False


def fmt_price(price):
    if price >= 1:      return f"{price:,.4f}"
    elif price >= 0.01: return f"{price:.6f}"
    return f"{price:.8f}"


# ─── GitHub state sync ────────────────────────────────────────────────────────

def pull_state():
    """Pull latest state files from GitHub before each scan."""
    if not GITHUB_TOKEN:
        return
    try:
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            capture_output=True, timeout=30
        )
    except Exception as e:
        print(f"  Git pull error: {e}")


def push_state():
    """Commit and push state files to GitHub after each scan."""
    if not GITHUB_TOKEN:
        return
    try:
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
        subprocess.run(["git", "config", "user.name", "render-bot"],
                       capture_output=True)
        subprocess.run(["git", "config", "user.email", "render-bot@noreply"],
                       capture_output=True)
        subprocess.run(["git", "add", "sent_signals.json", "open_positions.json"],
                       capture_output=True)
        result = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            capture_output=True
        )
        if result.returncode != 0:  # there are changes to commit
            subprocess.run(
                ["git", "commit", "-m", "update state [skip ci]"],
                capture_output=True
            )
            subprocess.run(
                ["git", "push", repo_url, "main"],
                capture_output=True, timeout=30
            )
    except Exception as e:
        print(f"  Git push error: {e}")


# ─── Single scan cycle ────────────────────────────────────────────────────────

def run_cycle(cycle_num):
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=MAX_CANDLE_AGE_MINUTES)

    print(f"\n{'='*55}")
    print(f"  Cycle {cycle_num} | {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*55}")

    # Check and close positions that hit stop/target
    try:
        check_and_close_positions()
    except Exception as e:
        print(f"  Position check error: {e}")

    sent_set  = load_sent()
    executed  = skipped = 0

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
                key = signal_key(symbol, sig["direction"], ts)
                if key in sent_set:
                    skipped += 1
                    continue

                # Execute on OKX Demo
                signal_dict = {
                    "symbol":    symbol,
                    "direction": sig["direction"],
                    "entry":     sig["entry"],
                    "stop":      sig["stop"],
                    "target1":   sig["target1"],
                    "target2":   sig.get("target2", sig["target1"]),
                    "rsi":       sig["rsi"],
                }
                success = execute_signal(signal_dict)

                sent_set.add(key)
                executed += 1
                status = "executed" if success else "signal only"
                print(f"  + {symbol} {sig['direction']} @ "
                      f"{fmt_price(sig['entry'])} — {status}.")

        except Exception as e:
            print(f"  {symbol}: skipped ({e})")

    save_sent(sent_set)
    print(f"  Done: {executed} new, {skipped} skipped.")
    return executed


# ─── Main loop ────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  RSI Divergence Bot — Production Runner")
    print(f"  Interval: {SCAN_INTERVAL_SEC//60} minutes | TF: {TF}")
    print(f"  OKX: {'ON' if os.environ.get('OKX_API_KEY') else 'OFF'}")
    print(f"  GitHub sync: {'ON' if GITHUB_TOKEN else 'OFF'}")
    print("=" * 55)

    cycle = 1
    while True:
        try:
            pull_state()
            run_cycle(cycle)
            push_state()
        except Exception as e:
            print(f"  Cycle {cycle} error: {e}")
            send_telegram(f"⚠️ Bot error in cycle {cycle}: {e}")

        cycle += 1
        print(f"\n  Sleeping {SCAN_INTERVAL_SEC//60} minutes...")
        time.sleep(SCAN_INTERVAL_SEC)


if __name__ == "__main__":
    main()
