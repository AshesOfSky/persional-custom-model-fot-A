"""
multi_timeframe.py — 多周期趋势仪表盘
分析日线/周线/月线的趋势方向、EMA排列、MACD方向、RSI区间
给出多周期共振评分
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


def analyze_timeframe_trend(df: pd.DataFrame) -> Dict:
    """
    分析单一时间周期的趋势方向

    Returns:
        {
            "trend": "多头" | "空头" | "震荡",
            "strength": 0-100,
            "ema_alignment": str,
            "macd_direction": str,
            "rsi_zone": str,
            "details": str,
        }
    """
    if df is None or df.empty or len(df) < 20:
        return {"trend": "数据不足", "strength": 0, "ema_alignment": "—",
                "macd_direction": "—", "rsi_zone": "—", "details": "数据不足"}

    close = df["Close"]
    last_close = close.iloc[-1]
    score = 0  # -100 ~ +100

    # EMA 排列检查
    ema_short = close.ewm(span=8, adjust=False).mean()
    ema_mid = close.ewm(span=21, adjust=False).mean()
    ema_long = close.ewm(span=55, adjust=False).mean()

    es, em, el = ema_short.iloc[-1], ema_mid.iloc[-1], ema_long.iloc[-1]
    if es > em > el:
        ema_alignment = "完全多头排列"
        score += 30
    elif es < em < el:
        ema_alignment = "完全空头排列"
        score -= 30
    elif last_close > em:
        ema_alignment = "偏多"
        score += 10
    elif last_close < em:
        ema_alignment = "偏空"
        score -= 10
    else:
        ema_alignment = "交织"

    # 价格 vs EMA位置
    if last_close > el:
        score += 10
    elif last_close < el:
        score -= 10

    # MACD方向
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal

    macd_val = macd.iloc[-1]
    hist_val = hist.iloc[-1]
    prev_hist = hist.iloc[-2] if len(hist) > 1 else 0

    if macd_val > 0 and hist_val > 0:
        macd_direction = "多头强势"
        score += 20
    elif macd_val > 0 and hist_val < 0:
        macd_direction = "多头减弱"
        score += 5
    elif macd_val < 0 and hist_val < 0:
        macd_direction = "空头强势"
        score -= 20
    elif macd_val < 0 and hist_val > 0:
        macd_direction = "空头减弱"
        score -= 5
    else:
        macd_direction = "零轴附近"

    # MACD柱增减
    if hist_val > prev_hist:
        score += 5
    elif hist_val < prev_hist:
        score -= 5

    # RSI区间
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = (100 - (100 / (1 + rs))).iloc[-1]

    if np.isnan(rsi):
        rsi_zone = "—"
    elif rsi > 70:
        rsi_zone = f"超买({rsi:.0f})"
        score += 10  # 强势
    elif rsi > 55:
        rsi_zone = f"偏强({rsi:.0f})"
        score += 15
    elif rsi > 45:
        rsi_zone = f"中性({rsi:.0f})"
    elif rsi > 30:
        rsi_zone = f"偏弱({rsi:.0f})"
        score -= 15
    else:
        rsi_zone = f"超卖({rsi:.0f})"
        score -= 10  # 可能反弹

    # 综合
    score = max(-100, min(100, score))
    if score > 25:
        trend = "多头"
    elif score < -25:
        trend = "空头"
    else:
        trend = "震荡"

    strength = abs(score)

    return {
        "trend": trend,
        "strength": strength,
        "score": score,
        "ema_alignment": ema_alignment,
        "macd_direction": macd_direction,
        "rsi_zone": rsi_zone,
        "details": f"{ema_alignment} | MACD{macd_direction} | RSI{rsi_zone}",
    }


def build_multi_timeframe_summary(analyses: Dict[str, Dict]) -> Dict:
    """
    综合多个周期的趋势分析

    Args:
        analyses: {"日线": {...}, "周线": {...}, "月线": {...}}

    Returns:
        {
            "timeframes": analyses,
            "alignment": "强共振" | "部分共振" | "分歧",
            "alignment_score": 0-100,
            "dominant_trend": "多头" | "空头" | "震荡",
            "summary": str,
        }
    """
    trends = []
    scores = []
    for tf_name, tf_data in analyses.items():
        if tf_data and tf_data.get("trend") != "数据不足":
            trends.append(tf_data["trend"])
            scores.append(tf_data.get("score", 0))

    if not trends:
        return {
            "timeframes": analyses,
            "alignment": "数据不足",
            "alignment_score": 0,
            "dominant_trend": "—",
            "summary": "数据不足",
        }

    # 计算共振
    bull_count = sum(1 for t in trends if t == "多头")
    bear_count = sum(1 for t in trends if t == "空头")
    total = len(trends)

    avg_score = sum(scores) / len(scores)

    if bull_count == total:
        alignment = "强多头共振"
        alignment_score = 90 + min(10, abs(avg_score) // 10)
        dominant_trend = "多头"
    elif bear_count == total:
        alignment = "强空头共振"
        alignment_score = 90 + min(10, abs(avg_score) // 10)
        dominant_trend = "空头"
    elif bull_count >= total - 1 and bear_count == 0:
        alignment = "偏多共振"
        alignment_score = 70
        dominant_trend = "多头"
    elif bear_count >= total - 1 and bull_count == 0:
        alignment = "偏空共振"
        alignment_score = 70
        dominant_trend = "空头"
    elif bull_count > bear_count:
        alignment = "部分多头"
        alignment_score = 50
        dominant_trend = "多头"
    elif bear_count > bull_count:
        alignment = "部分空头"
        alignment_score = 50
        dominant_trend = "空头"
    else:
        alignment = "多空分歧"
        alignment_score = 30
        dominant_trend = "震荡"

    # 简洁总结
    tf_strs = [f"{k}:{v.get('trend', '?')}" for k, v in analyses.items() if v]
    summary = f"{' / '.join(tf_strs)} → {alignment}"

    return {
        "timeframes": analyses,
        "alignment": alignment,
        "alignment_score": min(100, alignment_score),
        "dominant_trend": dominant_trend,
        "avg_score": round(avg_score, 1),
        "summary": summary,
    }
