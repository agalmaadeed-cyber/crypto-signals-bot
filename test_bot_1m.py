"""
test_bot_1m.py — Single-cycle 1m test bot for GitHub Actions scheduler validation
===================================================================================
PURPOSE: Validate that GitHub Actions fires reliably every minute by running ONE
cycle per invocation. GitHub itself is the scheduler — no internal loop or sleep.

DIFFERENCES from the local multi-cycle version:
  - No --cycles / --interval / --cap CLI args (GitHub controls cadence)
  - No lock file (GitHub ensures single concurrent execution via concurrency groups)
  - No internal sleep or loop — runs one scan, saves state, exits cleanly
  - Full isolation from production: reads/writes only _test1m state files
  - OKX execution handled inline (not via okx_executor.execute_signal) so the
    isolated _test1m position file is the single source of truth for the cap,
    with no cross-contamination from open_positions.json

STATE FILES (committed back to repo by the workflow after each run):
  sent_signals_test1m.json    — dedup memory (same pattern as sent_signals.json)
  open_positions_test1m.json  — isolated position tracker (cap enforcement)

CONCURRENCY CAP: hard-coded at 3 (same as production intent).
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).parent))

from data.fetcher import fetch_ohlcv, TOP_20
from patterns.detector import detect_rsi_divergence

try:
    import ccxt
    CCXT_AVAILABLE = True
except ImportError:
    CCXT_AVAILABLE = False

# ─── Configuration ────────────────────────────────────────────────────────────

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")

TF                       = "1m"
DAYS                     = 1
MAX_CONCURRENT           = 3       # hard cap — never exceeded regardless of signals
DETECTION_WINDOW_MINUTES = 10      # catch signals even if GitHub fired slightly late

OKX_API_KEY    = os.environ.get("OKX_API_KEY")
OKX_SECRET_KEY = os.environ.get("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.environ.get("OKX_PASSPHRASE")
OKX_ENABLED    = CCXT_AVAILABLE and all([OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE])

STATE_DIR      = Path(__file__).parent
SENT_FILE      = STATE_DIR / "sent_signals_test1m.json"
POSITIONS_FILE = STATE_DIR / "open_positions_test1m.json"

# Minimum order sizes per symbol (OKX Demo requirements)
MIN_AMOUNTS = {
    "BTC/USDT": 0.001, "ETH/USDT": 0.01,  "BNB/USDT": 0.1,
    "SOL/USDT": 0.1,   "XRP/USDT": 10.0,  "ADA/USDT": 10.0,
    "DOGE/USDT": 100.0,"AVAX/USDT": 0.1,  "LINK/USDT": 0.1,
    "TRX/USDT": 100.0, "default":   1.0,
}


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


def load_positions():
    if not POSITIONS_FILE.exists():
        return {}
    try:
        return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_positions(positions):
    POSITIONS_FILE.write_text(
        json.dumps(positions, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


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


# ─── OKX (inline, isolated — does NOT call okx_executor.py) ──────────────────

def connect_okx():
    return ccxt.okx({
        "apiKey":   OKX_API_KEY,
        "secret":   OKX_SECRET_KEY,
        "password": OKX_PASSPHRASE,
        "sandbox":  True,
        "options":  {"defaultType": "swap"},
    })


def get_pos_mode(exchange):
    try:
        resp = exchange.private_get_account_config()
        mode = resp.get("data", [{}])[0].get("posMode", "net_mode")
        return "hedge" if mode == "long_short_mode" else "oneway"
    except Exception:
        return "oneway"


def open_on_okx(symbol, direction, rsi):
    """
    Opens a Futures (Swap) position on OKX Demo.
    Uses explicit swap_symbol construction — avoids the Spot routing bug.
    Returns order_id or None.
    """
    try:
        exchange   = connect_okx()
        pos_mode   = get_pos_mode(exchange)
        swap_symbol = symbol.replace("/", "-") + "-SWAP"   # explicit — never rely on ccxt defaultType mapping

        # Set leverage to 1x (no leverage)
        try:
            exchange.set_leverage(1, swap_symbol, params={"mgnMode": "cross"})
        except Exception:
            pass  # may already be set

        # Calculate size: 2% of USDT balance
        balance    = float(exchange.fetch_balance()["total"].get("USDT", 0))
        risk_usdt  = balance * 0.02
        ticker     = exchange.fetch_ticker(symbol)
        price      = ticker["last"]
        amount     = max(round(risk_usdt / price, 4),
                         MIN_AMOUNTS.get(symbol, MIN_AMOUNTS["default"]))

        order_side = "buy" if direction == "long" else "sell"
        params     = {"posSide": direction} if pos_mode == "hedge" else {}

        order = exchange.create_order(
            symbol=swap_symbol,    # explicit swap symbol
            type="market",
            side=order_side,
            amount=amount,
            params=params,
        )
        print(f"  OKX: opened {direction} {amount} {symbol} @ ~{fmt_price(price)} (swap)")
        return order.get("id"), amount
    except Exception as e:
        print(f"  OKX error: {e}")
        return None, None


# ─── Main single-cycle logic ──────────────────────────────────────────────────

def run():
    now    = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=DETECTION_WINDOW_MINUTES)

    print(f"[TEST 1m] {now.strftime('%Y-%m-%d %H:%M:%S')} UTC | "
          f"OKX: {'ON' if OKX_ENABLED else 'OFF'} | "
          f"Window: {DETECTION_WINDOW_MINUTES}min")

    sent_set  = load_sent()
    positions = load_positions()
    open_count = len(positions)
    print(f"  Open positions: {open_count}/{MAX_CONCURRENT}")

    executed_count = rejected_count = 0

    for symbol in TOP_20[:10]:
        try:
            df = fetch_ohlcv(symbol, TF, days=DAYS, use_cache=False)
            if df.empty or len(df) < 60:
                continue
            signals = detect_rsi_divergence(df)
            if signals.empty:
                continue
            recent = signals[signals.index >= cutoff]

            for ts, sig in recent.iterrows():
                key = signal_key(symbol, sig["direction"], ts)
                if key in sent_set:
                    print(f"  ~ {symbol} {sig['direction']} — duplicate, skipped.")
                    continue

                # Reload positions immediately before cap check
                # (protects against stale reads within the same cycle)
                positions  = load_positions()
                open_count = len(positions)

                if open_count >= MAX_CONCURRENT:
                    print(f"  X {symbol} {sig['direction']} — cap reached ({open_count}/{MAX_CONCURRENT}).")
                    rejected_count += 1
                    sent_set.add(key)
                    continue

                # Execute on OKX (inline, isolated)
                order_id = amount = None
                if OKX_ENABLED:
                    order_id, amount = open_on_okx(symbol, sig["direction"], sig["rsi"])

                # Save to ISOLATED position file — never touches open_positions.json
                pos_key = f"{symbol}|{sig['direction']}"
                positions[pos_key] = {
                    "symbol":    symbol,
                    "direction": sig["direction"],
                    "entry":     sig["entry"],
                    "stop":      sig["stop"],
                    "target1":   sig["target1"],
                    "amount":    amount,
                    "order_id":  order_id,
                    "opened_at": ts.isoformat(),
                }
                save_positions(positions)
                sent_set.add(key)
                executed_count += 1

                status = "executed on OKX Swap" if order_id else "detect-only"
                print(f"  + {symbol} {sig['direction']} @ {fmt_price(sig['entry'])} — {status}.")

                send_telegram(
                    f"🧪 <b>[TEST 1m] {status}</b>\n\n"
                    f"Symbol: <b>{symbol}</b>\n"
                    f"Direction: {sig['direction'].upper()}\n"
                    f"Entry: {fmt_price(sig['entry'])}\n"
                    f"RSI: {sig['rsi']}\n"
                    f"Time: {ts.strftime('%H:%M UTC')}"
                )

        except Exception as e:
            print(f"  {symbol}: skipped ({e})")

    save_sent(sent_set)
    print(f"  Done: {executed_count} executed, {rejected_count} rejected by cap.")


if __name__ == "__main__":
    run()
