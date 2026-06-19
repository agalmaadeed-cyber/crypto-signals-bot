"""
okx_test.py — اختبار الاتصال بـ OKX Demo Trading
==================================================
يتصل بـ OKX Demo، يقرأ الرصيد الوهمي.
أول خطوة في بناء EXECUTION LAYER.

الأمان: المفاتيح من متغيرات البيئة — لا تُكتب في الكود أبداً.

التشغيل (في Claude Code):
  python okx_test.py
"""

import os
import sys

# نتحقق من المكتبة أولاً
try:
    import ccxt
except ImportError:
    print("ERROR: ccxt not installed.")
    print("Run: pip install ccxt")
    sys.exit(1)

# المفاتيح من البيئة
API_KEY     = os.environ.get("OKX_API_KEY")
SECRET_KEY  = os.environ.get("OKX_SECRET_KEY")
PASSPHRASE  = os.environ.get("OKX_PASSPHRASE")

def check_keys():
    missing = []
    if not API_KEY:    missing.append("OKX_API_KEY")
    if not SECRET_KEY: missing.append("OKX_SECRET_KEY")
    if not PASSPHRASE: missing.append("OKX_PASSPHRASE")
    if missing:
        print(f"ERROR: Missing environment variables: {', '.join(missing)}")
        print("Set them first, then re-run.")
        sys.exit(1)

def connect_demo():
    """يتصل بـ OKX Demo Trading."""
    exchange = ccxt.okx({
        "apiKey":     API_KEY,
        "secret":     SECRET_KEY,
        "password":   PASSPHRASE,   # OKX يسمي الـ Passphrase بـ password في ccxt
        "sandbox":    True,          # Demo Trading — أموال وهمية
        "options": {
            "defaultType": "spot",   # تداول فوري (Spot)
        }
    })
    return exchange

def test_connection(exchange):
    """يختبر الاتصال بقراءة الرصيد."""
    print("Connecting to OKX Demo Trading...")
    try:
        balance = exchange.fetch_balance()
        print("Connection: SUCCESS")
        print("\nDemo Balance (non-zero only):")
        found = False
        for currency, data in balance["total"].items():
            if data > 0:
                print(f"  {currency}: {data:,.4f}")
                found = True
        if not found:
            print("  (No balance yet — may need to request demo funds from OKX)")
        return True
    except ccxt.AuthenticationError as e:
        print(f"FAILED: Authentication error — {e}")
        print("Check your API Key, Secret, and Passphrase.")
        return False
    except ccxt.NetworkError as e:
        print(f"FAILED: Network error — {e}")
        return False
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        return False

if __name__ == "__main__":
    check_keys()
    exchange = connect_demo()
    test_connection(exchange)
