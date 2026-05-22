"""
candlestick_patterns.py — K线形态识别引擎
识别20种最常见且最可靠的K线形态，用于短线交易入场/出场时机判断
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional


# ─── 形态定义 ────────────────────────────────────────────────────────────────

PATTERN_DEFS = {
    # 反转看涨
    "hammer":           {"cn": "锤子线",     "type": "反转", "dir": "看涨", "reliability": "强", "score": 50},
    "inverted_hammer":  {"cn": "倒锤子",     "type": "反转", "dir": "看涨", "reliability": "中", "score": 35},
    "bullish_engulfing": {"cn": "看涨吞没",  "type": "反转", "dir": "看涨", "reliability": "强", "score": 60},
    "morning_star":     {"cn": "早晨之星",   "type": "反转", "dir": "看涨", "reliability": "强", "score": 65},
    "piercing_line":    {"cn": "穿刺形态",   "type": "反转", "dir": "看涨", "reliability": "中", "score": 40},
    "three_white_soldiers": {"cn": "三白兵", "type": "反转", "dir": "看涨", "reliability": "强", "score": 60},
    "bullish_harami":   {"cn": "看涨孕线",   "type": "反转", "dir": "看涨", "reliability": "中", "score": 30},
    "dragonfly_doji":   {"cn": "蜻蜓十字",   "type": "反转", "dir": "看涨", "reliability": "中", "score": 35},
    # 反转看跌
    "shooting_star":    {"cn": "射击之星",   "type": "反转", "dir": "看跌", "reliability": "强", "score": -50},
    "bearish_engulfing": {"cn": "看跌吞没",  "type": "反转", "dir": "看跌", "reliability": "强", "score": -60},
    "evening_star":     {"cn": "黄昏之星",   "type": "反转", "dir": "看跌", "reliability": "强", "score": -65},
    "dark_cloud_cover": {"cn": "乌云盖顶",   "type": "反转", "dir": "看跌", "reliability": "中", "score": -40},
    "three_black_crows": {"cn": "三黑鸦",    "type": "反转", "dir": "看跌", "reliability": "强", "score": -60},
    "bearish_harami":   {"cn": "看跌孕线",   "type": "反转", "dir": "看跌", "reliability": "中", "score": -30},
    "hanging_man":      {"cn": "上吊线",     "type": "反转", "dir": "看跌", "reliability": "中", "score": -40},
    "gravestone_doji":  {"cn": "墓碑十字",   "type": "反转", "dir": "看跌", "reliability": "中", "score": -35},
    # 持续/中性
    "rising_three":     {"cn": "上升三法",   "type": "持续", "dir": "看涨", "reliability": "中", "score": 30},
    "falling_three":    {"cn": "下降三法",   "type": "持续", "dir": "看跌", "reliability": "中", "score": -30},
    "doji":             {"cn": "十字星",     "type": "中性", "dir": "中性", "reliability": "弱", "score": 0},
    "spinning_top":     {"cn": "陀螺线",     "type": "中性", "dir": "中性", "reliability": "弱", "score": 0},
}


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def _body(o, c):
    """实体大小（绝对值）"""
    return abs(c - o)

def _range(h, l):
    """总振幅"""
    return h - l if h > l else 0.0001

def _upper_shadow(o, c, h):
    return h - max(o, c)

def _lower_shadow(o, c, l):
    return min(o, c) - l

def _is_bullish(o, c):
    return c > o

def _is_bearish(o, c):
    return c < o

def _body_pct(o, c, h, l):
    """实体占总振幅的比例"""
    r = _range(h, l)
    return _body(o, c) / r if r > 0 else 0

def _lower_shadow_pct(o, c, l, h):
    r = _range(h, l)
    return _lower_shadow(o, c, l) / r if r > 0 else 0

def _upper_shadow_pct(o, c, h, l):
    r = _range(h, l)
    return _upper_shadow(o, c, h) / r if r > 0 else 0

def _is_doji(o, c, h, l, threshold=0.1):
    return _body_pct(o, c, h, l) < threshold

def _avg_body(df, n=10):
    """过去N根K线的平均实体大小"""
    bodies = abs(df["Close"].tail(n) - df["Open"].tail(n))
    return bodies.mean() if len(bodies) > 0 else 0


# ─── 单根K线形态 ─────────────────────────────────────────────────────────────

def _check_hammer(df, i):
    """锤子线：下影线≥实体2倍，上影线极短，处于下跌趋势"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    body = _body(o, c)
    ls = _lower_shadow(o, c, l)
    us = _upper_shadow(o, c, h)
    rng = _range(h, l)
    if rng == 0:
        return False
    # 下影线 >= 2x 实体, 上影线 < 实体 * 0.3, 实体不太小
    if ls >= body * 2 and us <= body * 0.5 and _body_pct(o, c, h, l) >= 0.1:
        # 需在下跌趋势中（前5根K线整体下行）
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] < prev_close.iloc[0]:
                return True
    return False

def _check_hanging_man(df, i):
    """上吊线：形态同锤子线，但出现在上涨趋势中"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    body = _body(o, c)
    ls = _lower_shadow(o, c, l)
    us = _upper_shadow(o, c, h)
    rng = _range(h, l)
    if rng == 0:
        return False
    if ls >= body * 2 and us <= body * 0.5 and _body_pct(o, c, h, l) >= 0.1:
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] > prev_close.iloc[0]:
                return True
    return False

def _check_shooting_star(df, i):
    """射击之星：上影线≥实体2倍，下影线极短，在上涨趋势中"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    body = _body(o, c)
    us = _upper_shadow(o, c, h)
    ls = _lower_shadow(o, c, l)
    if _range(h, l) == 0:
        return False
    if us >= body * 2 and ls <= body * 0.5 and _body_pct(o, c, h, l) >= 0.1:
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] > prev_close.iloc[0]:
                return True
    return False

def _check_inverted_hammer(df, i):
    """倒锤子：形态同射击之星，但在下跌趋势中"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    body = _body(o, c)
    us = _upper_shadow(o, c, h)
    ls = _lower_shadow(o, c, l)
    if _range(h, l) == 0:
        return False
    if us >= body * 2 and ls <= body * 0.5 and _body_pct(o, c, h, l) >= 0.1:
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] < prev_close.iloc[0]:
                return True
    return False

def _check_doji(df, i):
    """十字星：实体极小"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    return _is_doji(o, c, h, l, 0.05) and _range(h, l) > 0

def _check_dragonfly_doji(df, i):
    """蜻蜓十字：十字星+长下影线，在下跌中"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    if not _is_doji(o, c, h, l, 0.08):
        return False
    if _lower_shadow_pct(o, c, l, h) >= 0.6 and _upper_shadow_pct(o, c, h, l) <= 0.1:
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] < prev_close.iloc[0]:
                return True
    return False

def _check_gravestone_doji(df, i):
    """墓碑十字：十字星+长上影线，在上涨中"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    if not _is_doji(o, c, h, l, 0.08):
        return False
    if _upper_shadow_pct(o, c, h, l) >= 0.6 and _lower_shadow_pct(o, c, l, h) <= 0.1:
        if i >= 5:
            prev_close = df["Close"].iloc[i-5:i]
            if prev_close.iloc[-1] > prev_close.iloc[0]:
                return True
    return False

def _check_spinning_top(df, i):
    """陀螺线：小实体+上下影线均较长"""
    o, h, l, c = df["Open"].iloc[i], df["High"].iloc[i], df["Low"].iloc[i], df["Close"].iloc[i]
    bp = _body_pct(o, c, h, l)
    usp = _upper_shadow_pct(o, c, h, l)
    lsp = _lower_shadow_pct(o, c, l, h)
    return 0.05 < bp < 0.35 and usp > 0.25 and lsp > 0.25


# ─── 双根K线形态 ─────────────────────────────────────────────────────────────

def _check_bullish_engulfing(df, i):
    """看涨吞没：前阴后阳，阳线实体完全包裹阴线实体，在下跌中"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    if not _is_bearish(o0, c0):
        return False
    if not _is_bullish(o1, c1):
        return False
    # 阳线实体包裹阴线实体
    if o1 <= c0 and c1 >= o0:
        if i >= 5:
            prev = df["Close"].iloc[i-5:i-1]
            if len(prev) >= 2 and prev.iloc[-1] < prev.iloc[0]:
                return True
    return False

def _check_bearish_engulfing(df, i):
    """看跌吞没：前阳后阴，阴线实体完全包裹阳线实体，在上涨中"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    if not _is_bullish(o0, c0):
        return False
    if not _is_bearish(o1, c1):
        return False
    if o1 >= c0 and c1 <= o0:
        if i >= 5:
            prev = df["Close"].iloc[i-5:i-1]
            if len(prev) >= 2 and prev.iloc[-1] > prev.iloc[0]:
                return True
    return False

def _check_bullish_harami(df, i):
    """看涨孕线：前大阴+后小阳（实体在前阴实体内），下跌中"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    h0, l0 = df["High"].iloc[i-1], df["Low"].iloc[i-1]
    if not _is_bearish(o0, c0) or not _is_bullish(o1, c1):
        return False
    # 后者实体在前者实体内
    if o1 > c0 and c1 < o0 and _body(o1, c1) < _body(o0, c0) * 0.6:
        return True
    return False

def _check_bearish_harami(df, i):
    """看跌孕线：前大阳+后小阴（实体在前阳实体内），上涨中"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    if not _is_bullish(o0, c0) or not _is_bearish(o1, c1):
        return False
    if o1 < c0 and c1 > o0 and _body(o1, c1) < _body(o0, c0) * 0.6:
        return True
    return False

def _check_piercing_line(df, i):
    """穿刺形态：前阴+阳线开低于前低，收于前实体50%以上"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    l0 = df["Low"].iloc[i-1]
    if not _is_bearish(o0, c0) or not _is_bullish(o1, c1):
        return False
    mid0 = (o0 + c0) / 2
    if o1 <= c0 and c1 > mid0 and c1 < o0:
        return True
    return False

def _check_dark_cloud_cover(df, i):
    """乌云盖顶：前阳+阴线开高于前高，收于前实体50%以下"""
    if i < 1:
        return False
    o0, c0 = df["Open"].iloc[i-1], df["Close"].iloc[i-1]
    o1, c1 = df["Open"].iloc[i], df["Close"].iloc[i]
    h0 = df["High"].iloc[i-1]
    if not _is_bullish(o0, c0) or not _is_bearish(o1, c1):
        return False
    mid0 = (o0 + c0) / 2
    if o1 >= c0 and c1 < mid0 and c1 > o0:
        return True
    return False


# ─── 三根K线形态 ─────────────────────────────────────────────────────────────

def _check_morning_star(df, i):
    """早晨之星：大阴 + 小实体(跳空低开) + 大阳(收于第一根50%以上)"""
    if i < 2:
        return False
    o0, c0 = df["Open"].iloc[i-2], df["Close"].iloc[i-2]
    o1, c1, h1, l1 = df["Open"].iloc[i-1], df["Close"].iloc[i-1], df["High"].iloc[i-1], df["Low"].iloc[i-1]
    o2, c2 = df["Open"].iloc[i], df["Close"].iloc[i]

    if not _is_bearish(o0, c0):
        return False
    if not _is_bullish(o2, c2):
        return False
    # 中间小实体
    if _body_pct(o1, c1, h1, l1) > 0.4:
        return False
    # 第三根收于第一根实体50%以上
    mid0 = (o0 + c0) / 2
    if c2 > mid0:
        return True
    return False

def _check_evening_star(df, i):
    """黄昏之星：大阳 + 小实体(跳空高开) + 大阴(收于第一根50%以下)"""
    if i < 2:
        return False
    o0, c0 = df["Open"].iloc[i-2], df["Close"].iloc[i-2]
    o1, c1, h1, l1 = df["Open"].iloc[i-1], df["Close"].iloc[i-1], df["High"].iloc[i-1], df["Low"].iloc[i-1]
    o2, c2 = df["Open"].iloc[i], df["Close"].iloc[i]

    if not _is_bullish(o0, c0):
        return False
    if not _is_bearish(o2, c2):
        return False
    if _body_pct(o1, c1, h1, l1) > 0.4:
        return False
    mid0 = (o0 + c0) / 2
    if c2 < mid0:
        return True
    return False

def _check_three_white_soldiers(df, i):
    """三白兵：连续三根阳线，每根开于前一根实体内，收于新高"""
    if i < 2:
        return False
    results = []
    for k in range(i-2, i+1):
        o, c = df["Open"].iloc[k], df["Close"].iloc[k]
        if not _is_bullish(o, c):
            return False
        results.append((o, c))
    # 每根开于前一根实体内
    for k in range(1, 3):
        prev_o, prev_c = results[k-1]
        cur_o, cur_c = results[k]
        if not (prev_o <= cur_o <= prev_c):
            return False
        if cur_c <= prev_c:
            return False
    return True

def _check_three_black_crows(df, i):
    """三黑鸦：连续三根阴线，每根开于前一根实体内，收于新低"""
    if i < 2:
        return False
    results = []
    for k in range(i-2, i+1):
        o, c = df["Open"].iloc[k], df["Close"].iloc[k]
        if not _is_bearish(o, c):
            return False
        results.append((o, c))
    for k in range(1, 3):
        prev_o, prev_c = results[k-1]
        cur_o, cur_c = results[k]
        if not (prev_c <= cur_o <= prev_o):
            return False
        if cur_c >= prev_c:
            return False
    return True


# ─── 五根K线形态 ─────────────────────────────────────────────────────────────

def _check_rising_three(df, i):
    """上升三法：大阳 + 2-3根小阴(在大阳范围内) + 大阳(收于新高)"""
    if i < 4:
        return False
    o0, c0 = df["Open"].iloc[i-4], df["Close"].iloc[i-4]
    if not _is_bullish(o0, c0):
        return False
    # 中间2-3根小K线在第一根范围内
    for k in range(i-3, i):
        hk, lk = df["High"].iloc[k], df["Low"].iloc[k]
        if hk > df["High"].iloc[i-4] or lk < df["Low"].iloc[i-4]:
            return False
    # 最后一根阳线收于新高
    o_last, c_last = df["Open"].iloc[i], df["Close"].iloc[i]
    if _is_bullish(o_last, c_last) and c_last > c0:
        return True
    return False

def _check_falling_three(df, i):
    """下降三法：大阴 + 2-3根小阳(在大阴范围内) + 大阴(收于新低)"""
    if i < 4:
        return False
    o0, c0 = df["Open"].iloc[i-4], df["Close"].iloc[i-4]
    if not _is_bearish(o0, c0):
        return False
    for k in range(i-3, i):
        hk, lk = df["High"].iloc[k], df["Low"].iloc[k]
        if hk > df["High"].iloc[i-4] or lk < df["Low"].iloc[i-4]:
            return False
    o_last, c_last = df["Open"].iloc[i], df["Close"].iloc[i]
    if _is_bearish(o_last, c_last) and c_last < c0:
        return True
    return False


# ─── 描述生成 ────────────────────────────────────────────────────────────────

DESCRIPTIONS = {
    "hammer":           "长下影线实体小，下方买盘支撑明显，下跌趋势中出现可能预示反转向上",
    "inverted_hammer":  "长上影线实体小，在下跌末端出现，暗示买方开始尝试推高价格",
    "bullish_engulfing": "阳线实体完全包裹前一根阴线，买方力量强劲压倒卖方，强反转信号",
    "morning_star":     "大阴+小实体+大阳三根组合，经典底部反转形态，可靠度高",
    "piercing_line":    "阳线开于前阴低点下方但收于其实体50%以上，下跌中买方反攻信号",
    "three_white_soldiers": "连续三根递增阳线，强烈看涨信号，趋势反转确认度高",
    "bullish_harami":   "大阴后出现小阳线（孕育在阴线实体内），下跌减速，可能反转",
    "dragonfly_doji":   "开盘收盘接近最高价，长下影线，下跌中出现暗示买方强力承接",
    "shooting_star":    "长上影线实体小，上涨趋势中出现，上方抛压沉重，可能反转向下",
    "bearish_engulfing": "阴线实体完全包裹前一根阳线，卖方力量压倒买方，强反转信号",
    "evening_star":     "大阳+小实体+大阴三根组合，经典顶部反转形态，可靠度高",
    "dark_cloud_cover": "阴线开于前阳高点上方但收于其实体50%以下，上涨中的反转预警",
    "three_black_crows": "连续三根递减阴线，强烈看跌信号，趋势反转确认度高",
    "bearish_harami":   "大阳后出现小阴线（孕育在阳线实体内），上涨减速，可能反转",
    "hanging_man":      "形态同锤子线但出现在上涨末端，暗示顶部卖压，需后续阴线确认",
    "gravestone_doji":  "开盘收盘接近最低价，长上影线，上涨中出现暗示卖方力量增强",
    "rising_three":     "上涨中大阳后出现小幅回调再创新高，趋势延续确认，可继续持多",
    "falling_three":    "下跌中大阴后出现小幅反弹再创新低，趋势延续确认，空头延续",
    "doji":             "开盘收盘极为接近，市场多空力量均衡，关键位置出现需关注后续方向",
    "spinning_top":     "小实体上下影线均较长，市场犹豫不决，可能变盘前兆",
}


# ─── 主检测函数 ───────────────────────────────────────────────────────────────

# 所有检测器列表
_DETECTORS = [
    ("hammer",             _check_hammer),
    ("hanging_man",        _check_hanging_man),
    ("shooting_star",      _check_shooting_star),
    ("inverted_hammer",    _check_inverted_hammer),
    ("doji",               _check_doji),
    ("dragonfly_doji",     _check_dragonfly_doji),
    ("gravestone_doji",    _check_gravestone_doji),
    ("spinning_top",       _check_spinning_top),
    ("bullish_engulfing",  _check_bullish_engulfing),
    ("bearish_engulfing",  _check_bearish_engulfing),
    ("bullish_harami",     _check_bullish_harami),
    ("bearish_harami",     _check_bearish_harami),
    ("piercing_line",      _check_piercing_line),
    ("dark_cloud_cover",   _check_dark_cloud_cover),
    ("morning_star",       _check_morning_star),
    ("evening_star",       _check_evening_star),
    ("three_white_soldiers", _check_three_white_soldiers),
    ("three_black_crows",  _check_three_black_crows),
    ("rising_three",       _check_rising_three),
    ("falling_three",      _check_falling_three),
]


def detect_patterns(df: pd.DataFrame, lookback: int = 5) -> Dict:
    """
    检测最近 lookback 根K线中的形态
    返回检出的所有形态及综合评分
    """
    if df is None or df.empty or len(df) < 6:
        return {
            "patterns": [], "pattern_count": 0,
            "bullish_patterns": 0, "bearish_patterns": 0,
            "strongest_signal": None, "combined_bias_score": 0,
        }

    n = len(df)
    start_idx = max(5, n - lookback)  # 至少从第6根开始（需前5根判断趋势）

    found = []
    for idx in range(start_idx, n):
        for pat_key, detector_fn in _DETECTORS:
            try:
                if detector_fn(df, idx):
                    defn = PATTERN_DEFS[pat_key]
                    # 避免同一根K线同时报告锤子线和十字星等冲突形态
                    # 优先级：专业形态 > 通用形态
                    found.append({
                        "name_cn": defn["cn"],
                        "name_en": pat_key.replace("_", " ").title(),
                        "type": defn["type"],
                        "direction": defn["dir"],
                        "reliability": defn["reliability"],
                        "bar_index": idx - n,  # 负索引
                        "description": DESCRIPTIONS.get(pat_key, ""),
                        "bias_score": defn["score"],
                        "_key": pat_key,
                        "_bar": idx,
                    })
            except Exception:
                continue

    # 去重：同一根K线只保留得分绝对值最高的形态
    bar_best = {}
    for p in found:
        bar = p["_bar"]
        if bar not in bar_best or abs(p["bias_score"]) > abs(bar_best[bar]["bias_score"]):
            bar_best[bar] = p
    patterns = list(bar_best.values())

    # 排序：最近的K线优先
    patterns.sort(key=lambda x: x["_bar"], reverse=True)

    # 清理内部字段
    for p in patterns:
        p.pop("_key", None)
        p.pop("_bar", None)

    bullish = sum(1 for p in patterns if p["direction"] == "看涨")
    bearish = sum(1 for p in patterns if p["direction"] == "看跌")

    # 综合评分：最近的形态权重更高
    if patterns:
        total_score = 0
        total_weight = 0
        for i, p in enumerate(patterns):
            w = 1.0 / (i + 1)  # 最近的权重最高
            total_score += p["bias_score"] * w
            total_weight += w
        combined = total_score / total_weight if total_weight > 0 else 0
    else:
        combined = 0

    # 最强信号
    strongest = None
    if patterns:
        strongest_p = max(patterns, key=lambda x: abs(x["bias_score"]))
        strongest = strongest_p["name_cn"]

    return {
        "patterns": patterns,
        "pattern_count": len(patterns),
        "bullish_patterns": bullish,
        "bearish_patterns": bearish,
        "strongest_signal": strongest,
        "combined_bias_score": round(combined, 1),
    }
