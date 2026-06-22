"""
حسابات المؤشرات الفنية المشتركة بين الأنماط
"""

import pandas as pd
import numpy as np


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """إضافة كل المؤشرات على DataFrame شمعي"""
    df = df.copy()

    # ── المتوسطات المتحركة ──────────────────────────────────────────
    df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["sma20"] = df["close"].rolling(20).mean()

    # ── RSI ─────────────────────────────────────────────────────────
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ── VWAP اليومي ──────────────────────────────────────────────────
    df["typical_price"] = (df["high"] + df["low"] + df["close"]) / 3
    df["date"] = df.index.date
    df["tp_vol"]     = df["typical_price"] * df["volume"]
    df["cum_tp_vol"] = df.groupby("date")["tp_vol"].transform("cumsum")
    df["cum_vol"]    = df.groupby("date")["volume"].transform("cumsum")
    df["vwap"] = df["cum_tp_vol"] / df["cum_vol"].replace(0, np.nan)
    df.drop(columns=["typical_price", "tp_vol", "cum_tp_vol", "cum_vol", "date"], inplace=True)

    # ── ATR ──────────────────────────────────────────────────────────
    high_low = df["high"] - df["low"]
    high_pc  = (df["high"] - df["close"].shift()).abs()
    low_pc   = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_pc, low_pc], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()

    # ── حجم نسبي ─────────────────────────────────────────────────────
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_ma20"].replace(0, np.nan)

    # ── Bollinger Bands ──────────────────────────────────────────────
    df["bb_mid"]   = df["sma20"]
    df["bb_std"]   = df["close"].rolling(20).std()
    df["bb_upper"] = df["bb_mid"] + 2 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2 * df["bb_std"]
    df["bb_pct"]   = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)

    return df


def swing_highs(series: pd.Series, window: int = 5) -> pd.Series:
    """نقاط القمة المحلية"""
    return series == series.rolling(window * 2 + 1, center=True).max()


def swing_lows(series: pd.Series, window: int = 5) -> pd.Series:
    """نقاط القاع المحلية"""
    return series == series.rolling(window * 2 + 1, center=True).min()
