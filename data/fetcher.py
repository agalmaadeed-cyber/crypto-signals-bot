"""
Crypto data fetcher — tries Binance → Kraken → Yahoo Finance (fallback chain)
"""

import ccxt
import pandas as pd
import time
import yfinance as yf
from datetime import datetime, timedelta
from pathlib import Path

TOP_20 = [
    "BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "SOL/USDT",
    "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "TRX/USDT", "LINK/USDT",
    "DOT/USDT", "MATIC/USDT", "UNI/USDT", "LTC/USDT", "BCH/USDT",
    "ATOM/USDT", "NEAR/USDT", "FIL/USDT", "APT/USDT", "ARB/USDT",
]

KRAKEN_MAP = {
    "BTC/USDT": "BTC/USD",   "ETH/USDT": "ETH/USD",   "SOL/USDT": "SOL/USD",
    "XRP/USDT": "XRP/USD",   "ADA/USDT": "ADA/USD",   "DOGE/USDT": "DOGE/USD",
    "AVAX/USDT": "AVAX/USD", "LINK/USDT": "LINK/USD", "DOT/USDT": "DOT/USD",
    "LTC/USDT": "LTC/USD",   "BCH/USDT": "BCH/USD",   "ATOM/USDT": "ATOM/USD",
    "NEAR/USDT": "NEAR/USD", "UNI/USDT": "UNI/USD",   "FIL/USDT": "FIL/USD",
    "BNB/USDT": "BNB/USD",   "TRX/USDT": "TRX/USD",   "ARB/USDT": "ARB/USD",
    "APT/USDT": "APT/USD",   "MATIC/USDT": "MATIC/USD",
}

YAHOO_MAP = {
    "BTC/USDT": "BTC-USD",      "ETH/USDT": "ETH-USD",   "BNB/USDT": "BNB-USD",
    "XRP/USDT": "XRP-USD",      "SOL/USDT": "SOL-USD",   "ADA/USDT": "ADA-USD",
    "DOGE/USDT": "DOGE-USD",    "AVAX/USDT": "AVAX-USD", "TRX/USDT": "TRX-USD",
    "LINK/USDT": "LINK-USD",    "DOT/USDT": "DOT-USD",   "MATIC/USDT": "MATIC-USD",
    "UNI/USDT": "UNI-USD",      "LTC/USDT": "LTC-USD",   "BCH/USDT": "BCH-USD",
    "ATOM/USDT": "ATOM-USD",    "NEAR/USDT": "NEAR-USD", "FIL/USDT": "FIL-USD",
    "APT/USDT": "APT21794-USD", "ARB/USDT": "ARB11841-USD",
}

YAHOO_DAY_LIMIT = {"5m": 59, "15m": 59, "1h": 729}
TIMEFRAMES = {"1h": "1h", "15m": "15m", "5m": "5m"}
CACHE_DIR = Path.home() / ".cache" / "crypto_cache"
CACHE_DIR.mkdir(exist_ok=True)


def _get_binance():
    return ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})

def _get_kraken():
    return ccxt.kraken({"enableRateLimit": True})

def _fetch_ccxt(exchange, symbol, timeframe, days):
    since = exchange.parse8601(
        (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")
    )
    all_candles = []
    while True:
        candles = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not candles:
            break
        all_candles.extend(candles)
        since = candles[-1][0] + 1
        if len(candles) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)
    if not all_candles:
        return pd.DataFrame()
    df = pd.DataFrame(all_candles, columns=["timestamp","open","high","low","close","volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    return df[~df.index.duplicated()].sort_index()

def _fetch_yahoo(symbol, timeframe, days):
    ticker = YAHOO_MAP.get(symbol)
    if not ticker:
        return pd.DataFrame()
    days = min(days, YAHOO_DAY_LIMIT.get(timeframe, 59))
    end, start = datetime.utcnow(), datetime.utcnow() - timedelta(days=days)
    try:
        raw = yf.download(ticker, start=start.strftime("%Y-%m-%d"),
                          end=end.strftime("%Y-%m-%d"), interval=timeframe,
                          progress=False, auto_adjust=True)
    except Exception:
        return pd.DataFrame()
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw = raw.rename(columns=str.lower)
    if not {"open","high","low","close","volume"}.issubset(set(raw.columns)):
        return pd.DataFrame()
    df = raw[["open","high","low","close","volume"]].copy()
    df.index = pd.to_datetime(df.index, utc=True)
    return df[~df.index.duplicated()].sort_index()

def fetch_ohlcv(symbol: str, timeframe: str, days: int = 90, use_cache: bool = True) -> pd.DataFrame:
    cache_file = CACHE_DIR / f"{symbol.replace('/','_')}_{timeframe}.parquet"
    if use_cache and cache_file.exists():
        if (time.time() - cache_file.stat().st_mtime) / 3600 < 1:
            try:
                return pd.read_parquet(cache_file)
            except Exception:
                pass
    df = pd.DataFrame()
    try:
        df = _fetch_ccxt(_get_binance(), symbol, timeframe, days)
    except Exception:
        pass
    if df.empty:
        try:
            df = _fetch_ccxt(_get_kraken(), KRAKEN_MAP.get(symbol, symbol.replace("USDT","USD")), timeframe, days)
        except Exception:
            pass
    if df.empty:
        try:
            df = _fetch_yahoo(symbol, timeframe, days)
        except Exception:
            pass
    if df.empty:
        return pd.DataFrame()
    try:
        df.to_parquet(cache_file)
    except Exception:
        pass
    return df

def fetch_all(symbols=None, timeframes=None, days=90):
    symbols = symbols or TOP_20
    timeframes = timeframes or list(TIMEFRAMES.values())
    data, total, done = {}, len(symbols)*len(timeframes), 0
    for symbol in symbols:
        data[symbol] = {}
        for tf in timeframes:
            done += 1
            print(f"  [{done}/{total}] {symbol} {tf} ... ", end="", flush=True)
            df = fetch_ohlcv(symbol, tf, days=days)
            if not df.empty:
                data[symbol][tf] = df
                print(f"✅ {len(df)} candles")
            else:
                print("⚠️  empty")
        time.sleep(0.1)
    return data
