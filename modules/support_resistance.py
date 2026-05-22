"""
support_resistance.py — 多周期支撑压力位精确计算引擎
支持 5分钟/30分钟/60分钟/日线/周线 各周期独立计算方案
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


# ─── 支撑压力位计算方法 ─────────────────────────────────────────────────────

def calc_pivot_points(high: float, low: float, close: float) -> Dict:
    """
    标准 Pivot Points (适用于5分钟级别)
    PP = (H + L + C) / 3
    R1 = 2*PP - L,  S1 = 2*PP - H
    R2 = PP + (H-L), S2 = PP - (H-L)
    R3 = H + 2*(PP-L), S3 = L - 2*(H-PP)
    """
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    s1 = 2 * pp - high
    r2 = pp + (high - low)
    s2 = pp - (high - low)
    r3 = high + 2 * (pp - low)
    s3 = low - 2 * (high - pp)
    return {
        "PP": round(pp, 4),
        "R1": round(r1, 4), "R2": round(r2, 4), "R3": round(r3, 4),
        "S1": round(s1, 4), "S2": round(s2, 4), "S3": round(s3, 4),
    }


def calc_vwap_bands(df: pd.DataFrame) -> Dict:
    """VWAP ± 1σ/2σ 支撑压力带"""
    if df.empty or "Volume" not in df.columns:
        return {}
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_vol = df["Volume"].cumsum()
    cum_tp_vol = (tp * df["Volume"]).cumsum()
    vwap = cum_tp_vol / cum_vol
    vwap_val = vwap.iloc[-1]

    # 标准差
    cum_var = ((tp - vwap) ** 2 * df["Volume"]).cumsum() / cum_vol
    std = cum_var.iloc[-1] ** 0.5

    return {
        "vwap": round(vwap_val, 4),
        "vwap_upper_1": round(vwap_val + std, 4),
        "vwap_lower_1": round(vwap_val - std, 4),
        "vwap_upper_2": round(vwap_val + 2 * std, 4),
        "vwap_lower_2": round(vwap_val - 2 * std, 4),
    }


def count_touches(df: pd.DataFrame, price_level: float, tolerance_pct: float = 0.005) -> int:
    """
    统计历史数据中某价位被触碰的次数
    触碰定义：最低价或最高价在 price_level ± tolerance 范围内
    """
    if df.empty or price_level <= 0:
        return 0
    tol = price_level * tolerance_pct
    low_touch = ((df["Low"] >= price_level - tol) & (df["Low"] <= price_level + tol))
    high_touch = ((df["High"] >= price_level - tol) & (df["High"] <= price_level + tol))
    return int((low_touch | high_touch).sum())


def _build_level(price: float, method: str, current_price: float,
                 df: pd.DataFrame = None, role: str = "support") -> Dict:
    """构建单个支撑/压力位描述"""
    dist_pct = (price - current_price) / current_price * 100 if current_price else 0
    touches = count_touches(df, price) if df is not None else 0
    # 可靠度：基于触碰次数
    if touches >= 5:
        strength = "强"
    elif touches >= 2:
        strength = "中"
    else:
        strength = "弱"
    return {
        "price": round(price, 4),
        "method": method,
        "distance_pct": round(dist_pct, 2),
        "touches": touches,
        "strength": strength,
        "role": role,
    }


# ─── 各周期计算引擎 ─────────────────────────────────────────────────────────

def calc_5m_levels(df: pd.DataFrame, daily_high: float = None,
                   daily_low: float = None, daily_close: float = None) -> Dict:
    """
    5分钟级别支撑压力位
    方法: Pivot Points (前日H/L/C) + VWAP ± σ
    """
    if df is None or df.empty:
        return {"timeframe": "5分钟", "levels": [], "error": "数据不足"}

    close = df["Close"].iloc[-1]

    # Pivot Points
    if daily_high and daily_low and daily_close:
        h, l, c = daily_high, daily_low, daily_close
    else:
        h = df["High"].max()
        l = df["Low"].min()
        c = close
    pivots = calc_pivot_points(h, l, c)

    # VWAP bands
    vwap = calc_vwap_bands(df)

    supports = []
    resistances = []

    # Pivot 支撑
    for key in ["S1", "S2", "S3"]:
        if pivots[key] < close:
            supports.append(_build_level(pivots[key], f"Pivot {key}", close, df, "support"))
    for key in ["R1", "R2", "R3"]:
        if pivots[key] > close:
            resistances.append(_build_level(pivots[key], f"Pivot {key}", close, df, "resistance"))

    # VWAP 支撑压力
    if vwap:
        if vwap["vwap"] < close:
            supports.append(_build_level(vwap["vwap"], "VWAP", close, df, "support"))
        elif vwap["vwap"] > close:
            resistances.append(_build_level(vwap["vwap"], "VWAP", close, df, "resistance"))
        if vwap.get("vwap_lower_1") and vwap["vwap_lower_1"] < close:
            supports.append(_build_level(vwap["vwap_lower_1"], "VWAP-1σ", close, df, "support"))
        if vwap.get("vwap_upper_1") and vwap["vwap_upper_1"] > close:
            resistances.append(_build_level(vwap["vwap_upper_1"], "VWAP+1σ", close, df, "resistance"))

    # 按距离排序
    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    primary_s = supports[0] if supports else None
    primary_r = resistances[0] if resistances else None

    return {
        "timeframe": "5分钟",
        "method": "Pivot Points + VWAP",
        "current_price": round(close, 4),
        "primary_support": primary_s,
        "secondary_support": supports[1] if len(supports) > 1 else None,
        "primary_resistance": primary_r,
        "secondary_resistance": resistances[1] if len(resistances) > 1 else None,
        "all_supports": supports[:4],
        "all_resistances": resistances[:4],
        "pivot_points": pivots,
    }


def calc_30m_levels(df: pd.DataFrame) -> Dict:
    """
    30分钟级别支撑压力位
    方法: 布林带上下轨 + EMA9/21 + 近5日高低点
    """
    if df is None or df.empty or len(df) < 20:
        return {"timeframe": "30分钟", "levels": [], "error": "数据不足"}

    close = df["Close"].iloc[-1]

    # 布林带
    ma20 = df["Close"].rolling(20, min_periods=1).mean()
    std20 = df["Close"].rolling(20, min_periods=1).std()
    bb_upper = (ma20 + 2 * std20).iloc[-1]
    bb_lower = (ma20 - 2 * std20).iloc[-1]
    bb_middle = ma20.iloc[-1]

    # EMA
    ema9 = df["Close"].ewm(span=9, adjust=False).mean().iloc[-1]
    ema21 = df["Close"].ewm(span=21, adjust=False).mean().iloc[-1]

    # 近期高低点
    recent = df.tail(min(len(df), 240))  # ~5日 30分钟
    recent_high = recent["High"].max()
    recent_low = recent["Low"].min()

    supports = []
    resistances = []

    # 布林带
    if bb_lower < close:
        supports.append(_build_level(bb_lower, "布林下轨", close, df, "support"))
    if bb_upper > close:
        resistances.append(_build_level(bb_upper, "布林上轨", close, df, "resistance"))
    if bb_middle < close:
        supports.append(_build_level(bb_middle, "布林中轨", close, df, "support"))
    elif bb_middle > close:
        resistances.append(_build_level(bb_middle, "布林中轨", close, df, "resistance"))

    # EMA
    for ema_val, name in [(ema9, "EMA9"), (ema21, "EMA21")]:
        if ema_val < close:
            supports.append(_build_level(ema_val, name, close, df, "support"))
        elif ema_val > close:
            resistances.append(_build_level(ema_val, name, close, df, "resistance"))

    # 近期高低
    if recent_low < close * 0.99:
        supports.append(_build_level(recent_low, "近5日低点", close, df, "support"))
    if recent_high > close * 1.01:
        resistances.append(_build_level(recent_high, "近5日高点", close, df, "resistance"))

    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "timeframe": "30分钟",
        "method": "布林带 + EMA9/21",
        "current_price": round(close, 4),
        "primary_support": supports[0] if supports else None,
        "secondary_support": supports[1] if len(supports) > 1 else None,
        "primary_resistance": resistances[0] if resistances else None,
        "secondary_resistance": resistances[1] if len(resistances) > 1 else None,
        "all_supports": supports[:4],
        "all_resistances": resistances[:4],
    }


def calc_60m_levels(df: pd.DataFrame) -> Dict:
    """
    60分钟级别支撑压力位
    方法: 斐波那契回撤 + EMA隧道（内隧道边界）
    """
    if df is None or df.empty or len(df) < 30:
        return {"timeframe": "60分钟", "levels": [], "error": "数据不足"}

    close = df["Close"].iloc[-1]

    # 近20日区间斐波那契
    recent = df.tail(min(len(df), 320))  # ~20日 60分钟
    high = recent["High"].max()
    low = recent["Low"].min()
    diff = high - low

    fib_levels = {
        "Fib 23.6%": high - diff * 0.236,
        "Fib 38.2%": high - diff * 0.382,
        "Fib 50.0%": high - diff * 0.5,
        "Fib 61.8%": high - diff * 0.618,
        "Fib 78.6%": high - diff * 0.786,
    }

    # EMA隧道
    ema8 = df["Close"].ewm(span=8, adjust=False).mean().iloc[-1]
    ema13 = df["Close"].ewm(span=13, adjust=False).mean().iloc[-1]
    ema21 = df["Close"].ewm(span=21, adjust=False).mean().iloc[-1]
    ema55 = df["Close"].ewm(span=55, adjust=False).mean().iloc[-1]

    inner_top = max(ema8, ema13, ema21)
    inner_bottom = min(ema8, ema13, ema21)

    supports = []
    resistances = []

    # 斐波那契
    for name, price in fib_levels.items():
        if price < close:
            supports.append(_build_level(price, name, close, df, "support"))
        elif price > close:
            resistances.append(_build_level(price, name, close, df, "resistance"))

    # EMA隧道
    if inner_bottom < close:
        supports.append(_build_level(inner_bottom, "内隧道下轨", close, df, "support"))
    if inner_top > close:
        resistances.append(_build_level(inner_top, "内隧道上轨", close, df, "resistance"))
    if ema55 < close:
        supports.append(_build_level(ema55, "EMA55", close, df, "support"))
    elif ema55 > close:
        resistances.append(_build_level(ema55, "EMA55", close, df, "resistance"))

    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "timeframe": "60分钟",
        "method": "斐波那契回撤 + EMA隧道",
        "current_price": round(close, 4),
        "primary_support": supports[0] if supports else None,
        "secondary_support": supports[1] if len(supports) > 1 else None,
        "primary_resistance": resistances[0] if resistances else None,
        "secondary_resistance": resistances[1] if len(resistances) > 1 else None,
        "all_supports": supports[:4],
        "all_resistances": resistances[:4],
    }


def calc_daily_levels(df: pd.DataFrame) -> Dict:
    """
    日线级别支撑压力位（增强版）
    方法: Fib + EMA隧道 + 布林带 + VWAP + Pivot + 前高低
    """
    if df is None or df.empty or len(df) < 30:
        return {"timeframe": "日线", "levels": [], "error": "数据不足"}

    close = df["Close"].iloc[-1]

    supports = []
    resistances = []

    # 1. 斐波那契 (权重3.0)
    high = df["High"].max()
    low = df["Low"].min()
    diff = high - low
    fib_map = {
        "Fib 23.6%": high - diff * 0.236,
        "Fib 38.2%": high - diff * 0.382,
        "Fib 50.0%": high - diff * 0.5,
        "Fib 61.8%": high - diff * 0.618,
        "Fib 78.6%": high - diff * 0.786,
    }
    for name, price in fib_map.items():
        if price < close:
            supports.append(_build_level(price, name, close, df, "support"))
        elif price > close:
            resistances.append(_build_level(price, name, close, df, "resistance"))

    # 2. EMA隧道
    for span, label in [(8, "EMA8"), (13, "EMA13"), (21, "EMA21"),
                         (55, "EMA55"), (144, "EMA144"), (288, "EMA288")]:
        if len(df) >= span:
            ema_val = df["Close"].ewm(span=span, adjust=False).mean().iloc[-1]
            if ema_val < close:
                supports.append(_build_level(ema_val, label, close, df, "support"))
            elif ema_val > close:
                resistances.append(_build_level(ema_val, label, close, df, "resistance"))

    # 3. 布林带
    ma20 = df["Close"].rolling(20, min_periods=1).mean().iloc[-1]
    std20 = df["Close"].rolling(20, min_periods=1).std().iloc[-1]
    bb_u = ma20 + 2 * std20
    bb_l = ma20 - 2 * std20
    if bb_l < close:
        supports.append(_build_level(bb_l, "布林下轨", close, df, "support"))
    if bb_u > close:
        resistances.append(_build_level(bb_u, "布林上轨", close, df, "resistance"))

    # 4. VWAP
    if "Volume" in df.columns:
        vwap_info = calc_vwap_bands(df)
        if vwap_info and vwap_info.get("vwap"):
            v = vwap_info["vwap"]
            if v < close:
                supports.append(_build_level(v, "VWAP", close, df, "support"))
            elif v > close:
                resistances.append(_build_level(v, "VWAP", close, df, "resistance"))

    # 5. Pivot Points
    prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]
    pivots = calc_pivot_points(prev["High"], prev["Low"], prev["Close"])
    if pivots["S1"] < close:
        supports.append(_build_level(pivots["S1"], "Pivot S1", close, df, "support"))
    if pivots["R1"] > close:
        resistances.append(_build_level(pivots["R1"], "Pivot R1", close, df, "resistance"))

    # 6. 近期高低点
    recent_lows = df["Low"].tail(20).nsmallest(3)
    recent_highs = df["High"].tail(20).nlargest(3)
    for lv in recent_lows:
        if lv < close * 0.99:
            supports.append(_build_level(lv, "近期低点", close, df, "support"))
    for hv in recent_highs:
        if hv > close * 1.01:
            resistances.append(_build_level(hv, "近期高点", close, df, "resistance"))

    # 7. 斐波那契扩展位（向上目标）
    fib_ext_map = {
        "Fib扩展 127.2%": high + diff * 0.272,
        "Fib扩展 161.8%": high + diff * 0.618,
        "Fib扩展 200.0%": high + diff * 1.0,
        "Fib扩展 261.8%": high + diff * 1.618,
    }
    for name, price in fib_ext_map.items():
        if price > close:
            resistances.append(_build_level(price, name, close, df, "resistance"))

    # 8. 前高前低（swing pivots）
    if len(df) >= 21:
        window = min(10, len(df) // 4)
        high_arr = df["High"].values
        low_arr = df["Low"].values
        for i in range(window, len(df) - window):
            if high_arr[i] == max(high_arr[i - window:i + window + 1]):
                if high_arr[i] > close * 1.005:
                    resistances.append(_build_level(
                        float(high_arr[i]), "前高", close, df, "resistance"))
            if low_arr[i] == min(low_arr[i - window:i + window + 1]):
                if low_arr[i] < close * 0.995:
                    supports.append(_build_level(
                        float(low_arr[i]), "前低", close, df, "support"))
        # 去重：前高前低可能重复，保留距离最近的几个
        seen_prices = set()
        unique_supports = []
        for s in supports:
            key = round(s["price"], 2)
            if key not in seen_prices:
                seen_prices.add(key)
                unique_supports.append(s)
        supports = unique_supports
        seen_prices = set()
        unique_resistances = []
        for r in resistances:
            key = round(r["price"], 2)
            if key not in seen_prices:
                seen_prices.add(key)
                unique_resistances.append(r)
        resistances = unique_resistances

    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "timeframe": "日线",
        "method": "Fib + EMA + BB + VWAP + Pivot + 前高前低 + Fib扩展",
        "current_price": round(close, 4),
        "primary_support": supports[0] if supports else None,
        "secondary_support": supports[1] if len(supports) > 1 else None,
        "primary_resistance": resistances[0] if resistances else None,
        "secondary_resistance": resistances[1] if len(resistances) > 1 else None,
        "all_supports": supports[:5],
        "all_resistances": resistances[:5],
    }


def calc_weekly_levels(df: pd.DataFrame) -> Dict:
    """
    周线级别支撑压力位
    方法: 斐波那契扩展 + EMA55/144
    """
    if df is None or df.empty or len(df) < 20:
        return {"timeframe": "周线", "levels": [], "error": "数据不足"}

    close = df["Close"].iloc[-1]

    # 近52周（或全部数据）
    high = df["High"].max()
    low = df["Low"].min()
    diff = high - low

    fib_map = {
        "周Fib 23.6%": high - diff * 0.236,
        "周Fib 38.2%": high - diff * 0.382,
        "周Fib 50.0%": high - diff * 0.5,
        "周Fib 61.8%": high - diff * 0.618,
    }

    # 斐波那契扩展
    fib_ext = {
        "Fib扩展 127.2%": high + diff * 0.272,
        "Fib扩展 161.8%": high + diff * 0.618,
    }

    supports = []
    resistances = []

    for name, price in fib_map.items():
        if price < close:
            supports.append(_build_level(price, name, close, df, "support"))
        elif price > close:
            resistances.append(_build_level(price, name, close, df, "resistance"))

    for name, price in fib_ext.items():
        if price > close:
            resistances.append(_build_level(price, name, close, df, "resistance"))

    # EMA55 / EMA144
    for span, label in [(55, "周EMA55"), (144, "周EMA144")]:
        if len(df) >= span:
            ema_val = df["Close"].ewm(span=span, adjust=False).mean().iloc[-1]
            if ema_val < close:
                supports.append(_build_level(ema_val, label, close, df, "support"))
            elif ema_val > close:
                resistances.append(_build_level(ema_val, label, close, df, "resistance"))

    supports.sort(key=lambda x: abs(x["distance_pct"]))
    resistances.sort(key=lambda x: abs(x["distance_pct"]))

    return {
        "timeframe": "周线",
        "method": "斐波那契扩展 + 周EMA",
        "current_price": round(close, 4),
        "primary_support": supports[0] if supports else None,
        "secondary_support": supports[1] if len(supports) > 1 else None,
        "primary_resistance": resistances[0] if resistances else None,
        "secondary_resistance": resistances[1] if len(resistances) > 1 else None,
        "all_supports": supports[:4],
        "all_resistances": resistances[:4],
    }


# ─── 统一入口 ────────────────────────────────────────────────────────────────

def calc_multi_timeframe_sr(
    df_daily: pd.DataFrame = None,
    df_weekly: pd.DataFrame = None,
    df_5m: pd.DataFrame = None,
    df_30m: pd.DataFrame = None,
    df_60m: pd.DataFrame = None,
) -> Dict:
    """
    多周期支撑压力位统一计算入口

    Returns:
        {
            "timeframes": [
                {"timeframe": "5分钟", "primary_support": {...}, "primary_resistance": {...}, ...},
                {"timeframe": "30分钟", ...},
                ...
            ],
            "summary": {
                "strongest_support": {...},
                "strongest_resistance": {...},
                "multi_tf_supports": [...],  # 多周期重合支撑
                "multi_tf_resistances": [...],
            }
        }
    """
    results = []

    # 日线 - 前日高低收用于5分钟 Pivot
    daily_h, daily_l, daily_c = None, None, None
    if df_daily is not None and not df_daily.empty and len(df_daily) > 1:
        prev_day = df_daily.iloc[-2]
        daily_h = prev_day["High"]
        daily_l = prev_day["Low"]
        daily_c = prev_day["Close"]

    if df_5m is not None:
        results.append(calc_5m_levels(df_5m, daily_h, daily_l, daily_c))
    if df_30m is not None:
        results.append(calc_30m_levels(df_30m))
    if df_60m is not None:
        results.append(calc_60m_levels(df_60m))
    if df_daily is not None:
        results.append(calc_daily_levels(df_daily))
    if df_weekly is not None:
        results.append(calc_weekly_levels(df_weekly))

    # 寻找多周期重合支撑压力
    all_supports = []
    all_resistances = []
    for r in results:
        for s in r.get("all_supports", []):
            all_supports.append({**s, "from_tf": r["timeframe"]})
        for res in r.get("all_resistances", []):
            all_resistances.append({**res, "from_tf": r["timeframe"]})

    # 聚类相近价位（±0.5%为同一价位）
    multi_tf_supports = _cluster_levels(all_supports)
    multi_tf_resistances = _cluster_levels(all_resistances)

    # 最强支撑/压力 = 多周期重合度最高的
    strongest_s = multi_tf_supports[0] if multi_tf_supports else None
    strongest_r = multi_tf_resistances[0] if multi_tf_resistances else None

    return {
        "timeframes": results,
        "summary": {
            "strongest_support": strongest_s,
            "strongest_resistance": strongest_r,
            "multi_tf_supports": multi_tf_supports[:5],
            "multi_tf_resistances": multi_tf_resistances[:5],
        },
    }


def _cluster_levels(levels: List[Dict], tolerance_pct: float = 0.005) -> List[Dict]:
    """
    将多个相近价位聚类，返回聚类后的价位（按重合周期数排序）
    """
    if not levels:
        return []

    # 按价格排序
    sorted_levels = sorted(levels, key=lambda x: x["price"])
    clusters = []
    used = set()

    for i, lv in enumerate(sorted_levels):
        if i in used:
            continue
        cluster = [lv]
        used.add(i)

        for j in range(i + 1, len(sorted_levels)):
            if j in used:
                continue
            if abs(sorted_levels[j]["price"] - lv["price"]) / lv["price"] < tolerance_pct:
                cluster.append(sorted_levels[j])
                used.add(j)

        # 聚类结果
        avg_price = sum(c["price"] for c in cluster) / len(cluster)
        methods = [f"{c['from_tf']}: {c['method']}" for c in cluster]
        timeframes = list(set(c["from_tf"] for c in cluster))

        clusters.append({
            "price": round(avg_price, 4),
            "tf_count": len(timeframes),
            "timeframes": timeframes,
            "methods": methods,
            "strength": "极强" if len(timeframes) >= 3 else ("强" if len(timeframes) >= 2 else "一般"),
        })

    # 按重合周期数降序
    clusters.sort(key=lambda x: -x["tf_count"])
    return clusters
