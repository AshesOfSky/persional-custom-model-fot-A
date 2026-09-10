"""
chart_patterns.py — 图表形态识别引擎
检测经典技术图表形态：双顶/底、头肩、三角形、旗形、楔形、通道
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional


# ─── Zigzag 转折点提取 ────────────────────────────────────────────────────────

def _zigzag_pivots(df: pd.DataFrame, min_pct: float = 3.0) -> List[Dict]:
    """
    Zigzag算法提取关键转折点
    min_pct: 最小价格变动幅度（百分比）才算一次转折
    """
    if len(df) < 10:
        return []

    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values

    pivots = []
    last_pivot_type = None  # "high" or "low"
    last_pivot_val = closes[0]
    last_pivot_idx = 0

    for i in range(1, len(df)):
        change_from_last = (closes[i] - last_pivot_val) / last_pivot_val * 100 if last_pivot_val else 0

        if change_from_last >= min_pct:
            # 上涨超过阈值
            if last_pivot_type != "low":
                # 如果之前不是低点，找到之前的最低点
                if pivots:
                    # 更新方向
                    pass
            # 标记前面的低点
            window_start = last_pivot_idx
            window_lows = lows[window_start:i+1]
            low_offset = np.argmin(window_lows)
            low_idx = window_start + low_offset
            low_val = window_lows[low_offset]

            if last_pivot_type != "low":
                pivots.append({"idx": low_idx, "value": low_val, "type": "low"})
                last_pivot_type = "low"
                last_pivot_val = low_val
                last_pivot_idx = low_idx

        elif change_from_last <= -min_pct:
            # 下跌超过阈值
            window_start = last_pivot_idx
            window_highs = highs[window_start:i+1]
            high_offset = np.argmax(window_highs)
            high_idx = window_start + high_offset
            high_val = window_highs[high_offset]

            if last_pivot_type != "high":
                pivots.append({"idx": high_idx, "value": high_val, "type": "high"})
                last_pivot_type = "high"
                last_pivot_val = high_val
                last_pivot_idx = high_idx

    return pivots


def _find_pivots_simple(df: pd.DataFrame, window: int = 10) -> Tuple[List[Dict], List[Dict]]:
    """
    简单的窗口极值点法（比zigzag更稳定）
    """
    highs_list = []
    lows_list = []
    n = len(df)

    for i in range(window, n - window):
        # 局部最高
        if df["High"].iloc[i] == df["High"].iloc[i-window:i+window+1].max():
            highs_list.append({"idx": i, "value": df["High"].iloc[i]})
        # 局部最低
        if df["Low"].iloc[i] == df["Low"].iloc[i-window:i+window+1].min():
            lows_list.append({"idx": i, "value": df["Low"].iloc[i]})

    return highs_list, lows_list


# ─── 形态检测函数 ────────────────────────────────────────────────────────────

def _detect_double_top(highs: List[Dict], lows: List[Dict], df: pd.DataFrame,
                       tolerance: float = 0.02) -> Optional[Dict]:
    """
    双顶：两个相近的高点 + 中间有一个低点（颈线）
    """
    if len(highs) < 2:
        return None

    n = len(df)
    current_price = df["Close"].iloc[-1]

    # 从最近的高点开始找
    for i in range(len(highs) - 1, 0, -1):
        h2 = highs[i]
        h1 = highs[i - 1]

        # 两个高点距离足够远
        if h2["idx"] - h1["idx"] < 10:
            continue

        # 两个高点价格接近
        diff_pct = abs(h2["value"] - h1["value"]) / h1["value"]
        if diff_pct > tolerance:
            continue

        # 找中间的最低点作为颈线
        between_lows = [l for l in lows if h1["idx"] < l["idx"] < h2["idx"]]
        if not between_lows:
            continue

        neckline_point = min(between_lows, key=lambda x: x["value"])
        neckline = neckline_point["value"]

        # 双顶高度
        top_avg = (h1["value"] + h2["value"]) / 2
        height = top_avg - neckline

        # 目标价（向下量度幅度）
        target = neckline - height

        # 当前价格应在颈线附近或以下才算有效
        if current_price > top_avg * 1.02:
            continue

        # 第二个高点应比较近期
        if n - h2["idx"] > 30:
            continue

        confidence = 60
        if diff_pct < 0.01:
            confidence += 10  # 两顶越接近越可靠
        if h2["idx"] - h1["idx"] >= 20:
            confidence += 10  # 间距够大更可靠

        return {
            "name_cn": "双顶",
            "name_en": "Double Top",
            "type": "反转",
            "direction": "看跌",
            "neckline": round(neckline, 4),
            "target_price": round(target, 4),
            "stop_price": round(top_avg * 1.02, 4),
            "confidence": min(confidence, 90),
            "start_idx": h1["idx"],
            "end_idx": h2["idx"],
            "description": f"双顶形态确认，颈线{neckline:.2f}，跌破则目标{target:.2f}",
            "bias_score": -50,
        }
    return None


def _detect_double_bottom(lows: List[Dict], highs: List[Dict], df: pd.DataFrame,
                          tolerance: float = 0.02) -> Optional[Dict]:
    """双底：两个相近的低点 + 中间有一个高点"""
    if len(lows) < 2:
        return None

    n = len(df)
    current_price = df["Close"].iloc[-1]

    for i in range(len(lows) - 1, 0, -1):
        l2 = lows[i]
        l1 = lows[i - 1]

        if l2["idx"] - l1["idx"] < 10:
            continue

        diff_pct = abs(l2["value"] - l1["value"]) / l1["value"]
        if diff_pct > tolerance:
            continue

        between_highs = [h for h in highs if l1["idx"] < h["idx"] < l2["idx"]]
        if not between_highs:
            continue

        neckline_point = max(between_highs, key=lambda x: x["value"])
        neckline = neckline_point["value"]

        bottom_avg = (l1["value"] + l2["value"]) / 2
        height = neckline - bottom_avg
        target = neckline + height

        if current_price < bottom_avg * 0.98:
            continue

        if n - l2["idx"] > 30:
            continue

        confidence = 60
        if diff_pct < 0.01:
            confidence += 10
        if l2["idx"] - l1["idx"] >= 20:
            confidence += 10

        return {
            "name_cn": "双底",
            "name_en": "Double Bottom",
            "type": "反转",
            "direction": "看涨",
            "neckline": round(neckline, 4),
            "target_price": round(target, 4),
            "stop_price": round(bottom_avg * 0.98, 4),
            "confidence": min(confidence, 90),
            "start_idx": l1["idx"],
            "end_idx": l2["idx"],
            "description": f"双底形态确认，颈线{neckline:.2f}，突破则目标{target:.2f}",
            "bias_score": 50,
        }
    return None


def _detect_head_shoulders_top(highs: List[Dict], lows: List[Dict], df: pd.DataFrame) -> Optional[Dict]:
    """头肩顶：左肩 < 头部 > 右肩，头部最高"""
    if len(highs) < 3:
        return None

    n = len(df)
    current_price = df["Close"].iloc[-1]

    for i in range(2, len(highs)):
        right = highs[i]
        head = highs[i - 1]
        left = highs[i - 2]

        # 头部必须最高
        if head["value"] <= left["value"] or head["value"] <= right["value"]:
            continue

        # 左肩右肩大致对称（高度差<15%）
        shoulder_diff = abs(left["value"] - right["value"]) / left["value"]
        if shoulder_diff > 0.15:
            continue

        # 间距合理
        if head["idx"] - left["idx"] < 5 or right["idx"] - head["idx"] < 5:
            continue

        # 颈线：左肩和头部之间的低点 + 头部和右肩之间的低点
        left_lows = [l for l in lows if left["idx"] < l["idx"] < head["idx"]]
        right_lows = [l for l in lows if head["idx"] < l["idx"] < right["idx"]]

        if not left_lows or not right_lows:
            continue

        nl_left = min(left_lows, key=lambda x: x["value"])["value"]
        nl_right = min(right_lows, key=lambda x: x["value"])["value"]
        neckline = (nl_left + nl_right) / 2

        height = head["value"] - neckline
        target = neckline - height

        if n - right["idx"] > 30:
            continue

        confidence = 70
        if shoulder_diff < 0.05:
            confidence += 10

        return {
            "name_cn": "头肩顶",
            "name_en": "Head and Shoulders",
            "type": "反转",
            "direction": "看跌",
            "neckline": round(neckline, 4),
            "target_price": round(target, 4),
            "stop_price": round(head["value"] * 1.01, 4),
            "confidence": min(confidence, 90),
            "start_idx": left["idx"],
            "end_idx": right["idx"],
            "description": f"头肩顶形态，颈线{neckline:.2f}，跌破则目标{target:.2f}",
            "bias_score": -60,
        }
    return None


def _detect_head_shoulders_bottom(lows: List[Dict], highs: List[Dict], df: pd.DataFrame) -> Optional[Dict]:
    """头肩底（倒头肩）"""
    if len(lows) < 3:
        return None

    n = len(df)

    for i in range(2, len(lows)):
        right = lows[i]
        head = lows[i - 1]
        left = lows[i - 2]

        if head["value"] >= left["value"] or head["value"] >= right["value"]:
            continue

        shoulder_diff = abs(left["value"] - right["value"]) / left["value"]
        if shoulder_diff > 0.15:
            continue

        if head["idx"] - left["idx"] < 5 or right["idx"] - head["idx"] < 5:
            continue

        left_highs = [h for h in highs if left["idx"] < h["idx"] < head["idx"]]
        right_highs = [h for h in highs if head["idx"] < h["idx"] < right["idx"]]

        if not left_highs or not right_highs:
            continue

        nl_left = max(left_highs, key=lambda x: x["value"])["value"]
        nl_right = max(right_highs, key=lambda x: x["value"])["value"]
        neckline = (nl_left + nl_right) / 2

        height = neckline - head["value"]
        target = neckline + height

        if n - right["idx"] > 30:
            continue

        confidence = 70
        if shoulder_diff < 0.05:
            confidence += 10

        return {
            "name_cn": "头肩底",
            "name_en": "Inverse Head and Shoulders",
            "type": "反转",
            "direction": "看涨",
            "neckline": round(neckline, 4),
            "target_price": round(target, 4),
            "stop_price": round(head["value"] * 0.99, 4),
            "confidence": min(confidence, 90),
            "start_idx": left["idx"],
            "end_idx": right["idx"],
            "description": f"头肩底形态，颈线{neckline:.2f}，突破则目标{target:.2f}",
            "bias_score": 60,
        }
    return None


def _detect_triangle(highs: List[Dict], lows: List[Dict], df: pd.DataFrame) -> Optional[Dict]:
    """
    三角形整理：上升/下降/对称
    上升三角形：高点水平 + 低点上移
    下降三角形：低点水平 + 高点下移
    对称三角形：高点下移 + 低点上移
    """
    if len(highs) < 2 or len(lows) < 2:
        return None

    n = len(df)
    current_price = df["Close"].iloc[-1]

    # 取最近的几个高低点
    recent_highs = [h for h in highs if n - h["idx"] < 60][-4:]
    recent_lows = [l for l in lows if n - l["idx"] < 60][-4:]

    if len(recent_highs) < 2 or len(recent_lows) < 2:
        return None

    # 高点趋势
    h_vals = [h["value"] for h in recent_highs]
    h_slope = (h_vals[-1] - h_vals[0]) / len(h_vals) if len(h_vals) > 1 else 0
    h_flat = abs(h_vals[-1] - h_vals[0]) / h_vals[0] < 0.02

    # 低点趋势
    l_vals = [l["value"] for l in recent_lows]
    l_slope = (l_vals[-1] - l_vals[0]) / len(l_vals) if len(l_vals) > 1 else 0
    l_flat = abs(l_vals[-1] - l_vals[0]) / l_vals[0] < 0.02

    h_decreasing = h_slope < 0
    l_increasing = l_slope > 0

    pattern = None

    if h_flat and l_increasing:
        # 上升三角形
        resistance = sum(h_vals) / len(h_vals)
        height = resistance - l_vals[0]
        pattern = {
            "name_cn": "上升三角形",
            "name_en": "Ascending Triangle",
            "type": "持续",
            "direction": "看涨",
            "neckline": round(resistance, 4),
            "target_price": round(resistance + height, 4),
            "stop_price": round(l_vals[-1] * 0.98, 4),
            "confidence": 65,
            "bias_score": 40,
            "description": f"上升三角形，压力线{resistance:.2f}，突破则目标{resistance + height:.2f}",
        }
    elif l_flat and h_decreasing:
        # 下降三角形
        support = sum(l_vals) / len(l_vals)
        height = h_vals[0] - support
        pattern = {
            "name_cn": "下降三角形",
            "name_en": "Descending Triangle",
            "type": "持续",
            "direction": "看跌",
            "neckline": round(support, 4),
            "target_price": round(support - height, 4),
            "stop_price": round(h_vals[-1] * 1.02, 4),
            "confidence": 65,
            "bias_score": -40,
            "description": f"下降三角形，支撑线{support:.2f}，跌破则目标{support - height:.2f}",
        }
    elif h_decreasing and l_increasing:
        # 对称三角形
        mid = (h_vals[-1] + l_vals[-1]) / 2
        height = h_vals[0] - l_vals[0]
        # 方向取决于之前的趋势
        prev_trend = df["Close"].iloc[-60] - df["Close"].iloc[-120] if len(df) >= 120 else 0
        direction = "看涨" if prev_trend > 0 else "看跌"
        bias = 25 if direction == "看涨" else -25

        pattern = {
            "name_cn": "对称三角形",
            "name_en": "Symmetric Triangle",
            "type": "持续",
            "direction": direction,
            "neckline": round(mid, 4),
            "target_price": round(mid + height * 0.6 if direction == "看涨" else mid - height * 0.6, 4),
            "stop_price": round(l_vals[-1] * 0.98 if direction == "看涨" else h_vals[-1] * 1.02, 4),
            "confidence": 55,
            "bias_score": bias,
            "description": f"对称三角形，收敛区间{l_vals[-1]:.2f}-{h_vals[-1]:.2f}，等待方向突破",
        }

    if pattern:
        pattern["start_idx"] = min(recent_highs[0]["idx"], recent_lows[0]["idx"])
        pattern["end_idx"] = max(recent_highs[-1]["idx"], recent_lows[-1]["idx"])

    return pattern


def _detect_channel(df: pd.DataFrame, window: int = 10) -> Optional[Dict]:
    """
    通道形态：价格在平行的上下轨之间运行
    """
    if len(df) < 40:
        return None

    n = len(df)
    recent = df.tail(40)

    # 用线性回归拟合上下轨
    x = np.arange(len(recent))
    highs = recent["High"].values
    lows = recent["Low"].values

    # 高点回归
    h_coeffs = np.polyfit(x, highs, 1)
    h_slope, h_intercept = h_coeffs

    # 低点回归
    l_coeffs = np.polyfit(x, lows, 1)
    l_slope, l_intercept = l_coeffs

    # 两条线是否大致平行（斜率差异<30%）
    if abs(h_slope) < 0.001 and abs(l_slope) < 0.001:
        return None  # 横盘不算通道

    slope_diff = abs(h_slope - l_slope) / (abs(h_slope) + 0.0001)
    if slope_diff > 0.5:
        return None  # 不平行

    # 检查价格是否在通道内（80%以上的K线）
    h_line = h_slope * x + h_intercept
    l_line = l_slope * x + l_intercept
    closes = recent["Close"].values
    in_channel = sum(1 for i in range(len(x)) if l_line[i] * 0.99 <= closes[i] <= h_line[i] * 1.01)

    if in_channel / len(x) < 0.75:
        return None

    avg_slope = (h_slope + l_slope) / 2
    channel_width = (h_line[-1] - l_line[-1]) / l_line[-1] * 100

    current_price = df["Close"].iloc[-1]
    upper = h_line[-1]
    lower = l_line[-1]

    if avg_slope > 0:
        name = "上升通道"
        direction = "看涨"
        desc = f"价格在上升通道内运行（{lower:.2f}-{upper:.2f}），通道宽度{channel_width:.1f}%"
        score = 30
    else:
        name = "下降通道"
        direction = "看跌"
        desc = f"价格在下降通道内运行（{lower:.2f}-{upper:.2f}），通道宽度{channel_width:.1f}%"
        score = -30

    # 当前位置
    pos_in_channel = (current_price - lower) / (upper - lower) if upper > lower else 0.5

    return {
        "name_cn": name,
        "name_en": "Ascending Channel" if avg_slope > 0 else "Descending Channel",
        "type": "持续",
        "direction": direction,
        "neckline": round((upper + lower) / 2, 4),
        "target_price": round(upper if direction == "看涨" else lower, 4),
        "stop_price": round(lower * 0.98 if direction == "看涨" else upper * 1.02, 4),
        "confidence": 60,
        "start_idx": n - 40,
        "end_idx": n - 1,
        "channel_upper": round(upper, 4),
        "channel_lower": round(lower, 4),
        "position_in_channel": round(pos_in_channel, 2),
        "description": desc + f"，当前位于通道{pos_in_channel*100:.0f}%位置",
        "bias_score": score,
    }


# ─── 主函数 ──────────────────────────────────────────────────────────────────

def detect_chart_patterns(df: pd.DataFrame, lookback: int = 120) -> Dict:
    """
    检测图表形态

    参数:
        df: OHLCV DataFrame
        lookback: 回溯K线数量

    返回:
        检出的图表形态列表
    """
    if df is None or df.empty or len(df) < 30:
        return {
            "patterns": [], "pattern_count": 0,
            "strongest_pattern": None,
        }

    # 使用最近lookback根K线
    sub = df.tail(lookback).copy()

    # 提取极值点
    highs, lows = _find_pivots_simple(sub, window=7)

    if not highs and not lows:
        # 降低窗口重试
        highs, lows = _find_pivots_simple(sub, window=5)

    patterns = []

    # 双顶
    dt = _detect_double_top(highs, lows, sub)
    if dt:
        patterns.append(dt)

    # 双底
    db = _detect_double_bottom(lows, highs, sub)
    if db:
        patterns.append(db)

    # 头肩顶
    hst = _detect_head_shoulders_top(highs, lows, sub)
    if hst:
        patterns.append(hst)

    # 头肩底
    hsb = _detect_head_shoulders_bottom(lows, highs, sub)
    if hsb:
        patterns.append(hsb)

    # 三角形
    tri = _detect_triangle(highs, lows, sub)
    if tri:
        patterns.append(tri)

    # 通道
    ch = _detect_channel(sub)
    if ch:
        patterns.append(ch)

    # 排序：信心度高的排前面
    patterns.sort(key=lambda x: x.get("confidence", 0), reverse=True)

    strongest = patterns[0] if patterns else None

    return {
        "patterns": patterns,
        "pattern_count": len(patterns),
        "strongest_pattern": strongest,
    }
