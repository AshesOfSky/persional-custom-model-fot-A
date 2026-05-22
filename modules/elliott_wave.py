"""
elliott_wave.py — Elliott波浪理论检测引擎
多周期(1h/4h/日线/周线)波浪识别 + Fibonacci评分 + 综合研判
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger(__name__)

# ── Fibonacci理想比率 ────────────────────────────────────────────────

FIB_IDEAL = {
    "w2_retrace": [0.50, 0.618, 0.786],       # W2回撤W1
    "w3_extension": [1.618, 2.0, 2.618],       # W3延伸W1
    "w4_retrace": [0.236, 0.382, 0.50],        # W4回撤W3
    "w5_extension": [0.618, 1.0, 1.618],       # W5相对W1
    "wB_retrace": [0.50, 0.618, 0.786],        # B回撤A
    "wC_extension": [0.618, 1.0, 1.618],       # C相对A
}

TOLERANCE = 0.08  # Fibonacci匹配容差

# 浪位→方向偏向
WAVE_BIAS = {
    "impulse_W1": +1.0,
    "impulse_W2": -0.5,
    "impulse_W3": +2.0,
    "impulse_W4": -0.5,
    "impulse_W5": +0.5,
    "corrective_A": -1.0,
    "corrective_B": +0.3,
    "corrective_C": -1.5,
}

# 多周期权重
TF_WEIGHTS = {"周线": 0.35, "日线": 0.30, "4小时": 0.20, "1小时": 0.15}


# ── 锯齿线构建 ──────────────────────────────────────────────────────

def _build_zigzag(swing_highs: List[Dict], swing_lows: List[Dict]) -> List[Dict]:
    """
    合并高低点按时间排序，去除连续同类型，生成交替锯齿线
    Returns: [{"index", "date", "price", "type": "high"|"low"}, ...]
    """
    all_points = []
    for h in swing_highs:
        all_points.append({**h, "type": "high"})
    for l in swing_lows:
        all_points.append({**l, "type": "low"})

    # 按index排序
    all_points.sort(key=lambda x: x["index"])

    if not all_points:
        return []

    # 去除连续同类型：保留最极端值
    zigzag = [all_points[0]]
    for pt in all_points[1:]:
        if pt["type"] == zigzag[-1]["type"]:
            # 同类型：保留更极端的
            if pt["type"] == "high" and pt["price"] > zigzag[-1]["price"]:
                zigzag[-1] = pt
            elif pt["type"] == "low" and pt["price"] < zigzag[-1]["price"]:
                zigzag[-1] = pt
        else:
            zigzag.append(pt)

    return zigzag


# ── 5浪推动检测 ─────────────────────────────────────────────────────

def _find_impulse_candidates(pivots: List[Dict], direction: str = "bullish") -> List[Dict]:
    """
    滑动窗口扫描5浪推动候选
    看涨: low-high-low-high-low-high (W0低→W1高→W2低→W3高→W4低→W5高)
    看跌: high-low-high-low-high-low
    需要6个交替枢轴点
    """
    candidates = []
    if len(pivots) < 6:
        return candidates

    if direction == "bullish":
        start_type = "low"
    else:
        start_type = "high"

    for i in range(len(pivots) - 5):
        pts = pivots[i:i + 6]

        # 检查起始类型
        if pts[0]["type"] != start_type:
            continue

        # 检查交替
        valid = True
        for j in range(1, 6):
            expected = "high" if (j % 2 == 1) == (direction == "bullish") else "low"
            if pts[j]["type"] != expected:
                valid = False
                break
        if not valid:
            continue

        prices = [pt["price"] for pt in pts]
        # W0, W1, W2, W3, W4, W5

        if direction == "bullish":
            # 基本形态: W1>W0, W3>W1, W5>W3(不必须), W2<W1, W4<W3
            if not (prices[1] > prices[0] and prices[3] > prices[1]):
                continue
        else:
            # 看跌: W1<W0, W3<W1
            if not (prices[1] < prices[0] and prices[3] < prices[1]):
                continue

        candidates.append({
            "pivots": pts,
            "prices": prices,
            "direction": direction,
            "start_index": pts[0]["index"],
            "end_index": pts[5]["index"],
        })

    return candidates


def _validate_impulse_rules(candidate: Dict) -> Tuple[bool, List[str]]:
    """
    验证Elliott三大铁律
    Returns: (is_valid, violations)
    """
    prices = candidate["prices"]
    direction = candidate["direction"]
    violations = []

    if direction == "bullish":
        w1_len = prices[1] - prices[0]  # W1上涨幅度
        w2_retrace = prices[1] - prices[2]  # W2回撤
        w3_len = prices[3] - prices[2]  # W3上涨
        w4_retrace = prices[3] - prices[4]  # W4回撤
        w5_len = prices[5] - prices[4]  # W5上涨

        # 规则1: W2回撤不超过W1的100%
        if w2_retrace >= w1_len:
            violations.append("W2回撤超过W1的100%")

        # 规则2: W3不是最短推动浪
        if w3_len <= w1_len and w3_len <= w5_len:
            violations.append("W3是最短推动浪")

        # 规则3: W4不进入W1区域 (W4低点 > W1高点)
        if prices[4] < prices[1]:
            violations.append("W4进入W1区域")

    else:  # bearish
        w1_len = prices[0] - prices[1]
        w2_retrace = prices[2] - prices[1]
        w3_len = prices[2] - prices[3]
        w4_retrace = prices[4] - prices[3]
        w5_len = prices[4] - prices[5]

        if w2_retrace >= w1_len:
            violations.append("W2回撤超过W1的100%")
        if w3_len <= w1_len and w3_len <= w5_len:
            violations.append("W3是最短推动浪")
        if prices[4] > prices[1]:
            violations.append("W4进入W1区域")

    return (len(violations) == 0, violations)


def _score_fibonacci_alignment(candidate: Dict) -> float:
    """
    Fibonacci比率评分 0-100
    """
    prices = candidate["prices"]
    direction = candidate["direction"]
    score = 0.0

    if direction == "bullish":
        w1_len = prices[1] - prices[0]
        w3_len = prices[3] - prices[2]
        w5_len = prices[5] - prices[4]
    else:
        w1_len = prices[0] - prices[1]
        w3_len = prices[2] - prices[3]
        w5_len = prices[4] - prices[5]

    if w1_len <= 0:
        return 0

    # W2回撤/W1 (权重25%)
    w2_retrace_ratio = abs(prices[1] - prices[2]) / w1_len if w1_len > 0 else 0
    score += _fib_match_score(w2_retrace_ratio, FIB_IDEAL["w2_retrace"]) * 25

    # W3延伸/W1 (权重40%)
    w3_ratio = w3_len / w1_len if w1_len > 0 else 0
    score += _fib_match_score(w3_ratio, FIB_IDEAL["w3_extension"]) * 40

    # W4回撤/W3 (权重20%)
    w4_retrace_ratio = abs(prices[3] - prices[4]) / w3_len if w3_len > 0 else 0
    score += _fib_match_score(w4_retrace_ratio, FIB_IDEAL["w4_retrace"]) * 20

    # W5/W1 (权重15%)
    w5_ratio = w5_len / w1_len if w1_len > 0 else 0
    score += _fib_match_score(w5_ratio, FIB_IDEAL["w5_extension"]) * 15

    return min(100, score)


def _fib_match_score(actual: float, ideals: List[float]) -> float:
    """计算实际比率与理想Fibonacci比率的匹配度 (0-1)"""
    if not ideals:
        return 0
    best = min(abs(actual - ideal) for ideal in ideals)
    if best <= TOLERANCE:
        return 1.0
    elif best <= TOLERANCE * 3:
        return 0.5
    else:
        return max(0, 1.0 - best)


# ── ABC修正检测 ─────────────────────────────────────────────────────

def _find_corrective_candidates(pivots: List[Dict], after_impulse: Dict = None) -> List[Dict]:
    """
    检测ABC修正浪 (3个枢轴点)
    看跌修正: high-low-high-low (A下B上C下)
    看涨修正: low-high-low-high (A上B下C上)
    """
    candidates = []
    if len(pivots) < 4:
        return candidates

    for i in range(len(pivots) - 3):
        pts = pivots[i:i + 4]
        # 看跌修正 (从高开始): high→low→high→low
        if pts[0]["type"] == "high" and pts[1]["type"] == "low" and \
           pts[2]["type"] == "high" and pts[3]["type"] == "low":
            a_len = pts[0]["price"] - pts[1]["price"]
            if a_len <= 0:
                continue
            b_retrace = (pts[2]["price"] - pts[1]["price"]) / a_len
            c_len = pts[2]["price"] - pts[3]["price"]
            c_ratio = c_len / a_len if a_len > 0 else 0

            b_score = _fib_match_score(b_retrace, FIB_IDEAL["wB_retrace"])
            c_score = _fib_match_score(c_ratio, FIB_IDEAL["wC_extension"])
            fib_score = (b_score * 50 + c_score * 50)

            candidates.append({
                "pivots": pts,
                "prices": [pt["price"] for pt in pts],
                "direction": "bearish_correction",
                "fib_score": fib_score,
                "start_index": pts[0]["index"],
                "end_index": pts[3]["index"],
            })

        # 看涨修正 (从低开始): low→high→low→high
        if pts[0]["type"] == "low" and pts[1]["type"] == "high" and \
           pts[2]["type"] == "low" and pts[3]["type"] == "high":
            a_len = pts[1]["price"] - pts[0]["price"]
            if a_len <= 0:
                continue
            b_retrace = (pts[1]["price"] - pts[2]["price"]) / a_len
            c_len = pts[3]["price"] - pts[2]["price"]
            c_ratio = c_len / a_len if a_len > 0 else 0

            b_score = _fib_match_score(b_retrace, FIB_IDEAL["wB_retrace"])
            c_score = _fib_match_score(c_ratio, FIB_IDEAL["wC_extension"])
            fib_score = (b_score * 50 + c_score * 50)

            candidates.append({
                "pivots": pts,
                "prices": [pt["price"] for pt in pts],
                "direction": "bullish_correction",
                "fib_score": fib_score,
                "start_index": pts[0]["index"],
                "end_index": pts[3]["index"],
            })

    return candidates


# ── 当前浪位判定 ────────────────────────────────────────────────────

def _determine_current_wave(
    impulses: List[Dict],
    corrections: List[Dict],
    pivots: List[Dict],
    current_price: float,
    df_len: int,
) -> Dict:
    """
    根据最近的浪型和当前价格判定所处浪位
    """
    result = {
        "type": "unknown",
        "wave_number": 0,
        "direction": "neutral",
        "confidence": 0,
        "phase": "未识别",
        "description": "无法识别当前波浪位置",
    }

    # 优先检查最近完成的浪型
    recent_impulse = impulses[-1] if impulses else None
    recent_correction = corrections[-1] if corrections else None

    # 判断哪个更近
    latest_end = 0
    latest_pattern = None
    latest_type = None

    if recent_impulse:
        imp_end = recent_impulse["end_index"]
        if imp_end > latest_end:
            latest_end = imp_end
            latest_pattern = recent_impulse
            latest_type = "impulse"

    if recent_correction:
        corr_end = recent_correction["end_index"]
        if corr_end > latest_end:
            latest_end = corr_end
            latest_pattern = recent_correction
            latest_type = "correction"

    if latest_pattern is None:
        # 尝试从不完整的枢轴序列推断
        if len(pivots) >= 3:
            last3 = pivots[-3:]
            if last3[0]["type"] == "low" and last3[2]["type"] == "low":
                if current_price > last3[1]["price"]:
                    result.update({
                        "type": "impulse", "wave_number": 3,
                        "direction": "bullish", "confidence": 30,
                        "phase": "可能处于推动浪中",
                        "description": "价格突破前高，可能正在展开第3浪",
                    })
                else:
                    result.update({
                        "type": "impulse", "wave_number": 2,
                        "direction": "bullish", "confidence": 25,
                        "phase": "可能处于回调中",
                        "description": "价格在前高前低之间，可能处于第2浪回调",
                    })
        return result

    # 基于最近完成的浪型推断当前位置
    if latest_type == "impulse":
        imp_dir = latest_pattern["direction"]
        prices_list = latest_pattern.get("prices", [])
        if len(prices_list) < 6:
            return result
        end_price = prices_list[5]

        if imp_dir == "bullish":
            if current_price < end_price:
                # 5浪完成后回落 → 可能进入修正A浪
                result.update({
                    "type": "corrective", "wave_number": 1,  # A浪
                    "direction": "bearish", "confidence": 55,
                    "phase": "修正A浪",
                    "description": "推动5浪完成后进入修正，可能处于A浪下跌",
                })
            else:
                # 价格仍在5浪高点之上 → 可能是延伸浪
                result.update({
                    "type": "impulse", "wave_number": 5,
                    "direction": "bullish", "confidence": 40,
                    "phase": "第5浪延伸或新推动",
                    "description": "价格仍高于5浪顶，可能是延伸浪",
                })
        else:  # bearish impulse
            if current_price > end_price:
                result.update({
                    "type": "corrective", "wave_number": 1,
                    "direction": "bullish", "confidence": 55,
                    "phase": "修正A浪(反弹)",
                    "description": "下跌5浪完成后反弹，可能处于修正A浪",
                })
            else:
                result.update({
                    "type": "impulse", "wave_number": 5,
                    "direction": "bearish", "confidence": 40,
                    "phase": "第5浪延伸(下跌)",
                    "description": "下跌5浪可能在延伸",
                })

    elif latest_type == "correction":
        corr_dir = latest_pattern["direction"]
        prices_list = latest_pattern.get("prices", [])
        if len(prices_list) < 4:
            return result
        end_price = prices_list[3]

        if "bearish" in corr_dir:
            # 看跌修正完成 → 可能新推动浪起步
            if current_price > end_price:
                result.update({
                    "type": "impulse", "wave_number": 1,
                    "direction": "bullish", "confidence": 50,
                    "phase": "新推动第1浪",
                    "description": "修正完成后反弹，可能是新推动浪第1浪",
                })
            else:
                result.update({
                    "type": "corrective", "wave_number": 3,  # C浪延伸
                    "direction": "bearish", "confidence": 45,
                    "phase": "修正C浪延伸",
                    "description": "修正可能仍在继续",
                })
        else:
            if current_price < end_price:
                result.update({
                    "type": "impulse", "wave_number": 1,
                    "direction": "bearish", "confidence": 50,
                    "phase": "新下跌第1浪",
                    "description": "反弹修正完成后下跌，可能是新下跌浪第1浪",
                })
            else:
                result.update({
                    "type": "corrective", "wave_number": 3,
                    "direction": "bullish", "confidence": 45,
                    "phase": "修正C浪延伸(反弹)",
                    "description": "反弹修正可能仍在继续",
                })

    return result


# ── 目标价预测 ──────────────────────────────────────────────────────

def _project_targets(current_wave: Dict, impulses: List[Dict],
                     corrections: List[Dict]) -> List[Dict]:
    """基于当前浪位和Fibonacci计算预测目标"""
    targets = []

    if not impulses and not corrections:
        return targets

    cw_type = current_wave.get("type", "unknown")
    cw_num = current_wave.get("wave_number", 0)
    cw_dir = current_wave.get("direction", "neutral")

    if cw_type == "impulse" and impulses:
        imp = impulses[-1]
        prices = imp["prices"]
        w1_len = abs(prices[1] - prices[0])

        if cw_num <= 3 and imp["direction"] == "bullish":
            # 预测W3目标
            w3_base = prices[2] if len(prices) > 2 else prices[0]
            for ratio, label in [(1.618, "W3目标(161.8%)"), (2.0, "W3目标(200%)")]:
                targets.append({
                    "price": round(w3_base + w1_len * ratio, 2),
                    "label": label,
                    "ratio": f"{ratio*100:.1f}%",
                    "direction": "up",
                })

        if cw_num >= 4 and imp["direction"] == "bullish":
            # 预测W5目标
            w5_base = prices[4] if len(prices) > 4 else prices[2]
            for ratio, label in [(1.0, "W5目标(=W1)"), (0.618, "W5目标(61.8%)")]:
                targets.append({
                    "price": round(w5_base + w1_len * ratio, 2),
                    "label": label,
                    "ratio": f"{ratio*100:.1f}%",
                    "direction": "up",
                })

    if cw_type == "corrective" and corrections:
        corr = corrections[-1]
        prices = corr["prices"]
        a_len = abs(prices[0] - prices[1])

        if "bearish" in corr["direction"]:
            # C浪目标
            c_base = prices[2]
            for ratio, label in [(1.0, "C浪目标(=A)"), (1.618, "C浪目标(161.8%)")]:
                targets.append({
                    "price": round(c_base - a_len * ratio, 2),
                    "label": label,
                    "ratio": f"{ratio*100:.1f}%",
                    "direction": "down",
                })

    return targets[:4]  # 最多4个目标


# ── 构建浪标签 ──────────────────────────────────────────────────────

def _build_wave_labels(impulses: List[Dict], corrections: List[Dict]) -> List[Dict]:
    """构建图表标注用的浪标签列表"""
    labels = []

    for imp in impulses:
        pts = imp["pivots"]
        direction = imp["direction"]
        wave_names = ["0", "1", "2", "3", "4", "5"]
        for idx, pt in enumerate(pts):
            if idx == 0:
                continue  # 跳过W0起点
            labels.append({
                "index": pt["index"],
                "date": pt["date"],
                "price": pt["price"],
                "label": wave_names[idx],
                "wave_type": f"impulse_{direction}",
                "pivot_type": pt["type"],
            })

    for corr in corrections:
        pts = corr["pivots"]
        direction = corr["direction"]
        wave_names = ["", "A", "B", "C"]
        for idx, pt in enumerate(pts):
            if idx == 0:
                continue
            labels.append({
                "index": pt["index"],
                "date": pt["date"],
                "price": pt["price"],
                "label": wave_names[idx],
                "wave_type": f"corrective_{direction}",
                "pivot_type": pt["type"],
            })

    return labels


# ── 单周期主入口 ────────────────────────────────────────────────────

def detect_elliott_waves(df: pd.DataFrame, window: int = 10) -> Dict:
    """
    单周期波浪检测主入口

    Args:
        df: OHLCV DataFrame
        window: 枢轴检测窗口

    Returns:
        impulse_waves, corrective_waves, current_wave, projected_targets, wave_labels
    """
    empty_result = {
        "impulse_waves": [],
        "corrective_waves": [],
        "current_wave": {
            "type": "unknown", "wave_number": 0, "direction": "neutral",
            "confidence": 0, "phase": "数据不足", "description": "数据不足以进行波浪分析",
        },
        "projected_targets": [],
        "wave_labels": [],
    }

    if df is None or df.empty or len(df) < 30:
        return empty_result

    from .signal_generator import detect_swing_highs_lows

    # 多窗口扫描，取最佳结果
    best_result = None
    best_score = -1

    for w in [5, 8, 10, 13]:
        if len(df) < w * 2 + 6:
            continue

        swings = detect_swing_highs_lows(df, window=w)
        zigzag = _build_zigzag(swings["swing_highs"], swings["swing_lows"])

        if len(zigzag) < 4:
            continue

        # 检测推动浪（看涨+看跌）
        bull_candidates = _find_impulse_candidates(zigzag, "bullish")
        bear_candidates = _find_impulse_candidates(zigzag, "bearish")

        valid_impulses = []
        for cand in bull_candidates + bear_candidates:
            is_valid, violations = _validate_impulse_rules(cand)
            if is_valid:
                fib_score = _score_fibonacci_alignment(cand)
                cand["fib_score"] = fib_score
                cand["is_valid"] = True
                valid_impulses.append(cand)

        # 按Fibonacci评分排序，保留最佳
        valid_impulses.sort(key=lambda x: x["fib_score"], reverse=True)
        valid_impulses = valid_impulses[:3]  # 保留Top 3

        # 检测修正浪
        corrective = _find_corrective_candidates(zigzag)
        corrective.sort(key=lambda x: x["fib_score"], reverse=True)
        corrective = corrective[:3]

        # 评估总分
        total_score = sum(imp["fib_score"] for imp in valid_impulses) + \
                      sum(c["fib_score"] for c in corrective)

        if total_score > best_score:
            best_score = total_score
            current_price = float(df["Close"].iloc[-1])

            current_wave = _determine_current_wave(
                valid_impulses, corrective, zigzag, current_price, len(df)
            )
            targets = _project_targets(current_wave, valid_impulses, corrective)
            wave_labels = _build_wave_labels(valid_impulses, corrective)

            best_result = {
                "impulse_waves": valid_impulses,
                "corrective_waves": corrective,
                "current_wave": current_wave,
                "projected_targets": targets,
                "wave_labels": wave_labels,
            }

    return best_result if best_result else empty_result


# ── 多周期综合分析 ──────────────────────────────────────────────────

def detect_multi_timeframe_waves(
    df_daily: pd.DataFrame,
    df_weekly: pd.DataFrame = None,
    df_60m: pd.DataFrame = None,
    df_4h: pd.DataFrame = None,
) -> Dict:
    """
    多周期波浪综合分析

    Args:
        df_daily: 日线数据
        df_weekly: 周线数据（或从日线resample）
        df_60m: 60分钟数据（用于1h分析）
        df_4h: 4小时数据（或从60m resample）

    Returns:
        timeframes, alignment, synthesis
    """
    tf_results = {}

    # 日线
    if df_daily is not None and len(df_daily) >= 30:
        tf_results["日线"] = detect_elliott_waves(df_daily, window=10)

    # 周线
    if df_weekly is not None and len(df_weekly) >= 30:
        tf_results["周线"] = detect_elliott_waves(df_weekly, window=8)
    elif df_daily is not None and len(df_daily) >= 200:
        try:
            weekly = df_daily.resample("W").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna()
            if len(weekly) >= 30:
                tf_results["周线"] = detect_elliott_waves(weekly, window=8)
        except Exception:
            pass

    # 1小时
    if df_60m is not None and len(df_60m) >= 30:
        tf_results["1小时"] = detect_elliott_waves(df_60m, window=8)

    # 4小时
    if df_4h is not None and len(df_4h) >= 30:
        tf_results["4小时"] = detect_elliott_waves(df_4h, window=8)
    elif df_60m is not None and len(df_60m) >= 120:
        try:
            h4 = df_60m.resample("4h").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna()
            if len(h4) >= 30:
                tf_results["4小时"] = detect_elliott_waves(h4, window=8)
        except Exception:
            pass

    synthesis = _synthesize_multi_timeframe(tf_results)

    return {
        "timeframes": tf_results,
        "alignment": synthesis.get("alignment", "数据不足"),
        "synthesis": synthesis,
    }


def _synthesize_multi_timeframe(tf_results: Dict) -> Dict:
    """
    多周期综合研判

    权重: 周线0.35, 日线0.30, 4h0.20, 1h0.15
    每个周期的浪位→方向偏向分数，加权求和→操作建议
    """
    if not tf_results:
        return {
            "alignment": "数据不足",
            "bias": "中性",
            "confidence": 0,
            "reasoning": ["无足够数据进行多周期分析"],
            "suggested_action": "观望",
            "entry_timing": "等待更多数据",
        }

    weighted_bias = 0.0
    total_weight = 0.0
    reasoning = []
    tf_details = {}

    for tf_name, weight in TF_WEIGHTS.items():
        if tf_name not in tf_results:
            continue

        cw = tf_results[tf_name].get("current_wave", {})
        wave_type = cw.get("type", "unknown")
        wave_num = cw.get("wave_number", 0)
        direction = cw.get("direction", "neutral")
        confidence = cw.get("confidence", 0)
        phase = cw.get("phase", "未识别")

        # 构建浪位key
        if wave_type == "impulse":
            bias_key = f"impulse_W{wave_num}"
        elif wave_type == "corrective":
            wave_letter = {1: "A", 2: "B", 3: "C"}.get(wave_num, "A")
            bias_key = f"corrective_{wave_letter}"
        else:
            bias_key = None

        bias = WAVE_BIAS.get(bias_key, 0)

        # 方向调整
        if direction == "bearish" and wave_type == "impulse":
            bias = -bias

        # 置信度衰减
        bias *= (confidence / 100.0)

        weighted_bias += bias * weight
        total_weight += weight

        tf_details[tf_name] = {
            "wave": f"{'推动' if wave_type == 'impulse' else '修正'}W{wave_num}",
            "direction": direction,
            "confidence": confidence,
            "phase": phase,
        }

        if confidence > 30:
            reasoning.append(f"{tf_name}: {phase} (置信度{confidence}%)")

    if total_weight > 0:
        weighted_bias /= total_weight

    # 确定共振/分歧
    directions = [d.get("direction") for d in tf_details.values() if d.get("confidence", 0) > 30]
    bullish_count = sum(1 for d in directions if d == "bullish")
    bearish_count = sum(1 for d in directions if d == "bearish")

    if len(directions) >= 2:
        if bullish_count == len(directions):
            alignment = "强共振多"
        elif bearish_count == len(directions):
            alignment = "强共振空"
        elif bullish_count > bearish_count:
            alignment = "部分共振(偏多)"
        elif bearish_count > bullish_count:
            alignment = "部分共振(偏空)"
        else:
            alignment = "分歧"
    else:
        alignment = "数据不足"

    # 操作建议
    if weighted_bias > 1.0:
        action = "强买"
        bias_label = "看多"
    elif weighted_bias > 0.3:
        action = "弱买"
        bias_label = "看多"
    elif weighted_bias < -1.0:
        action = "强卖"
        bias_label = "看空"
    elif weighted_bias < -0.3:
        action = "弱卖"
        bias_label = "看空"
    else:
        action = "观望"
        bias_label = "中性"

    # 进场时机
    entry_timing = _determine_entry_timing(tf_details, action)

    overall_conf = min(95, int(abs(weighted_bias) * 40 + len(directions) * 10))

    return {
        "alignment": alignment,
        "bias": bias_label,
        "confidence": overall_conf,
        "reasoning": reasoning if reasoning else ["各周期波浪信号较弱"],
        "suggested_action": action,
        "entry_timing": entry_timing,
        "weighted_bias": round(weighted_bias, 2),
        "tf_details": tf_details,
    }


def _determine_entry_timing(tf_details: Dict, action: str) -> str:
    """基于低周期浪位的进场时机建议"""
    h1 = tf_details.get("1小时", {})
    h4 = tf_details.get("4小时", {})
    daily = tf_details.get("日线", {})

    if action in ("强买", "弱买"):
        if h1.get("phase") and "回调" in h1["phase"]:
            return "1小时回调到位后入场"
        if h4.get("phase") and "修正" in h4["phase"]:
            return "等待4小时修正完成后入场"
        if h1.get("phase") and "第1浪" in h1["phase"]:
            return "1小时新推动确认后追入"
        return "等待低周期回调确认后入场"

    if action in ("强卖", "弱卖"):
        if h1.get("phase") and "反弹" in h1["phase"]:
            return "1小时反弹到压力位后减仓"
        return "等待低周期反弹减仓"

    return "观望等待明确信号"
