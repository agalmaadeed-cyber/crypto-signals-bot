"""
okx_executor.py — EXECUTION LAYER
==================================
يربط إشارات RSI Divergence بالتنفيذ الفعلي على OKX Demo.

المنطق الكامل:
  1. يقرأ رصيد USDT الفعلي من OKX Demo
  2. يحسب حجم الصفقة (2% من الرصيد)
  3. يفتح صفقة Futures (long أو short) بلا رافعة
  4. يحفظ الصفقة في open_positions.json
  5. يرسل تأكيد عبر Telegram

إدارة المراكز المفتوحة:
  - يراقب الصفقات المفتوحة في كل تشغيل
  - يغلق الصفقات التي بلغت الوقف أو الهدف

الأمان: المفاتيح من متغيرات البيئة فقط.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone

try:
    import ccxt
except ImportError:
    print("ERROR: ccxt not installed. Run: pip install ccxt")
    sys.exit(1)

# ─── إعدادات ────────────────────────────────────────────────────────────────

API_KEY    = os.environ.get("OKX_API_KEY")
SECRET_KEY = os.environ.get("OKX_SECRET_KEY")
PASSPHRASE = os.environ.get("OKX_PASSPHRASE")
TG_TOKEN   = os.environ.get("TELEGRAM_TOKEN")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")

RISK_PCT        = 0.02          # 2% لكل صفقة
LEVERAGE        = 1             # بلا رافعة
POSITIONS_FILE  = Path(__file__).parent / "open_positions.json"

# الحد الأدنى للكمية لكل عملة (متطلبات OKX)
MIN_AMOUNTS = {
    "BTC/USDT":  0.001,
    "ETH/USDT":  0.01,
    "BNB/USDT":  0.1,
    "SOL/USDT":  0.1,
    "XRP/USDT":  10.0,
    "ADA/USDT":  10.0,
    "DOGE/USDT": 100.0,
    "AVAX/USDT": 0.1,
    "LINK/USDT": 0.1,
    "TRX/USDT":  100.0,
    "default":   1.0,
}


# ─── الاتصال ────────────────────────────────────────────────────────────────

def connect():
    return ccxt.okx({
        "apiKey":   API_KEY,
        "secret":   SECRET_KEY,
        "password": PASSPHRASE,
        "sandbox":  True,
        "options":  {"defaultType": "swap"},
    })


def check_keys():
    missing = [k for k, v in {
        "OKX_API_KEY": API_KEY,
        "OKX_SECRET_KEY": SECRET_KEY,
        "OKX_PASSPHRASE": PASSPHRASE,
    }.items() if not v]
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        sys.exit(1)


# ─── الرصيد والحجم ──────────────────────────────────────────────────────────

def get_usdt_balance(exchange):
    """يقرأ رصيد USDT الفعلي من OKX Demo."""
    try:
        balance = exchange.fetch_balance()
        return float(balance["total"].get("USDT", 0))
    except Exception as e:
        print(f"  Balance error: {e}")
        return 0.0


def calc_amount(exchange, symbol, usdt_balance):
    """
    يحسب حجم الصفقة:
    risk_usdt = 2% من الرصيد
    amount    = risk_usdt ÷ سعر العملة الحالي
    مع مراعاة الحد الأدنى للبورصة.
    """
    risk_usdt = usdt_balance * RISK_PCT
    try:
        ticker = exchange.fetch_ticker(symbol)
        price  = ticker["last"]
        amount = risk_usdt / price
        # مراعاة الحد الأدنى
        min_amt = MIN_AMOUNTS.get(symbol, MIN_AMOUNTS["default"])
        amount  = max(amount, min_amt)
        # تقريب لأربعة أرقام عشرية
        return round(amount, 4), price
    except Exception as e:
        print(f"  Price fetch error: {e}")
        return None, None


# ─── إدارة الملف ────────────────────────────────────────────────────────────

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


def position_key(symbol, direction):
    return f"{symbol}|{direction}"


# ─── Telegram ───────────────────────────────────────────────────────────────

def send_telegram(text):
    if not TG_TOKEN:
        return
    url  = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    TG_CHAT,
        "text":       text,
        "parse_mode": "HTML",
    }).encode()
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # الإشعار اختياري — لا يوقف التنفيذ


# ─── التنفيذ ────────────────────────────────────────────────────────────────

def set_leverage(exchange, symbol):
    try:
        market_id = symbol.replace("/", "-") + "-SWAP"
        exchange.set_leverage(LEVERAGE, market_id, params={"mgnMode": "cross"})
    except Exception:
        pass  # قد تكون مضبوطة أصلاً


def get_pos_mode(exchange):
    try:
        resp     = exchange.private_get_account_config()
        pos_mode = resp.get("data", [{}])[0].get("posMode", "net_mode")
        return "hedge" if pos_mode == "long_short_mode" else "oneway"
    except Exception:
        return "oneway"


def open_position(exchange, symbol, direction, amount, pos_mode):
    """يفتح مركز Futures."""
    swap_symbol = symbol.replace("/", "-") + "-SWAP"
    order_side  = "buy" if direction == "long" else "sell"
    params      = {"posSide": direction} if pos_mode == "hedge" else {}
    try:
        order = exchange.create_order(
            symbol=swap_symbol, type="market",
            side=order_side, amount=amount, params=params
        )
        return order
    except Exception as e:
        print(f"  Order error: {e}")
        return None


def close_position(exchange, symbol, direction, amount, pos_mode):
    """يغلق مركز Futures."""
    swap_symbol = symbol.replace("/", "-") + "-SWAP"
    close_side  = "sell" if direction == "long" else "buy"
    params      = {"posSide": direction} if pos_mode == "hedge" else {}
    try:
        exchange.create_order(
            symbol=swap_symbol, type="market",
            side=close_side, amount=amount, params=params
        )
        return True
    except Exception as e:
        print(f"  Close error: {e}")
        return False


# ─── الدوال الرئيسية ────────────────────────────────────────────────────────

def execute_signal(signal):
    """
    ينفّذ إشارة واحدة.
    signal: dict فيه symbol, direction, entry, stop, target1, target2, rsi
    """
    check_keys()
    exchange = connect()
    symbol    = signal["symbol"]
    direction = signal["direction"]
    key       = position_key(symbol, direction)

    # تحقق إن كان المركز مفتوحاً أصلاً
    positions = load_positions()
    if key in positions:
        print(f"  {symbol} {direction} already open — skipping.")
        return False

    # الرصيد والحجم
    balance = get_usdt_balance(exchange)
    if balance < 10:
        print(f"  Insufficient balance: {balance:.2f} USDT")
        return False

    amount, price = calc_amount(exchange, symbol, balance)
    if not amount:
        return False

    print(f"  Balance : {balance:,.2f} USDT")
    print(f"  Risk    : {balance*RISK_PCT:,.2f} USDT (2%)")
    print(f"  Price   : {price:,.4f}")
    print(f"  Amount  : {amount} {symbol.split('/')[0]}")

    # ضبط الرافعة وتنفيذ الأمر
    set_leverage(exchange, symbol)
    pos_mode = get_pos_mode(exchange)
    order    = open_position(exchange, symbol, direction, amount, pos_mode)

    if not order:
        return False

    # حفظ المركز
    positions[key] = {
        "symbol":    symbol,
        "direction": direction,
        "amount":    amount,
        "entry":     signal.get("entry", price),
        "stop":      signal.get("stop"),
        "target1":   signal.get("target1"),
        "target2":   signal.get("target2"),
        "rsi":       signal.get("rsi"),
        "order_id":  order.get("id"),
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    save_positions(positions)

    # إشعار Telegram
    direction_label = "LONG 🟢" if direction == "long" else "SHORT 🔴"
    send_telegram(
        f"✅ <b>Position Opened — OKX Demo</b>\n\n"
        f"Symbol:    <b>{symbol}</b>\n"
        f"Direction: <b>{direction_label}</b>\n"
        f"Entry:     {signal.get('entry', price):,.4f}\n"
        f"Stop:      {signal.get('stop', 0):,.4f}\n"
        f"Target 1:  {signal.get('target1', 0):,.4f}\n"
        f"RSI:       {signal.get('rsi', 0)}\n"
        f"Amount:    {amount} {symbol.split('/')[0]}\n"
        f"Risk:      ${balance*RISK_PCT:,.2f}"
    )

    print(f"  Position opened and saved.")
    return True


def check_and_close_positions():
    """
    يفحص المراكز المفتوحة ويغلق ما وصل للوقف أو الهدف.
    يُشغَّل في كل دورة مسح.
    """
    positions = load_positions()
    if not positions:
        return

    check_keys()
    exchange = connect()
    pos_mode = get_pos_mode(exchange)
    closed   = []

    for key, pos in positions.items():
        symbol    = pos["symbol"]
        direction = pos["direction"]
        stop      = pos.get("stop")
        target1   = pos.get("target1")

        try:
            ticker       = exchange.fetch_ticker(symbol)
            current_price = ticker["last"]
        except Exception:
            continue

        outcome = None
        if direction == "long":
            if stop and current_price <= stop:
                outcome = "STOP"
            elif target1 and current_price >= target1:
                outcome = "TARGET1"
        else:  # short
            if stop and current_price >= stop:
                outcome = "STOP"
            elif target1 and current_price <= target1:
                outcome = "TARGET1"

        if outcome:
            print(f"  {symbol} {direction} hit {outcome} at {current_price:,.4f}")
            success = close_position(
                exchange, symbol, direction, pos["amount"], pos_mode
            )
            if success:
                closed.append(key)
                emoji = "🔴" if outcome == "STOP" else "🟢"
                send_telegram(
                    f"{emoji} <b>Position Closed — {outcome}</b>\n\n"
                    f"Symbol:    <b>{symbol}</b>\n"
                    f"Direction: {direction.upper()}\n"
                    f"Entry:     {pos['entry']:,.4f}\n"
                    f"Close:     {current_price:,.4f}\n"
                    f"Outcome:   {outcome}"
                )

    # احذف المغلقة من الملف
    for key in closed:
        del positions[key]
    if closed:
        save_positions(positions)
        print(f"  Closed {len(closed)} position(s).")


def show_open_positions():
    """يعرض المراكز المفتوحة حالياً."""
    positions = load_positions()
    if not positions:
        print("No open positions.")
        return
    print(f"\nOpen positions ({len(positions)}):")
    for key, pos in positions.items():
        print(f"  {pos['symbol']:12} {pos['direction']:5} "
              f"entry={pos['entry']:.4f} "
              f"stop={pos.get('stop', 0):.4f} "
              f"T1={pos.get('target1', 0):.4f} "
              f"opened={pos['opened_at'][:16]}")


# ─── اختبار مباشر ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["test", "status", "check"],
                    default="test")
    ap.add_argument("--symbol",    default="BTC/USDT")
    ap.add_argument("--direction", default="long", choices=["long", "short"])
    args = ap.parse_args()

    if args.action == "status":
        show_open_positions()

    elif args.action == "check":
        print("Checking open positions against current prices...")
        check_and_close_positions()

    else:  # test
        print("=" * 52)
        print("  EXECUTION LAYER TEST — OKX Demo")
        print("=" * 52)
        test_signal = {
            "symbol":    args.symbol,
            "direction": args.direction,
            "entry":     0,       # سيُستبدل بالسعر الحالي
            "stop":      0,
            "target1":   0,
            "rsi":       25.0,
        }
        print(f"\nExecuting signal: {args.symbol} {args.direction}")
        result = execute_signal(test_signal)
        print(f"\nResult: {'SUCCESS' if result else 'FAILED or already open'}")
        print("\nCurrent positions:")
        show_open_positions()
