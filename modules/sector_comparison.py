"""
sector_comparison.py — 行业对比分析
计算个股与行业/板块的相对强弱，判断是否跑赢/跑输行业
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# 美股行业ETF映射
US_SECTOR_ETFS = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Basic Materials": "XLB",
    "Communication Services": "XLC",
}

# A股常用板块指数（akshare板块名 → 指数代码）
A_SHARE_SECTOR_MAP = {
    "白酒": "399998.SZ",    # 中证白酒
    "银行": "399986.SZ",    # 中证银行
    "医药": "399989.SZ",    # 中证医疗
    "半导体": "399959.SZ",  # 半导体50
    "新能源": "399808.SZ",  # 中证新能源
    "消费": "399932.SZ",    # 中证消费
    "军工": "399959.SZ",    # 国防军工
}


def get_sector_benchmark(ticker: str, info: dict = None) -> Tuple[str, str]:
    """
    根据股票信息自动选择行业基准

    Returns:
        (benchmark_ticker, benchmark_name)
    """
    if info is None:
        info = {}

    sector = info.get("sector", "")
    industry = info.get("industry", "")

    # 美股：通过yfinance sector映射到行业ETF
    if not ticker.endswith((".SS", ".SZ", ".HK")):
        etf = US_SECTOR_ETFS.get(sector)
        if etf:
            return etf, f"{sector} ETF ({etf})"
        # 默认用SPY
        return "SPY", "S&P 500 ETF"

    # A股：尝试匹配已知行业
    for keyword, idx in A_SHARE_SECTOR_MAP.items():
        if keyword in industry or keyword in info.get("longName", ""):
            return idx, f"{keyword}指数"

    # 默认：上证指数
    if ticker.endswith(".SS"):
        return "000001.SS", "上证指数"
    elif ticker.endswith(".SZ"):
        return "399001.SZ", "深证成指"
    elif ticker.endswith(".HK"):
        return "^HSI", "恒生指数"

    return "SPY", "S&P 500"


def run_sector_comparison(stock_df: pd.DataFrame, benchmark_df: pd.DataFrame,
                          benchmark_name: str = "行业基准") -> Dict:
    """
    计算个股与行业基准的相对强弱

    Args:
        stock_df: 个股OHLCV DataFrame
        benchmark_df: 基准OHLCV DataFrame
        benchmark_name: 基准名称

    Returns:
        相对强弱分析结果
    """
    if stock_df is None or benchmark_df is None or stock_df.empty or benchmark_df.empty:
        return {"error": "数据不足"}

    # 对齐日期
    common_idx = stock_df.index.intersection(benchmark_df.index)
    if len(common_idx) < 10:
        return {"error": "重叠数据不足"}

    stock = stock_df.loc[common_idx, "Close"]
    bench = benchmark_df.loc[common_idx, "Close"]

    # 各窗口收益率对比
    windows = {"1W": 5, "1M": 20, "3M": 60, "6M": 120, "1Y": 250}
    performance = {}
    for label, days in windows.items():
        if len(stock) >= days:
            s_ret = (stock.iloc[-1] / stock.iloc[-days] - 1) * 100
            b_ret = (bench.iloc[-1] / bench.iloc[-days] - 1) * 100
            excess = s_ret - b_ret
            performance[label] = {
                "stock_return": round(s_ret, 2),
                "benchmark_return": round(b_ret, 2),
                "excess_return": round(excess, 2),
                "outperform": excess > 0,
            }

    # 滚动相对强弱 RS线 (20日)
    rs_ratio = (stock / bench).dropna()
    rs_ratio_norm = rs_ratio / rs_ratio.iloc[0] * 100  # 归一化到100起点

    # RS趋势判断
    if len(rs_ratio) >= 20:
        rs_current = rs_ratio.iloc[-1]
        rs_ma20 = rs_ratio.rolling(20).mean().iloc[-1]
        rs_trend = "走强" if rs_current > rs_ma20 else "走弱"
    else:
        rs_trend = "—"

    # 最近表现汇总
    recent = performance.get("1M", {})
    if recent:
        if recent.get("excess_return", 0) > 3:
            strength_label = "显著跑赢"
        elif recent.get("excess_return", 0) > 0:
            strength_label = "小幅跑赢"
        elif recent.get("excess_return", 0) > -3:
            strength_label = "小幅跑输"
        else:
            strength_label = "显著跑输"
    else:
        strength_label = "—"

    return {
        "benchmark_name": benchmark_name,
        "performance": performance,
        "rs_trend": rs_trend,
        "strength_label": strength_label,
        "rs_ratio": rs_ratio_norm.to_dict() if len(rs_ratio_norm) > 0 else {},
    }
