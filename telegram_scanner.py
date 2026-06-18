"""
telegram_scanner.py — اللبنة 2+3: مسح + إرسال + منع التكرار
=============================================================
يمسح العملات، يلتقط إشارات آخر شمعة (آخر 15 دقيقة)،
يرسل كل إشارة جديدة لـ Telegram، ويتذكر ما أرسله لتجنّب التكرار.

مفتاح الإشارة الفريد: رمز العملة + الاتجاه + وقت الشمعة
الذاكرة: sent_signals.json في مجلد المشروع

الأمان: التوكن من متغيّر بيئة TELEGRAM_TOKEN.

التشغيل:
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

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8589721199")

TF             = "15m"
DAYS           = 3
MAX_CANDLE_AGE = 15     # دقيقة — آخر شمعة فقط
SENT_FILE      = Path(__file__).parent / "sent_signals.json"
MAX_MEMORY     = 500    # أقصى عدد إشارات نحتفظ بها في الذاكرة (لتجنب تضخّم الملف)


# ─── الذاكرة ────────────────────────────────────────────────────────────────

def load_sent():
    """يقرأ الإشارات المُرسَلة سابقاً من الملف."""
    if not SENT_FILE.exists():
        return set()
    try:
        data = json.loads(SENT_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()


def save_sent(sent_set):
    """يحفظ الإشارات المُرسَلة — يحتفظ بآخر MAX_MEMORY فقط."""
    items = list(sent_set)[-MAX_MEMORY:]
    SENT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                         encoding="utf-8")


def signal_key(symbol, direction, ts):
    """مفتاح فريد لكل إشارة: عملة + اتجاه + وقت الشمعة."""
    return f"{symbol}|{direction}|{ts.strftime('%Y%m%d%H%M')}"


# ─── الإرسال ────────────────────────────────────────────────────────────────

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


def format_signal(symbol, sig, ts):
    direction = "LONG  🟢" if sig["direction"] == "long" else "SHORT 🔴"
    return (
        f"🔔 <b>New Signal — RSI Divergence</b>\n"
        f"\n"
        f"Symbol:    <b>{symbol}</b>\n"
        f"Direction: <b>{direction}</b>\n"
        f"Entry:     {sig['entry']:,.2f}\n"
        f"Stop:      {sig['stop']:,.2f}\n"
        f"Target 1:  {sig['target1']:,.2f}\n"
        f"Target 2:  {sig['target2']:,.2f}\n"
        f"RSI:       {sig['rsi']}\n"
        f"Time:      {ts.strftime('%Y-%m-%d %H:%M UTC')}"
    )


# ─── المسح ──────────────────────────────────────────────────────────────────

def scan():
    now     = datetime.now(timezone.utc)
    cutoff  = now - timedelta(minutes=MAX_CANDLE_AGE)
    found   = []

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


# ─── الرئيسي ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    signals  = scan()
    sent_set = load_sent()

    if not signals:
        print("No new signals in the last 15 minutes.")
        sys.exit(0)

    new_count = sent_count = skipped = 0

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
            print(f"  + {symbol} {sig['direction']} @ {sig['entry']:.2f} — sent.")
        else:
            print(f"  ! {symbol} {sig['direction']} — send failed.")

        new_count += 1

    save_sent(sent_set)

    print(f"\nDone: {sent_count} sent, {skipped} skipped (already sent).")
