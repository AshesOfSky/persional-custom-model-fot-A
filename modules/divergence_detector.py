"""
divergence_detector.py — 背离检测引擎
检测 MACD柱/RSI/OBV 与价格之间的背离信号
常规背离(反转) + 隐藏背离(趋势延续)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


# ─── Swing 极值点检测 ────────────────────────────────────────────────────────

def _find_swing_highs(series: pd.Series, window: int = 5) -> List[Tuple[int, float]]:
    """寻找摆动高点：比前后 window 根K线都高的点"""
    highs = []
    arr = series.values
    n = len(arr)
    for i in range(window, n - window):
        if all(arr[i] >= arr[i - j] for j in range(1, window + 1)) and \
           all(arr[i] >= arr[i + j] for j in range(1, window + 1)):
            highs.append((i, arr[i]))
    return highs


def _find_swing_lows(series: pd.Series, window: int = 5) -> List[Tuple[int, float]]:
    """寻找摆动低点：比前后 window 根K线都低的点"""
    lows = []
    arr = series.values
    n = len(arr)
    for i in range(window, n - window):
        if all(arr[i] <= arr[i - j] for j in range(1, window + 1)) and \
           all(arr[i] <= arr[i + j] for j in range(1, window + 1)):
            lows.append((i, arr[i]))
    return lows


# ─── 背离检测核心 ────────────────────────────────────────────────────────────

def _detect_bearish_regular(price_highs, indicator_highs, min_span=5):
    """
    常规顶背离：价格创新高但指标未创新高 → 看跌反转
    """
    divergences = []
    if len(price_highs) < 2 or len(indicator_highs) < 2:
        return divergences

    for i in range(1, len(price_highs)):
        p_idx1, p_val1 = price_highs[i - 1]
        p_idx2, p_val2 = price_highs[i]

        if p_idx2 - p_idx1 < min_span:
            continue

        # 价格创新高
        if p_val2 <= p_val1:
            continue

        # 找对应的指标高点（最接近价格高点的）
        ih1 = _find_closest(indicator_highs, p_idx1)
        ih2 = _find_closest(indicator_highs, p_idx2)

        if ih1 is None or ih2 is None:
            continue

        # 指标未创新高
        if ih2[1] < ih1[1]:
            divergences.append({
                "start_idx": p_idx1,
                "end_idx": p_idx2,
                "price_val1": p_val1,
                "price_val2": p_val2,
                "ind_val1": ih1[1],
                "ind_val2": ih2[1],
                "ind_idx1": ih1[0],
                "ind_idx2": ih2[0],
            })
    return divergences


def _detect_bullish_regular(price_lows, indicator_lows, min_span=5):
    """
    常规底背离：价格创新低但指标未创新低 → 看涨反转
    """
    divergences = []
    if len(price_lows) < 2 or len(indicator_lows) < 2:
        return divergences

    for i in range(1, len(price_lows)):
        p_idx1, p_val1 = price_lows[i - 1]
        p_idx2, p_val2 = price_lows[i]

        if p_idx2 - p_idx1 < min_span:
            continue

        # 价格创新低
        if p_val2 >= p_val1:
            continue

        ih1 = _find_closest(indicator_lows, p_idx1)
        ih2 = _find_closest(indicator_lows, p_idx2)

        if ih1 is None or ih2 is None:
            continue

        # 指标未创新低
        if ih2[1] > ih1[1]:
            divergences.append({
                "start_idx": p_idx1,
                "end_idx": p_idx2,
                "price_val1": p_val1,
                "price_val2": p_val2,
                "ind_val1": ih1[1],
                "ind_val2": ih2[1],
                "ind_idx1": ih1[0],
                "ind_idx2": ih2[0],
            })
    return divergences


def _detect_hidden_bearish(price_highs, indicator_highs, min_span=5):
    """
    隐藏顶背离：价格未创新高但指标创新高 → 空头趋势延续
    """
    divergences = []
    if len(price_highs) < 2 or len(indicator_highs) < 2:
        return divergences

    for i in range(1, len(price_highs)):
        p_idx1, p_val1 = price_highs[i - 1]
        p_idx2, p_val2 = price_highs[i]

        if p_idx2 - p_idx1 < min_span:
            continue

        if p_val2 >= p_val1:  # 价格未创新高
            continue

        ih1 = _find_closest(indicator_highs, p_idx1)
        ih2 = _find_closest(indicator_highs, p_idx2)

        if ih1 is None or ih2 is None:
            continue

        if ih2[1] > ih1[1]:  # 指标创新高
            divergences.append({
                "start_idx": p_idx1,
                "end_idx": p_idx2,
                "price_val1": p_val1,
                "price_val2": p_val2,
                "ind_val1": ih1[1],
                "ind_val2": ih2[1],
                "ind_idx1": ih1[0],
                "ind_idx2": ih2[0],
            })
    return divergences


def _detect_hidden_bullish(price_lows, indicator_lows, min_span=5):
    """
    隐藏底背离：价格未创新低但指标创新低 → 多头趋势延续
    """
    divergences = []
    if len(price_lows) < 2 or len(indicator_lows) < 2:
        return divergences

    for i in range(1, len(price_lows)):
        p_idx1, p_val1 = price_lows[i - 1]
        p_idx2, p_val2 = price_lows[i]

        if p_idx2 - p_idx1 < min_span:
            continue

        if p_val2 <= p_val1:  # 价格未创新低
            continue

        ih1 = _find_closest(indicator_lows, p_idx1)
        ih2 = _find_closest(indicator_lows, p_idx2)

        if ih1 is None or ih2 is None:
            continue

        if ih2[1] < ih1[1]:  # 指标创新低
            divergences.append({
                "start_idx": p_idx1,
                "end_idx": p_idx2,
                "price_val1": p_val1,
                "price_val2": p_val2,
                "ind_val1": ih1[1],
                "ind_val2": ih2[1],
                "ind_idx1": ih1[0],
                "ind_idx2": ih2[0],
            })
    return divergences


def _find_closest(points: List[Tuple[int, float]], target_idx: int, max_dist: int = 8):
    """在极值点列表中找到最接近目标索引的点"""
    best = None
    best_dist = float("inf")
    for idx, val in points:
        dist = abs(idx - target_idx)
        if dist < best_dist and dist <= max_dist:
            best_dist = dist
            best = (idx, val)
    return best


# ─── 可靠度评估 ───────────────────────────────────────────────────────────────

def _calc_reliability(div_type: str, indicator: str, span: int, price_diff_pct: float):
    """
    根据背离类型、指标、跨度和价格变化幅度评估可靠度
    """
    score = 0

    # 指标权重：MACD > RSI > OBV
    ind_weight = {"MACD": 1.3, "RSI": 1.0, "OBV": 0.8}
    score += ind_weight.get(indicator, 1.0) * 20

    # 跨度越大越可靠
    if span >= 20:
        score += 30
    elif span >= 10:
        score += 20
    elif span >= 5:
        score += 10

    # 价格偏差越大越可靠
    if abs(price_diff_pct) >= 5:
        score += 25
    elif abs(price_diff_pct) >= 2:
        score += 15
    else:
        score += 5

    # 常规背离比隐藏背离可靠
    if "regular" in div_type:
        score += 15

    if score >= 65:
        return "强"
    elif score >= 40:
        return "中"
    return "弱"


# ─── 主函数 ──────────────────────────────────────────────────────────────────

def detect_divergences(df: pd.DataFrame, lookback: int = 60, swing_window: int = 5) -> Dict:
    """
    检测 MACD柱/RSI/OBV 与价格的背离

    参数:
        df: 包含 OHLCV 和技术指标列的DataFrame
        lookback: 向前回溯的K线数量
        swing_window: Swing点检测窗口大小

    返回:
        包含所有检出背离信号的字典
    """
    if df is None or df.empty or len(df) < lookback:
        lookback = len(df) - 10 if len(df) > 20 else 0
    if lookback < 15:
        return {
            "divergences": [], "active_divergences": [],
            "divergence_count": 0,
            "has_bearish_divergence": False, "has_bullish_divergence": False,
            "strongest_divergence": None,
        }

    n = len(df)
    start = max(0, n - lookback)
    sub = df.iloc[start:].copy().reset_index(drop=True)

    # 收集价格极值点
    price_highs = _find_swing_highs(sub["High"], swing_window)
    price_lows = _find_swing_lows(sub["Low"], swing_window)

    # 对每个指标检测背离
    indicators_to_scan = []

    # MACD 柱状图
    for col in ["MACD_hist", "MACD_Hist"]:
        if col in sub.columns:
            indicators_to_scan.append(("MACD", sub[col]))
            break

    # RSI
    if "RSI" in sub.columns:
        indicators_to_scan.append(("RSI", sub["RSI"]))

    # OBV
    if "OBV" in sub.columns:
        indicators_to_scan.append(("OBV", sub["OBV"]))

    all_divergences = []

    for ind_name, ind_series in indicators_to_scan:
        if ind_series.isna().all():
            continue

        ind_highs = _find_swing_highs(ind_series, swing_window)
        ind_lows = _find_swing_lows(ind_series, swing_window)

        # 常规顶背离
        for d in _detect_bearish_regular(price_highs, ind_highs):
            span = d["end_idx"] - d["start_idx"]
            price_diff = (d["price_val2"] - d["price_val1"]) / d["price_val1"] * 100 if d["price_val1"] else 0
            reliability = _calc_reliability("bearish_regular", ind_name, span, price_diff)

            start_date = sub.index[d["start_idx"]] if hasattr(sub.index[0], 'strftime') else str(d["start_idx"])
            end_date = sub.index[d["end_idx"]] if hasattr(sub.index[0], 'strftime') else str(d["end_idx"])

            bias_score = -40 if reliability == "弱" else (-55 if reliability == "中" else -70)

            all_divergences.append({
                "type": "顶背离",
                "type_en": "bearish_regular",
                "indicator": ind_name,
                "start_idx": d["start_idx"] + start,
                "end_idx": d["end_idx"] + start,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "price_high_1": round(d["price_val1"], 4),
                "price_high_2": round(d["price_val2"], 4),
                "indicator_val_1": round(d["ind_val1"], 4),
                "indicator_val_2": round(d["ind_val2"], 4),
                "span_bars": span,
                "reliability": reliability,
                "description": f"价格创新高({d['price_val1']:.2f}→{d['price_val2']:.2f})但{ind_name}未创新高，"
                              f"看跌反转概率较大（可靠度：{reliability}）",
                "bias_score": bias_score,
            })

        # 常规底背离
        for d in _detect_bullish_regular(price_lows, ind_lows):
            span = d["end_idx"] - d["start_idx"]
            price_diff = (d["price_val2"] - d["price_val1"]) / d["price_val1"] * 100 if d["price_val1"] else 0
            reliability = _calc_reliability("bullish_regular", ind_name, span, price_diff)

            start_date = sub.index[d["start_idx"]] if hasattr(sub.index[0], 'strftime') else str(d["start_idx"])
            end_date = sub.index[d["end_idx"]] if hasattr(sub.index[0], 'strftime') else str(d["end_idx"])

            bias_score = 40 if reliability == "弱" else (55 if reliability == "中" else 70)

            all_divergences.append({
                "type": "底背离",
                "type_en": "bullish_regular",
                "indicator": ind_name,
                "start_idx": d["start_idx"] + start,
                "end_idx": d["end_idx"] + start,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "price_low_1": round(d["price_val1"], 4),
                "price_low_2": round(d["price_val2"], 4),
                "indicator_val_1": round(d["ind_val1"], 4),
                "indicator_val_2": round(d["ind_val2"], 4),
                "span_bars": span,
                "reliability": reliability,
                "description": f"价格创新低({d['price_val1']:.2f}→{d['price_val2']:.2f})但{ind_name}未创新低，"
                              f"看涨反转概率较大（可靠度：{reliability}）",
                "bias_score": bias_score,
            })

        # 隐藏顶背离
        for d in _detect_hidden_bearish(price_highs, ind_highs):
            span = d["end_idx"] - d["start_idx"]
            price_diff = (d["price_val2"] - d["price_val1"]) / d["price_val1"] * 100 if d["price_val1"] else 0
            reliability = _calc_reliability("hidden_bearish", ind_name, span, price_diff)

            all_divergences.append({
                "type": "隐藏顶背离",
                "type_en": "hidden_bearish",
                "indicator": ind_name,
                "start_idx": d["start_idx"] + start,
                "end_idx": d["end_idx"] + start,
                "start_date": str(d["start_idx"]),
                "end_date": str(d["end_idx"]),
                "span_bars": span,
                "reliability": reliability,
                "description": f"价格未创新高但{ind_name}创新高，空头趋势可能延续（可靠度：{reliability}）",
                "bias_score": -30 if reliability == "弱" else (-40 if reliability == "中" else -50),
            })

        # 隐藏底背离
        for d in _detect_hidden_bullish(price_lows, ind_lows):
            span = d["end_idx"] - d["start_idx"]
            price_diff = (d["price_val2"] - d["price_val1"]) / d["price_val1"] * 100 if d["price_val1"] else 0
            reliability = _calc_reliability("hidden_bullish", ind_name, span, price_diff)

            all_divergences.append({
                "type": "隐藏底背离",
                "type_en": "hidden_bullish",
                "indicator": ind_name,
                "start_idx": d["start_idx"] + start,
                "end_idx": d["end_idx"] + start,
                "start_date": str(d["start_idx"]),
                "end_date": str(d["end_idx"]),
                "span_bars": span,
                "reliability": reliability,
                "description": f"价格未创新低但{ind_name}创新低，多头趋势可能延续（可靠度：{reliability}）",
                "bias_score": 30 if reliability == "弱" else (40 if reliability == "中" else 50),
            })

    # 按结束时间排序（最近的排前面）
    all_divergences.sort(key=lambda x: x["end_idx"], reverse=True)

    # 活跃背离：第二极值在最近10根K线内
    active_threshold = n - 10
    active = [d for d in all_divergences if d["end_idx"] >= active_threshold]

    has_bearish = any(d["type"] in ("顶背离", "隐藏顶背离") for d in active)
    has_bullish = any(d["type"] in ("底背离", "隐藏底背离") for d in active)

    strongest = None
    if active:
        s = max(active, key=lambda x: abs(x["bias_score"]))
        strongest = f"{s['indicator']} {s['type']}（{s['reliability']}）"

    return {
        "divergences": all_divergences,
        "active_divergences": active,
        "divergence_count": len(all_divergences),
        "active_count": len(active),
        "has_bearish_divergence": has_bearish,
        "has_bullish_divergence": has_bullish,
        "strongest_divergence": strongest,
    }
