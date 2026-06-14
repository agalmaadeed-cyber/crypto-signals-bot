"""
محرك اكتشاف الأنماط — ستة أنماط day trading للكريبتو

كل نمط يُرجع DataFrame بالإشارات:
  index = وقت الإشارة
  entry, stop, target1, target2, direction
"""

import pandas as pd
import numpy as np
from .indicators import add_indicators, swing_highs, swing_lows


# ═══════════════════════════════════════════════════════════════════════
# 1. BREAKOUT — اختراق مستوى مقاومة مع حجم
# ═══════════════════════════════════════════════════════════════════════
def detect_breakout(df: pd.DataFrame, lookback: int = 20, vol_mult: float = 1.5) -> pd.DataFrame:
    """
    السعر يكسر أعلى قمة N شمعة + الحجم > vol_mult × المتوسط
    دخول: إغلاق الشمعة فوق المقاومة
    وقف: أدنى الشمعة الكاسرة
    هدف: 2× المسافة من الوقف
    """
    df = add_indicators(df)
    signals = []

    for i in range(lookback, len(df) - 1):
        window = df.iloc[i - lookback: i]
        resistance = window["high"].max()
        candle = df.iloc[i]

        if (candle["close"] > resistance and
                candle["vol_ratio"] >= vol_mult and
                candle["close"] > candle["open"]):  # شمعة صاعدة

            risk = candle["close"] - candle["low"]
            if risk <= 0:
                continue

            signals.append({
                "timestamp": df.index[i],
                "pattern":   "Breakout",
                "direction": "long",
                "entry":     candle["close"],
                "stop":      candle["low"],
                "target1":   candle["close"] + risk * 1.5,
                "target2":   candle["close"] + risk * 3.0,
                "vol_ratio": round(candle["vol_ratio"], 2),
            })

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# 2. VWAP REVERSION — ارتداد من VWAP
# ═══════════════════════════════════════════════════════════════════════
def detect_vwap_reversion(df: pd.DataFrame, dev_pct: float = 1.5) -> pd.DataFrame:
    """
    السعر يبتعد > dev_pct% من VWAP ثم يعود إليه
    يصطاد الانعكاس نحو VWAP
    """
    df = add_indicators(df)
    signals = []

    for i in range(21, len(df) - 1):
        candle = df.iloc[i]
        prev   = df.iloc[i - 1]

        if pd.isna(candle["vwap"]):
            continue

        vwap_dev = (candle["close"] - candle["vwap"]) / candle["vwap"] * 100

        # سعر بعيد كثيراً تحت VWAP + بداية الارتداد
        if (vwap_dev < -dev_pct and
                candle["close"] > prev["close"] and
                candle["rsi"] < 40):

            risk = candle["atr14"]
            signals.append({
                "timestamp": df.index[i],
                "pattern":   "VWAP_Reversion",
                "direction": "long",
                "entry":     candle["close"],
                "stop":      candle["close"] - risk,
                "target1":   candle["vwap"],
                "target2":   candle["vwap"] + risk * 0.5,
                "vwap_dev":  round(vwap_dev, 2),
            })

        # سعر بعيد كثيراً فوق VWAP + بداية الانعكاس
        elif (vwap_dev > dev_pct and
              candle["close"] < prev["close"] and
              candle["rsi"] > 60):

            risk = candle["atr14"]
            signals.append({
                "timestamp": df.index[i],
                "pattern":   "VWAP_Reversion",
                "direction": "short",
                "entry":     candle["close"],
                "stop":      candle["close"] + risk,
                "target1":   candle["vwap"],
                "target2":   candle["vwap"] - risk * 0.5,
                "vwap_dev":  round(vwap_dev, 2),
            })

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# 3. RSI DIVERGENCE — تباين RSI
# ═══════════════════════════════════════════════════════════════════════
def detect_rsi_divergence(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    تباين صاعد: السعر يصنع قاعاً أدنى لكن RSI يصنع قاعاً أعلى → إشارة شراء
    تباين هابط: السعر يصنع قمة أعلى لكن RSI يصنع قمة أدنى → إشارة بيع
    """
    df = add_indicators(df)
    signals = []

    lows_price  = swing_lows(df["close"], window)
    highs_price = swing_highs(df["close"], window)
    lows_rsi    = swing_lows(df["rsi"], window)
    highs_rsi   = swing_highs(df["rsi"], window)

    low_idx  = df.index[lows_price].tolist()
    high_idx = df.index[highs_price].tolist()

    # تباين صاعد (bullish)
    for j in range(1, len(low_idx)):
        t1, t2 = low_idx[j - 1], low_idx[j]
        p1 = df.loc[t1, "close"]
        p2 = df.loc[t2, "close"]
        r1 = df.loc[t1, "rsi"]
        r2 = df.loc[t2, "rsi"]
        if p2 < p1 and r2 > r1 and r2 < 40:  # سعر أدنى + RSI أعلى في منطقة ذعر
            candle = df.loc[t2]
            risk   = candle["atr14"]
            signals.append({
                "timestamp": t2,
                "pattern":   "RSI_Divergence_Bull",
                "direction": "long",
                "entry":     candle["close"],
                "stop":      candle["close"] - risk * 1.5,
                "target1":   candle["close"] + risk * 2,
                "target2":   candle["close"] + risk * 4,
                "rsi":       round(r2, 1),
            })

    # تباين هابط (bearish)
    for j in range(1, len(high_idx)):
        t1, t2 = high_idx[j - 1], high_idx[j]
        p1 = df.loc[t1, "close"]
        p2 = df.loc[t2, "close"]
        r1 = df.loc[t1, "rsi"]
        r2 = df.loc[t2, "rsi"]
        if p2 > p1 and r2 < r1 and r2 > 60:  # سعر أعلى + RSI أدنى في منطقة جشع
            candle = df.loc[t2]
            risk   = candle["atr14"]
            signals.append({
                "timestamp": t2,
                "pattern":   "RSI_Divergence_Bear",
                "direction": "short",
                "entry":     candle["close"],
                "stop":      candle["close"] + risk * 1.5,
                "target1":   candle["close"] - risk * 2,
                "target2":   candle["close"] - risk * 4,
                "rsi":       round(r2, 1),
            })

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# 4. VOLUME SPIKE MOMENTUM — زخم انفجار الحجم
# ═══════════════════════════════════════════════════════════════════════
def detect_volume_spike(df: pd.DataFrame, vol_mult: float = 3.0, body_pct: float = 0.6) -> pd.DataFrame:
    """
    شمعة بحجم > 3× المتوسط + جسم يمثل > 60% من المدى → زخم
    يركب الزخم في اتجاه الشمعة
    """
    df = add_indicators(df)
    signals = []

    for i in range(21, len(df) - 1):
        candle = df.iloc[i]
        body   = abs(candle["close"] - candle["open"])
        rng    = candle["high"] - candle["low"]

        if rng == 0:
            continue

        body_ratio = body / rng
        direction  = "long" if candle["close"] > candle["open"] else "short"
        risk       = candle["atr14"]

        if candle["vol_ratio"] >= vol_mult and body_ratio >= body_pct:
            if direction == "long":
                signals.append({
                    "timestamp": df.index[i],
                    "pattern":   "Volume_Spike",
                    "direction": "long",
                    "entry":     candle["close"],
                    "stop":      candle["low"],
                    "target1":   candle["close"] + risk,
                    "target2":   candle["close"] + risk * 2.5,
                    "vol_ratio": round(candle["vol_ratio"], 1),
                })
            else:
                signals.append({
                    "timestamp": df.index[i],
                    "pattern":   "Volume_Spike",
                    "direction": "short",
                    "entry":     candle["close"],
                    "stop":      candle["high"],
                    "target1":   candle["close"] - risk,
                    "target2":   candle["close"] - risk * 2.5,
                    "vol_ratio": round(candle["vol_ratio"], 1),
                })

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# 5. SUPPORT BOUNCE — ارتداد من دعم قوي
# ═══════════════════════════════════════════════════════════════════════
def detect_support_bounce(df: pd.DataFrame, zone_pct: float = 0.3, touches: int = 2) -> pd.DataFrame:
    """
    تحديد مناطق دعم بناءً على تكرار الارتداد منها
    الدخول عند لمس الدعم + شمعة انعكاس
    """
    df = add_indicators(df)
    signals = []

    # بناء مناطق الدعم من القيعان المتكررة
    lows = df["low"].values
    support_zones = []
    used = set()

    for i in range(len(lows)):
        if i in used:
            continue
        zone_min = lows[i] * (1 - zone_pct / 100)
        zone_max = lows[i] * (1 + zone_pct / 100)
        count = sum(1 for j, l in enumerate(lows) if zone_min <= l <= zone_max)
        if count >= touches:
            support_zones.append((zone_min, zone_max, lows[i]))
            used.update(j for j, l in enumerate(lows) if zone_min <= l <= zone_max)

    for i in range(50, len(df) - 1):
        candle = df.iloc[i]
        prev   = df.iloc[i - 1]

        for z_min, z_max, z_base in support_zones:
            # السعر لمس الدعم وانعكس
            if (candle["low"] <= z_max and
                    candle["close"] > candle["open"] and       # شمعة صاعدة
                    candle["close"] > prev["close"] and        # فوق الشمعة السابقة
                    candle["rsi"] < 50):                       # ليس في منطقة تشبع

                risk = candle["close"] - candle["low"]
                if risk <= 0:
                    continue

                signals.append({
                    "timestamp":    df.index[i],
                    "pattern":      "Support_Bounce",
                    "direction":    "long",
                    "entry":        candle["close"],
                    "stop":         candle["low"] - candle["atr14"] * 0.3,
                    "target1":      candle["close"] + risk * 2,
                    "target2":      candle["close"] + risk * 4,
                    "support_zone": round(z_base, 4),
                })
                break  # إشارة واحدة لكل شمعة

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# 6. EMA CROSS MOMENTUM — تقاطع EMA مع زخم
# ═══════════════════════════════════════════════════════════════════════
def detect_ema_cross(df: pd.DataFrame) -> pd.DataFrame:
    """
    EMA9 تعبر فوق EMA21 + السعر فوق EMA50 + حجم طبيعي → زخم صاعد
    EMA9 تعبر تحت EMA21 + السعر تحت EMA50 → زخم هابط
    """
    df = add_indicators(df)
    signals = []

    for i in range(51, len(df) - 1):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        # تقاطع صاعد
        if (prev["ema9"] <= prev["ema21"] and
                curr["ema9"] > curr["ema21"] and
                curr["close"] > curr["ema50"] and
                curr["vol_ratio"] > 1.0):

            risk = curr["atr14"]
            signals.append({
                "timestamp": df.index[i],
                "pattern":   "EMA_Cross",
                "direction": "long",
                "entry":     curr["close"],
                "stop":      curr["ema21"],
                "target1":   curr["close"] + risk * 1.5,
                "target2":   curr["close"] + risk * 3,
                "vol_ratio": round(curr["vol_ratio"], 2),
            })

        # تقاطع هابط
        elif (prev["ema9"] >= prev["ema21"] and
              curr["ema9"] < curr["ema21"] and
              curr["close"] < curr["ema50"] and
              curr["vol_ratio"] > 1.0):

            risk = curr["atr14"]
            signals.append({
                "timestamp": df.index[i],
                "pattern":   "EMA_Cross",
                "direction": "short",
                "entry":     curr["close"],
                "stop":      curr["ema21"],
                "target1":   curr["close"] - risk * 1.5,
                "target2":   curr["close"] - risk * 3,
                "vol_ratio": round(curr["vol_ratio"], 2),
            })

    return pd.DataFrame(signals).set_index("timestamp") if signals else pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════════
# تشغيل كل الأنماط دفعة واحدة
# ═══════════════════════════════════════════════════════════════════════
ALL_PATTERNS = {
    "Breakout":          detect_breakout,
    "VWAP_Reversion":    detect_vwap_reversion,
    "RSI_Divergence":    detect_rsi_divergence,
    "Volume_Spike":      detect_volume_spike,
    "Support_Bounce":    detect_support_bounce,
    "EMA_Cross":         detect_ema_cross,
}


def detect_all(df: pd.DataFrame) -> pd.DataFrame:
    """تشغيل كل الأنماط وجمع الإشارات في DataFrame واحد"""
    results = []
    for name, func in ALL_PATTERNS.items():
        try:
            sigs = func(df)
            if not sigs.empty:
                results.append(sigs)
        except Exception as e:
            print(f"  ⚠️  خطأ في نمط {name}: {e}")
    return pd.concat(results).sort_index() if results else pd.DataFrame()
