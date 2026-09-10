"""纯 pandas/numpy 技术指标 — 契约禁止依赖 ta 库。

EMA8/21/55/144、BOLL(20,2)、VWAP、MACD(12,26,9, hist=(DIF-DEA)*2)、
KDJ(9,3,3)、RSI14；周线/月线由日线重采样。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV_AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def boll(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)  # A 股软件惯例：总体标准差
    return mid, mid + k * std, mid - k * std


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    dif = ema(close, fast) - ema(close, slow)
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2.0  # A 股软件惯例 ×2
    return dif, dea, hist


def kdj(df: pd.DataFrame, n: int = 9):
    llv = df["low"].rolling(n).min()
    hhv = df["high"].rolling(n).max()
    rsv = (df["close"] - llv) / (hhv - llv).replace(0, np.nan) * 100.0
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return k, d, j


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    au = up.ewm(alpha=1 / n, adjust=False).mean()   # Wilder 平滑
    ad = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = au / ad.replace(0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def vwap_daily_window(df: pd.DataFrame) -> pd.Series:
    """日线：窗口内累计典型价 VWAP（在显示窗口上调用）。"""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    return (typical * vol).cumsum() / vol.cumsum()


def vwap_intraday(df: pd.DataFrame) -> pd.Series:
    """分钟线：日内锚定 VWAP（每个交易日重新累计）。"""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    pv = typical * vol
    day = df.index.normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_v = vol.groupby(day).cumsum()
    return cum_pv / cum_v


def resample_week(df: pd.DataFrame) -> pd.DataFrame:
    agg = {**OHLCV_AGG, **({"amount": "sum"} if "amount" in df.columns else {})}
    out = df.resample("W-FRI").agg(agg).dropna(subset=["close"])
    out.index.name = df.index.name
    return out


def resample_month(df: pd.DataFrame) -> pd.DataFrame:
    agg = {**OHLCV_AGG, **({"amount": "sum"} if "amount" in df.columns else {})}
    out = df.resample("ME").agg(agg).dropna(subset=["close"])  # pandas 3 用 ME
    out.index.name = df.index.name
    return out


def compute_all(df: pd.DataFrame) -> dict:
    """在给定 K 线序列上计算全部指标，返回 Series 字典。"""
    close = df["close"]
    mid, upper, lower = boll(close)
    dif, dea, hist = macd(close)
    k, d, j = kdj(df)
    return {
        "ema8": ema(close, 8),
        "ema21": ema(close, 21),
        "ema55": ema(close, 55),
        "ema144": ema(close, 144),
        "boll_mid": mid,
        "boll_upper": upper,
        "boll_lower": lower,
        "dif": dif,
        "dea": dea,
        "hist": hist,
        "k": k,
        "d": d,
        "j": j,
        "rsi14": rsi(close, 14),
    }
