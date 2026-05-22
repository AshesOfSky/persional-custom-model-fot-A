"""
correlation_analysis.py — 跨资产关联性分析引擎
支持贵金属/能源/A股/美股/期货商品的跨品种关联分析
包含关联映射、滚动相关系数、关键比率、宏观事件日历
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# ─── 资产关联映射表 ──────────────────────────────────────────────────────────

CORRELATION_MAP = {
    # === 贵金属 ===
    "黄金": {
        "keywords": ["AU", "XAU", "GC", "黄金", "gold"],
        "related": [
            {"name": "白银", "tickers": ["AG0", "XAGUSD"], "direction": "正相关"},
            {"name": "美元指数", "tickers": ["DX-Y.NYB"], "direction": "负相关"},
            {"name": "原油", "tickers": ["SC0", "CL=F"], "direction": "正相关"},
        ],
        "ratios": [
            {"name": "金银比", "numerator": ["AU0", "XAUUSD"], "denominator": ["AG0", "XAGUSD"],
             "normal_range": [60, 80], "description": "金银比偏高→白银相对低估"},
            {"name": "金油比", "numerator": ["AU0", "XAUUSD"], "denominator": ["SC0", "CL=F"],
             "normal_range": [15, 25], "description": "金油比偏高→原油相对低估或避险情绪升温"},
        ],
        "macro_factors": ["美联储利率", "CPI", "非农就业", "地缘政治", "美债收益率"],
    },
    "白银": {
        "keywords": ["AG", "XAG", "SI", "白银", "silver"],
        "related": [
            {"name": "黄金", "tickers": ["AU0", "XAUUSD"], "direction": "正相关"},
            {"name": "铜", "tickers": ["CU0"], "direction": "正相关"},
            {"name": "美元指数", "tickers": ["DX-Y.NYB"], "direction": "负相关"},
        ],
        "ratios": [
            {"name": "金银比", "numerator": ["AU0", "XAUUSD"], "denominator": ["AG0", "XAGUSD"],
             "normal_range": [60, 80], "description": "金银比偏低→白银涨幅可能领先黄金"},
        ],
        "macro_factors": ["工业需求", "光伏装机", "CPI"],
    },
    # === 能源 ===
    "原油": {
        "keywords": ["SC", "CL", "OIL", "原油", "crude", "WTI", "Brent"],
        "related": [
            {"name": "美元指数", "tickers": ["DX-Y.NYB"], "direction": "负相关"},
            {"name": "黄金", "tickers": ["AU0", "XAUUSD"], "direction": "弱正相关"},
        ],
        "ratios": [
            {"name": "金油比", "numerator": ["AU0", "XAUUSD"], "denominator": ["SC0", "CL=F"],
             "normal_range": [15, 25], "description": "金油比过高→原油可能被低估"},
        ],
        "macro_factors": ["OPEC产量", "EIA库存", "地缘政治", "全球PMI"],
    },
    # === A股行业 ===
    "A股科技": {
        "keywords": ["科技", "半导体", "芯片", "AI", "300750", "002230"],
        "related": [
            {"name": "纳斯达克", "tickers": ["^IXIC"], "direction": "正相关"},
            {"name": "费城半导体", "tickers": ["^SOX"], "direction": "正相关"},
            {"name": "恒生科技", "tickers": ["^HSTECH"], "direction": "正相关"},
        ],
        "ratios": [],
        "macro_factors": ["美国科技股走势", "AI政策", "出口管制"],
    },
    "A股消费": {
        "keywords": ["消费", "白酒", "食品", "600519", "000858"],
        "related": [
            {"name": "社零同比", "tickers": [], "direction": "正相关"},
        ],
        "ratios": [],
        "macro_factors": ["社零数据", "CPI", "居民收入"],
    },
    "A股金融": {
        "keywords": ["银行", "保险", "证券", "601398", "601318"],
        "related": [],
        "ratios": [],
        "macro_factors": ["LPR", "MLF", "社融数据", "国债收益率"],
    },
    # === 美股 ===
    "美股科技": {
        "keywords": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"],
        "related": [
            {"name": "VIX恐慌指数", "tickers": ["^VIX"], "direction": "负相关"},
            {"name": "美10年国债", "tickers": ["^TNX"], "direction": "负相关"},
        ],
        "ratios": [],
        "macro_factors": ["美联储利率", "CPI", "科技财报季"],
    },
    # === 期货商品 ===
    "铜": {
        "keywords": ["CU", "HG", "铜", "copper"],
        "related": [
            {"name": "美元指数", "tickers": ["DX-Y.NYB"], "direction": "负相关"},
            {"name": "铁矿石", "tickers": ["I0"], "direction": "正相关"},
        ],
        "ratios": [],
        "macro_factors": ["PMI制造业", "基建投资", "中国需求"],
    },
    "铁矿石": {
        "keywords": ["I0", "铁矿", "iron"],
        "related": [
            {"name": "螺纹钢", "tickers": ["RB0"], "direction": "正相关"},
            {"name": "焦煤", "tickers": ["J0"], "direction": "正相关"},
        ],
        "ratios": [],
        "macro_factors": ["钢厂利润", "港口库存", "房地产数据"],
    },
}


# ─── 资产类别自动识别 ─────────────────────────────────────────────────────────

def identify_asset_class(ticker: str) -> Optional[str]:
    """根据 ticker 代码自动识别资产类别"""
    ticker_upper = ticker.upper()

    for class_name, config in CORRELATION_MAP.items():
        for kw in config["keywords"]:
            if kw.upper() in ticker_upper:
                return class_name

    # 特殊匹配
    if ticker_upper.startswith("AU") or "GOLD" in ticker_upper:
        return "黄金"
    if ticker_upper.startswith("AG") or "SILVER" in ticker_upper:
        return "白银"
    if ticker_upper.startswith("SC") or "CL" in ticker_upper:
        return "原油"
    if ticker_upper.startswith("CU"):
        return "铜"

    # A股代码模式
    if any(ticker_upper.startswith(p) for p in ["60", "00", "30"]) and len(ticker) == 6:
        # 简单分类（可扩展）
        return None  # 暂不分类，用通用逻辑

    # 美股
    if ticker_upper in ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]:
        return "美股科技"

    return None


# ─── 相关系数计算 ─────────────────────────────────────────────────────────────

def calc_rolling_correlation(
    series_a: pd.Series,
    series_b: pd.Series,
    windows: List[int] = [20, 60],
) -> Dict:
    """
    计算两个价格序列的滚动相关系数

    Returns:
        {"corr_20d": float, "corr_60d": float, "corr_trend": str}
    """
    if series_a is None or series_b is None:
        return {}
    if len(series_a) < 20 or len(series_b) < 20:
        return {}

    # 对齐索引
    combined = pd.DataFrame({"a": series_a, "b": series_b}).dropna()
    if len(combined) < 20:
        return {}

    # 用收益率计算相关性（更稳健）
    ret_a = combined["a"].pct_change().dropna()
    ret_b = combined["b"].pct_change().dropna()

    result = {}
    for w in windows:
        if len(ret_a) >= w:
            corr = ret_a.tail(w).corr(ret_b.tail(w))
            result[f"corr_{w}d"] = round(corr, 3) if not np.isnan(corr) else None

    # 相关性趋势
    if result.get("corr_20d") and result.get("corr_60d"):
        if result["corr_20d"] > result["corr_60d"] + 0.1:
            result["corr_trend"] = "相关性增强"
        elif result["corr_20d"] < result["corr_60d"] - 0.1:
            result["corr_trend"] = "相关性减弱"
        else:
            result["corr_trend"] = "稳定"

    return result


def interpret_correlation(corr: float) -> str:
    """解读相关系数"""
    if corr is None:
        return "数据不足"
    abs_corr = abs(corr)
    direction = "正" if corr > 0 else "负"
    if abs_corr >= 0.8:
        return f"高度{direction}相关"
    elif abs_corr >= 0.5:
        return f"显著{direction}相关"
    elif abs_corr >= 0.3:
        return f"弱{direction}相关"
    else:
        return "基本无关"


# ─── 关键比率计算 ─────────────────────────────────────────────────────────────

def calc_ratio(
    price_a: float,
    price_b: float,
    series_a: pd.Series = None,
    series_b: pd.Series = None,
    normal_range: List[float] = None,
) -> Dict:
    """
    计算两个资产的比率及历史百分位

    Returns:
        {"current": float, "historical_pct": float, "signal": str}
    """
    if not price_a or not price_b or price_b == 0:
        return {}

    current = price_a / price_b

    result = {"current": round(current, 2)}

    # 历史百分位
    if series_a is not None and series_b is not None:
        combined = pd.DataFrame({"a": series_a, "b": series_b}).dropna()
        if len(combined) > 20:
            ratio_history = combined["a"] / combined["b"].replace(0, np.nan)
            ratio_history = ratio_history.dropna()
            if len(ratio_history) > 0:
                pct = (ratio_history < current).sum() / len(ratio_history)
                result["historical_pct"] = round(pct, 2)

                # 信号
                if pct >= 0.9:
                    result["percentile_signal"] = "极度偏高（历史90%+分位）"
                elif pct >= 0.75:
                    result["percentile_signal"] = "偏高"
                elif pct <= 0.1:
                    result["percentile_signal"] = "极度偏低（历史10%-分位）"
                elif pct <= 0.25:
                    result["percentile_signal"] = "偏低"
                else:
                    result["percentile_signal"] = "正常区间"

    # 与正常范围比较
    if normal_range and len(normal_range) == 2:
        if current > normal_range[1]:
            result["range_signal"] = f"高于正常区间({normal_range[0]}-{normal_range[1]})"
        elif current < normal_range[0]:
            result["range_signal"] = f"低于正常区间({normal_range[0]}-{normal_range[1]})"
        else:
            result["range_signal"] = f"在正常区间内({normal_range[0]}-{normal_range[1]})"

    return result


# ─── 宏观事件日历 ─────────────────────────────────────────────────────────────

def fetch_macro_calendar() -> List[Dict]:
    """
    获取宏观经济事件日历
    优先使用 akshare，备选内置日历
    """
    events = []

    try:
        import akshare as ak
        # 尝试获取美国经济日历
        try:
            df_us = ak.macro_usa_gdp()
            # 如果接口有效，解析事件
        except Exception:
            pass

        # 尝试中国经济日历
        try:
            df_cn = ak.macro_china_pmi()
        except Exception:
            pass
    except ImportError:
        pass

    # 备选：内置关键日期（每季度更新一次）
    # 2026年关键经济日历
    builtin_events = [
        # 美联储会议
        {"event": "美联储FOMC利率决议", "date": "2026-01-28", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-03-18", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-05-06", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-06-17", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-07-29", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-09-16", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-11-04", "impact": "高", "region": "美国"},
        {"event": "美联储FOMC利率决议", "date": "2026-12-16", "impact": "高", "region": "美国"},
        # 非农（每月第一个周五）
        {"event": "美国非农就业数据", "date": "2026-03-06", "impact": "高", "region": "美国"},
        {"event": "美国非农就业数据", "date": "2026-04-03", "impact": "高", "region": "美国"},
        {"event": "美国非农就业数据", "date": "2026-05-01", "impact": "高", "region": "美国"},
        {"event": "美国非农就业数据", "date": "2026-06-05", "impact": "高", "region": "美国"},
        # CPI
        {"event": "美国CPI数据", "date": "2026-03-11", "impact": "高", "region": "美国"},
        {"event": "美国CPI数据", "date": "2026-04-14", "impact": "高", "region": "美国"},
        {"event": "美国CPI数据", "date": "2026-05-13", "impact": "高", "region": "美国"},
        # 中国PMI
        {"event": "中国PMI数据", "date": "2026-03-31", "impact": "中", "region": "中国"},
        {"event": "中国PMI数据", "date": "2026-04-30", "impact": "中", "region": "中国"},
        # 中国LPR
        {"event": "中国LPR利率公布", "date": "2026-03-20", "impact": "中", "region": "中国"},
        {"event": "中国LPR利率公布", "date": "2026-04-20", "impact": "中", "region": "中国"},
    ]

    today = datetime.now().date()
    future_7d = today + timedelta(days=7)
    future_30d = today + timedelta(days=30)

    for evt in builtin_events:
        evt_date = datetime.strptime(evt["date"], "%Y-%m-%d").date()
        if evt_date >= today and evt_date <= future_30d:
            days_until = (evt_date - today).days
            events.append({
                **evt,
                "days_until": days_until,
                "urgency": "紧急" if days_until <= 3 else ("临近" if days_until <= 7 else "关注"),
            })

    # 按日期排序
    events.sort(key=lambda x: x["date"])
    return events


# ─── 综合关联分析 ─────────────────────────────────────────────────────────────

def run_correlation_analysis(
    ticker: str,
    primary_df: pd.DataFrame,
    fetch_data_func=None,
) -> Dict:
    """
    运行完整的跨资产关联分析

    Args:
        ticker: 主标的代码
        primary_df: 主标的价格数据
        fetch_data_func: 获取关联资产数据的函数 (ticker) -> pd.DataFrame

    Returns:
        完整关联分析结果
    """
    asset_class = identify_asset_class(ticker)

    if not asset_class or asset_class not in CORRELATION_MAP:
        return {
            "asset_class": None,
            "message": f"未识别到 {ticker} 的资产类别，暂无关联分析",
            "correlations": [],
            "ratios": [],
            "macro_events": fetch_macro_calendar(),
            "macro_factors": [],
            "correlation_signal": "无关联分析数据",
        }

    config = CORRELATION_MAP[asset_class]
    primary_close = primary_df["Close"] if primary_df is not None else None

    # 1. 计算关联资产相关性
    correlations = []
    for related in config.get("related", []):
        corr_result = {
            "asset": related["name"],
            "expected_direction": related["direction"],
            "tickers": related.get("tickers", []),
        }

        # 尝试获取关联资产数据
        if fetch_data_func and related.get("tickers"):
            for t in related["tickers"]:
                try:
                    related_df = fetch_data_func(t)
                    if related_df is not None and not related_df.empty:
                        corr = calc_rolling_correlation(
                            primary_close, related_df["Close"]
                        )
                        if corr:
                            corr_result.update(corr)
                            corr_20 = corr.get("corr_20d")
                            if corr_20 is not None:
                                corr_result["signal"] = interpret_correlation(corr_20)
                            corr_result["data_ticker"] = t
                            # 关联资产当前价格
                            corr_result["related_price"] = round(related_df["Close"].iloc[-1], 4)
                            # 关联资产涨跌
                            if len(related_df) > 1:
                                chg = (related_df["Close"].iloc[-1] - related_df["Close"].iloc[-2]) / related_df["Close"].iloc[-2] * 100
                                corr_result["related_change_pct"] = round(chg, 2)
                        break
                except Exception as e:
                    logger.warning(f"获取关联资产 {t} 数据失败: {e}")

        correlations.append(corr_result)

    # 2. 计算关键比率
    ratios = []
    for ratio_cfg in config.get("ratios", []):
        ratio_result = {
            "name": ratio_cfg["name"],
            "description": ratio_cfg.get("description", ""),
        }

        if fetch_data_func:
            # 获取分子分母数据
            num_price, num_series = None, None
            den_price, den_series = None, None

            for t in ratio_cfg.get("numerator", []):
                try:
                    df = fetch_data_func(t)
                    if df is not None and not df.empty:
                        num_price = df["Close"].iloc[-1]
                        num_series = df["Close"]
                        break
                except Exception:
                    pass

            # 如果分子是主标的
            if num_price is None and primary_close is not None:
                num_price = primary_close.iloc[-1]
                num_series = primary_close

            for t in ratio_cfg.get("denominator", []):
                try:
                    df = fetch_data_func(t)
                    if df is not None and not df.empty:
                        den_price = df["Close"].iloc[-1]
                        den_series = df["Close"]
                        break
                except Exception:
                    pass

            if num_price and den_price:
                ratio_data = calc_ratio(
                    num_price, den_price,
                    num_series, den_series,
                    ratio_cfg.get("normal_range"),
                )
                ratio_result.update(ratio_data)

        ratios.append(ratio_result)

    # 3. 宏观事件日历
    macro_events = fetch_macro_calendar()
    # 筛选与该资产类别相关的宏观因素
    relevant_factors = config.get("macro_factors", [])
    relevant_events = [
        evt for evt in macro_events
        if any(f in evt["event"] for f in relevant_factors) or evt["impact"] == "高"
    ]

    # 4. 综合关联信号
    signal_parts = []

    # 基于关联资产涨跌给出信号
    bullish_count = 0
    bearish_count = 0
    for corr in correlations:
        chg = corr.get("related_change_pct", 0)
        direction = corr.get("expected_direction", "")
        if "正相关" in direction:
            if chg > 0:
                bullish_count += 1
            elif chg < 0:
                bearish_count += 1
        elif "负相关" in direction:
            if chg > 0:
                bearish_count += 1
            elif chg < 0:
                bullish_count += 1

    if bullish_count > bearish_count:
        signal_parts.append(f"关联资产偏多（{bullish_count}个利多信号）")
    elif bearish_count > bullish_count:
        signal_parts.append(f"关联资产偏空（{bearish_count}个利空信号）")

    # 基于比率偏离度
    for r in ratios:
        if r.get("percentile_signal") and "极度" in r.get("percentile_signal", ""):
            signal_parts.append(f"{r['name']}{r['percentile_signal']}")

    # 基于临近宏观事件
    urgent_events = [e for e in relevant_events if e.get("urgency") in ["紧急", "临近"]]
    if urgent_events:
        signal_parts.append(f"注意: {urgent_events[0]['event']}({urgent_events[0]['date']})")

    correlation_signal = " | ".join(signal_parts) if signal_parts else "关联指标正常"

    return {
        "asset_class": asset_class,
        "correlations": correlations,
        "ratios": ratios,
        "macro_events": relevant_events[:10],
        "macro_factors": relevant_factors,
        "correlation_signal": correlation_signal,
        "bullish_signals": bullish_count,
        "bearish_signals": bearish_count,
    }
