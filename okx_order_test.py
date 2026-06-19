"""
okx_order_test.py — اختبار فتح صفقة تجريبية على OKX Demo
==========================================================
يفتح صفقة Futures صغيرة جداً (long أو short) على OKX Demo.
بدون رافعة مالية (leverage = 1).

الهدف: التأكد أن الكود يستطيع إرسال أمر حقيقي قبل ربطه بالإشارات.

التشغيل:
  python okx_order_test.py --symbol BTC/USDT --side long
  python okx_order_test.py --symbol BTC/USDT --side short
"""

import os
import sys
import argparse

try:
    import ccxt
except ImportError:
    print("ERROR: ccxt not installed. Run: pip install ccxt")
    sys.exit(1)

API_KEY    = os.environ.get("OKX_API_KEY")
SECRET_KEY = os.environ.get("OKX_SECRET_KEY")
PASSPHRASE = os.environ.get("OKX_PASSPHRASE")

# أصغر حجم صفقة للاختبار (بالعملة الأساسية)
# BTC: 0.001 = ~$64 على سعر 64,000
TEST_AMOUNTS = {
    "BTC/USDT":  0.001,
    "ETH/USDT":  0.01,
    "SOL/USDT":  0.1,
    "default":   1.0,
}


def check_keys():
    missing = [k for k, v in {
        "OKX_API_KEY": API_KEY,
        "OKX_SECRET_KEY": SECRET_KEY,
        "OKX_PASSPHRASE": PASSPHRASE
    }.items() if not v]
    if missing:
        print(f"ERROR: Missing: {', '.join(missing)}")
        sys.exit(1)


def connect():
    return ccxt.okx({
        "apiKey":   API_KEY,
        "secret":   SECRET_KEY,
        "password": PASSPHRASE,
        "sandbox":  True,
        "options":  {"defaultType": "swap"},  # Futures دائمة (Perpetual)
    })


def set_leverage(exchange, symbol, leverage=1):
    """يضبط الرافعة على 1 (بلا رافعة فعلية)."""
    try:
        # OKX يحتاج رمز بصيغة مختلفة للـ Futures
        market_id = symbol.replace("/", "-") + "-SWAP"
        exchange.set_leverage(leverage, market_id, params={
            "mgnMode": "cross"  # Cross margin
        })
        print(f"  Leverage set to {leverage}x (no leverage)")
    except Exception as e:
        print(f"  Leverage note: {e} (may already be set)")


def get_pos_mode(exchange, symbol):
    """Returns 'hedge' or 'oneway' based on account position mode."""
    try:
        market_id = symbol.replace("/", "-") + "-SWAP"
        resp = exchange.private_get_account_config()
        pos_mode = resp.get("data", [{}])[0].get("posMode", "net_mode")
        return "hedge" if pos_mode == "long_short_mode" else "oneway"
    except Exception:
        return "oneway"


def place_order(exchange, symbol, side, amount):
    """
    يفتح صفقة Futures.
    side: 'long' أو 'short'
    """
    pos_mode   = get_pos_mode(exchange, symbol)
    order_side = "buy" if side == "long" else "sell"

    # posSide only valid in hedge mode; omit in one-way mode
    if pos_mode == "hedge":
        params = {"posSide": side}
    else:
        params = {}

    print(f"\nPlacing {side.upper()} order (pos mode: {pos_mode}):")
    print(f"  Symbol : {symbol}")
    print(f"  Amount : {amount}")
    print(f"  Type   : Market (instant execution)")

    try:
        order = exchange.create_order(
            symbol=symbol,
            type="market",
            side=order_side,
            amount=amount,
            params=params
        )
        print(f"\n  SUCCESS: Order placed!")
        print(f"  Order ID : {order.get('id', 'N/A')}")
        print(f"  Status   : {order.get('status', 'N/A')}")
        print(f"  Price    : {order.get('price') or order.get('average', 'market')}")
        return order
    except ccxt.InsufficientFunds as e:
        print(f"\n  FAILED: Insufficient funds — {e}")
        return None
    except ccxt.InvalidOrder as e:
        print(f"\n  FAILED: Invalid order — {e}")
        return None
    except Exception as e:
        print(f"\n  FAILED: {type(e).__name__}: {e}")
        return None


def close_order(exchange, symbol, side, amount):
    """يغلق الصفقة المفتوحة."""
    close_side = "sell" if side == "long" else "buy"
    pos_mode   = get_pos_mode(exchange, symbol)

    if pos_mode == "hedge":
        params = {"posSide": side, "reduceOnly": True}
    else:
        params = {}

    print(f"\nClosing {side.upper()} position...")
    try:
        order = exchange.create_order(
            symbol=symbol,
            type="market",
            side=close_side,
            amount=amount,
            params=params
        )
        print(f"  Position closed successfully.")
        return order
    except Exception as e:
        print(f"  Close note: {e}")
        return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--side",   default="long", choices=["long", "short"])
    ap.add_argument("--close",  action="store_true",
                    help="أغلق الصفقة بعد فتحها (للاختبار الكامل)")
    args = ap.parse_args()

    check_keys()
    exchange = connect()
    amount   = TEST_AMOUNTS.get(args.symbol, TEST_AMOUNTS["default"])

    print("=" * 50)
    print("  OKX DEMO — Order Test")
    print(f"  Mode: Futures Perpetual | Leverage: 1x")
    print("=" * 50)

    # اضبط الرافعة أولاً
    set_leverage(exchange, args.symbol, leverage=1)

    # افتح الصفقة
    order = place_order(exchange, args.symbol, args.side, amount)

    # أغلق الصفقة إن طُلب (للاختبار الكامل)
    if order and args.close:
        import time
        print("\nWaiting 3 seconds before closing...")
        time.sleep(3)
        close_order(exchange, args.symbol, args.side, amount)

    print("\nDone. Check OKX Demo Trading to verify the position.")
