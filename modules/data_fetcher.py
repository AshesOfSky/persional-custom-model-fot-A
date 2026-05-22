"""
data_fetcher.py — yfinance 数据获取模块
支持 A股(.SS/.SZ)、港股(.HK)、美股
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta


# 周期映射: (yf_period, interval) 用于日线
PERIOD_MAP = {
    "1M":  ("1mo",  "1d"),
    "3M":  ("3mo",  "1d"),
    "6M":  ("6mo",  "1d"),
    "1Y":  ("1y",   "1d"),
    "2Y":  ("2y",   "1wk"),
    "5Y":  ("5y",   "1wk"),
}

# 多时间框架映射: timeframe -> {period -> (yf_period, interval)}
TIMEFRAME_MAP = {
    "日线": {
        "1M": ("1mo", "1d"), "3M": ("3mo", "1d"), "6M": ("6mo", "1d"),
        "1Y": ("1y", "1d"), "2Y": ("2y", "1d"), "5Y": ("5y", "1d"),
    },
    "周线": {
        "1M": ("1mo", "1wk"), "3M": ("3mo", "1wk"), "6M": ("6mo", "1wk"),
        "1Y": ("1y", "1wk"), "2Y": ("2y", "1wk"), "5Y": ("5y", "1wk"), "10Y": ("10y", "1wk"),
    },
    "月线": {
        "1Y": ("1y", "1mo"), "2Y": ("2y", "1mo"), "5Y": ("5y", "1mo"), "10Y": ("10y", "1mo"), "MAX": ("max", "1mo"),
    },
    "小时线": {
        "1D": ("1d", "1h"), "3D": ("5d", "1h"), "1W": ("1wk", "1h"), "2W": ("1mo", "1h"), "1M": ("3mo", "1h"),
    },
}

def detect_market(ticker: str) -> str:
    """识别市场类型"""
    t = ticker.upper()
    if t.endswith(".SS"):
        return "沪市A股"
    elif t.endswith(".SZ"):
        return "深市A股"
    elif t.endswith(".HK"):
        return "港股"
    else:
        return "美股"


def normalize_ticker(ticker: str) -> str:
    """标准化股票代码"""
    return ticker.strip().upper()


def fetch_ohlcv(ticker: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    拉取 OHLCV 数据
    支持多时间框架: 日线/周线/月线/小时线
    返回 DataFrame，列：Open, High, Low, Close, Volume
    """
    t = normalize_ticker(ticker)

    # 根据时间框架获取对应的周期映射
    tf_map = TIMEFRAME_MAP.get(timeframe, TIMEFRAME_MAP["日线"])
    yf_period, interval = tf_map.get(period, ("1y", "1d"))

    try:
        df = yf.download(
            t,
            period=yf_period,
            interval=interval,
            progress=False,
            auto_adjust=True,
        )
        if df.empty:
            return pd.DataFrame()

        # 展平多级列（yfinance 0.2.x 返回 MultiIndex）
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        raise ValueError(f"获取 {t} 行情数据失败: {e}")


def fetch_info(ticker: str) -> dict:
    """拉取股票基本信息"""
    t = normalize_ticker(ticker)
    try:
        stock = yf.Ticker(t)
        info = stock.info or {}
        return info
    except Exception:
        return {}


def fetch_financials(ticker: str) -> dict:
    """
    拉取财务报表数据
    返回包含 income_stmt / balance_sheet / cashflow 的字典
    """
    t = normalize_ticker(ticker)
    result = {
        "income_stmt": pd.DataFrame(),
        "balance_sheet": pd.DataFrame(),
        "cashflow": pd.DataFrame(),
    }
    try:
        stock = yf.Ticker(t)
        result["income_stmt"] = stock.income_stmt
        result["balance_sheet"] = stock.balance_sheet
        result["cashflow"] = stock.cashflow
    except Exception:
        pass
    return result


def fetch_all(ticker: str, period: str = "1Y", timeframe: str = "日线") -> dict:
    """统一入口：拉取所有数据"""
    ohlcv = fetch_ohlcv(ticker, period, timeframe)
    info = fetch_info(ticker)
    financials = fetch_financials(ticker)

    return {
        "ticker": normalize_ticker(ticker),
        "market": detect_market(ticker),
        "period": period,
        "timeframe": timeframe,
        "ohlcv": ohlcv,
        "info": info,
        **financials,
    }
