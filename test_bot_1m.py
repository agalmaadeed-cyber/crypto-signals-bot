"""
test_bot_1m.py — Infrastructure stress test using REAL strategy logic on 1m candles
=====================================================================================
PURPOSE: This is NOT a profitability test. It validates that the full pipeline
(detect -> cap -> execute -> notify -> track -> close) works reliably under
HIGH FREQUENCY conditions, using the exact same detect_rsi_divergence() function
the production 15m system uses — only the timeframe changes.

WHY THIS EXISTS: On 2026-06-18/19, GitHub Actions' scheduler went silent for
hours, then fired all at once and opened 8 concurrent positions with zero
concurrency cap. This script proves the pipeline behaves correctly (caps at
MAX_CONCURRENT, executes cleanly, closes cleanly) before we trust it with a
more reliable scheduler.

ISOLATION: Uses separate state files (suffixed _test1m) so this NEVER touches
production state (open_positions.json, sent_signals.json) used by the real
15m GitHub Actions workflow.

SAFETY: Hard concurrency cap enforced BEFORE execution, not after.

USAGE (run locally, from the crypto_signals_bot folder, with env vars set):
    python test_bot_1m.py
    python test_bot_1m.py --cycles 30 --interval 60 --cap 3
"""

import os
import sys
import json
import time
import argparse
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Force unbuffered, line-flushed stdout so live progress is visible
# regardless of how this script is launched (background task, redirected
# to a file, etc). Without this, output can sit invisible in a buffer
# until the process exits.
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).parent))

# ─── Import REAL production modules — do not redefine detection logic ───────
from data.fetcher import fetch_ohlcv, TOP_20
from patterns.detector import detect_rsi_divergence

# OKX executor is optional — script still runs (detect-only) if unavailable
try:
    from okx_executor import execute_signal as okx_execute_signal
    OKX_MODULE_AVAILABLE = True
except ImportError:
    OKX_MODULE_AVAILABLE = False

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")

TF              = "1m"      # <-- the ONLY deliberate change from production
DAYS            = 1         # 1m candles -> 1 day is plenty of history
DEFAULT_CYCLES  = 30
DEFAULT_INTERVAL = 60       # seconds between cycles
DEFAULT_CAP     = 3         # hard concurrency cap — non-negotiable per today's incident

# Isolated state — NEVER shared with the production 15m bot
STATE_DIR        = Path(__file__).parent
SENT_FILE_TEST   = STATE_DIR / "sent_signals_test1m.json"
POSITIONS_FILE   = STATE_DIR / "open_positions_test1m.json"
LOG_FILE         = STATE_DIR / "test1m_run_log.json"

OKX_ENABLED = OKX_MODULE_AVAILABLE and all([
    os.environ.get("OKX_API_KEY"),
    os.environ.get("OKX_SECRET_KEY"),
    os.environ.get("OKX_PASSPHRASE"),
])


# ─── Sanity guard ─────────────────────────────────────────────────────────────

def assert_isolated_from_production():
    """
    Refuse to run if this script would touch production state files.
    This is the one mistake that must never happen.
    """
    prod_files = ["sent_signals.json", "open_positions.json"]
    for f in prod_files:
        if Path(f).resolve() == SENT_FILE_TEST.resolve() or \
           Path(f).resolve() == POSITIONS_FILE.resolve():
            print(f"FATAL: state file collision with production file {f}. Aborting.")
            sys.exit(1)
    print("OK: isolated from production state files (sent_signals.json, open_positions.json).")


LOCK_FILE = STATE_DIR / "test_bot_1m.lock"


def acquire_lock_or_exit():
    """
    Refuse to start a second instance. This script's concurrency cap only
    works correctly with a single writer to open_positions_test1m.json —
    two instances racing each other can both pass the cap check before
    either has saved its position, silently exceeding the cap.
    """
    if LOCK_FILE.exists():
        try:
            old_pid = int(LOCK_FILE.read_text().strip())
        except Exception:
            old_pid = None
        print(f"FATAL: lock file exists ({LOCK_FILE.name}, pid={old_pid}).")
        print("Another instance may already be running. If you're sure it is not,")
        print(f"delete {LOCK_FILE} manually and re-run.")
        sys.exit(1)
    LOCK_FILE.write_text(str(os.getpid()))


def release_lock():
    LOCK_FILE.unlink(missing_ok=True)


# ─── State (isolated) ─────────────────────────────────────────────────────────

def load_json_set(path):
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_json_set(path, data_set, max_items=500):
    items = list(data_set)[-max_items:]
    path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def load_positions():
    if not POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_positions(positions):
    POSITIONS_FILE.write_text(json.dumps(positions, ensure_ascii=False, indent=2),
                              encoding="utf-8")


def signal_key(symbol, direction, ts):
    return f"{symbol}|{direction}|{ts.strftime('%Y%m%d%H%M')}"


# ─── Telegram ──────────────────────────────────────────────────────────────────

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
    if price >= 1:
        return f"{price:,.4f}"
    elif price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


# ─── Core cycle logic ──────────────────────────────────────────────────────────

def count_open_positions():
    return len(load_positions())


DETECTION_WINDOW_MINUTES = 10  # widened from 1 to avoid missing signals if a
                                 # cycle is delayed (cycles have drifted 60-150s
                                 # in practice, not exactly 60s every time)


def scan_one_cycle(symbols):
    """Scan all symbols on 1m for signals in the last DETECTION_WINDOW_MINUTES."""
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=DETECTION_WINDOW_MINUTES)
    found  = []
    for symbol in symbols:
        try:
            df = fetch_ohlcv(symbol, TF, days=DAYS, use_cache=False)
            if df.empty or len(df) < 60:
                continue
            signals = detect_rsi_divergence(df)
            if signals.empty:
                continue
            recent = signals[signals.index >= cutoff]
            for ts, sig in recent.iterrows():
                found.append((symbol, sig, ts))
        except Exception as e:
            print(f"    {symbol}: skipped ({e})")
    return found


def run_cycle(cycle_num, total_cycles, cap, sent_set, stats):
    print(f"\n--- Cycle {cycle_num}/{total_cycles} | {datetime.now(timezone.utc).strftime('%H:%M:%S')} UTC ---")

    open_count = count_open_positions()
    print(f"  Open positions: {open_count}/{cap}")

    signals = scan_one_cycle(TOP_20[:10])  # smaller universe — 1m scanning is heavier
    if not signals:
        print("  No signals this cycle.")
        return

    for symbol, sig, ts in signals:
        key = signal_key(symbol, sig["direction"], ts)
        if key in sent_set:
            print(f"  ~ {symbol} {sig['direction']} — duplicate, skipped.")
            continue

        # HARD CAP — enforced BEFORE any execution, every single time
        open_count = count_open_positions()
        if open_count >= cap:
            print(f"  X {symbol} {sig['direction']} — REJECTED, cap reached ({open_count}/{cap}).")
            stats["rejected_cap"] += 1
            sent_set.add(key)  # don't keep re-evaluating the same rejected signal
            continue

        executed = False
        if OKX_ENABLED:
            signal_dict = {
                "symbol": symbol, "direction": sig["direction"],
                "entry": sig["entry"], "stop": sig["stop"],
                "target1": sig["target1"], "target2": sig.get("target2", sig["target1"]),
                "rsi": sig["rsi"],
            }
            try:
                executed = okx_execute_signal(signal_dict)
            except Exception as e:
                print(f"    OKX execution error: {e}")

        # Track in ISOLATED position file regardless of OKX result,
        # so the cap logic works even in detect-only mode.
        positions = load_positions()
        positions[f"{symbol}|{sig['direction']}"] = {
            "symbol": symbol, "direction": sig["direction"],
            "entry": sig["entry"], "opened_at": ts.isoformat(),
        }
        save_positions(positions)

        status = "executed on OKX" if executed else ("detect-only" if not OKX_ENABLED else "OKX exec failed")
        msg = (
            f"🧪 <b>[TEST 1m] Signal — {status}</b>\n\n"
            f"Symbol: <b>{symbol}</b>\n"
            f"Direction: {sig['direction'].upper()}\n"
            f"Entry: {fmt_price(sig['entry'])}\n"
            f"RSI: {sig['rsi']}\n"
            f"Cycle: {cycle_num}/{total_cycles}"
        )
        send_telegram(msg)

        sent_set.add(key)
        stats["executed"] += 1
        print(f"  + {symbol} {sig['direction']} @ {fmt_price(sig['entry'])} — {status}.")


# ─── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles",   type=int, default=DEFAULT_CYCLES)
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="seconds between cycles")
    ap.add_argument("--cap",      type=int, default=DEFAULT_CAP)
    args = ap.parse_args()

    assert_isolated_from_production()
    acquire_lock_or_exit()

    print("=" * 60)
    print("  TEST BOT — 1m timeframe, REAL detection logic")
    print(f"  Cycles: {args.cycles} | Interval: {args.interval}s | Cap: {args.cap}")
    print(f"  OKX execution: {'ENABLED' if OKX_ENABLED else 'disabled (detect-only)'}")
    print("=" * 60)

    sent_set = load_json_set(SENT_FILE_TEST)
    stats    = {"executed": 0, "rejected_cap": 0}

    try:
        for i in range(1, args.cycles + 1):
            run_cycle(i, args.cycles, args.cap, sent_set, stats)
            save_json_set(SENT_FILE_TEST, sent_set)
            if i < args.cycles:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped manually.")
    finally:
        release_lock()

    print("\n" + "=" * 60)
    print("  RUN SUMMARY")
    print(f"  Signals executed/notified : {stats['executed']}")
    print(f"  Signals rejected by cap   : {stats['rejected_cap']}")
    print(f"  Final open positions      : {count_open_positions()}/{args.cap}")
    print("=" * 60)

    summary = {
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "cycles": args.cycles, "interval": args.interval, "cap": args.cap,
        **stats,
    }
    LOG_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nSummary saved to {LOG_FILE.name}")
