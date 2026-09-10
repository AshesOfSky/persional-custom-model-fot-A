"""
elliott_wave.py — Elliott波浪理论检测引擎
多周期(1h/4h/日线/周线)波浪识别 + Fibonacci评分 + 综合研判
"""

import hashlib
import json
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


def _restore_swing_timestamps(swings: Dict, index: pd.Index) -> None:
    """按摆动点位置恢复时间精度；小时线不能被压缩成同一天。"""
    try:
        datetime_index = pd.DatetimeIndex(index)
        preserve_time = bool(
            (datetime_index != datetime_index.normalize()).any()
            or datetime_index.tz is not None
        )
    except (TypeError, ValueError):
        preserve_time = False

    def _format(position) -> Optional[str]:
        try:
            position = int(position)
        except (OverflowError, TypeError, ValueError):
            return None
        if position < 0 or position >= len(index):
            return None
        value = index[position]
        if preserve_time and hasattr(value, "isoformat"):
            return value.isoformat()
        if hasattr(value, "date"):
            return str(value.date())
        return str(value)

    for kind in ("swing_highs", "swing_lows"):
        for point in swings.get(kind, []):
            pivot_time = _format(point.get("index"))
            confirmed_time = _format(point.get("confirmed_index"))
            if pivot_time is not None:
                point["date"] = pivot_time
            if confirmed_time is not None:
                point["confirmed_date"] = confirmed_time


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
    violations = []
    try:
        prices = [float(price) for price in candidate["prices"]]
    except (KeyError, TypeError, ValueError):
        return False, ["浪位价格格式无效"]
    if len(prices) < 6 or not all(np.isfinite(price) for price in prices[:6]):
        return False, ["浪位价格包含缺失或非有限值"]
    direction = candidate.get("direction")

    if direction == "bullish":
        w1_len = prices[1] - prices[0]  # W1上涨幅度
        w2_retrace = prices[1] - prices[2]  # W2回撤
        w3_len = prices[3] - prices[2]  # W3上涨
        w4_retrace = prices[3] - prices[4]  # W4回撤
        w5_len = prices[5] - prices[4]  # W5上涨

        if w1_len <= 0:
            violations.append("W1未向上运行")
        if w2_retrace <= 0:
            violations.append("W2未形成向下回撤")
        if w3_len <= 0:
            violations.append("W3未向上运行")
        if w4_retrace <= 0:
            violations.append("W4未形成向下回撤")
        if w5_len <= 0:
            violations.append("W5未向上运行")

        # 规则1: W2回撤不超过W1的100%
        if w2_retrace >= w1_len:
            violations.append("W2回撤超过W1的100%")

        # 规则2: W3不是最短推动浪
        if w3_len <= w1_len and w3_len <= w5_len:
            violations.append("W3是最短推动浪")

        # 规则3: W4不进入W1区域 (W4低点 > W1高点)
        if prices[4] < prices[1]:
            violations.append("W4进入W1区域")

    elif direction == "bearish":
        w1_len = prices[0] - prices[1]
        w2_retrace = prices[2] - prices[1]
        w3_len = prices[2] - prices[3]
        w4_retrace = prices[4] - prices[3]
        w5_len = prices[4] - prices[5]

        if w1_len <= 0:
            violations.append("W1未向下运行")
        if w2_retrace <= 0:
            violations.append("W2未形成向上反弹")
        if w3_len <= 0:
            violations.append("W3未向下运行")
        if w4_retrace <= 0:
            violations.append("W4未形成向上反弹")
        if w5_len <= 0:
            violations.append("W5未向下运行")

        if w2_retrace >= w1_len:
            violations.append("W2回撤超过W1的100%")
        if w3_len <= w1_len and w3_len <= w5_len:
            violations.append("W3是最短推动浪")
        if prices[4] > prices[1]:
            violations.append("W4进入W1区域")
    else:
        violations.append("推动浪方向无效")

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
    if not ideals or not np.isfinite(actual):
        return 0
    valid_ideals = [float(ideal) for ideal in ideals if np.isfinite(ideal)]
    if not valid_ideals:
        return 0
    best = min(abs(actual - ideal) for ideal in valid_ideals)

    # 容差内视为完全匹配；超出后在 3×容差处连续、单调地衰减到 0。
    # 旧版在 3×容差边界会从 0.5 跳升到约 0.76，导致“偏得更远反而得分更高”。
    if best <= TOLERANCE:
        return 1.0
    fade_span = TOLERANCE * 2
    if fade_span <= 0:
        return 0
    return max(0.0, 1.0 - (best - TOLERANCE) / fade_span)


def _keep_latest_candidates(candidates: List[Dict], limit: int = 3) -> List[Dict]:
    """按结束位置保留最近候选；同一结束位置再以 Fibonacci 分数择优。"""
    if limit <= 0:
        return []
    return sorted(
        candidates,
        key=lambda x: (x.get("end_index", -1), x.get("fib_score", 0)),
        reverse=True,
    )[:limit]


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

    scan_positions = range(len(pivots) - 3)
    parent_direction = None
    if after_impulse is not None:
        parent_end = after_impulse.get("end_index")
        parent_pivots = after_impulse.get("pivots") or []
        parent_last = parent_pivots[-1] if parent_pivots else {}

        def _is_parent_end(point: Dict) -> bool:
            if point.get("index") != parent_end:
                return False
            if parent_last.get("type") and point.get("type") != parent_last.get("type"):
                return False
            if parent_last.get("price") is not None and not np.isclose(
                float(point.get("price")),
                float(parent_last.get("price")),
            ):
                return False
            return True

        anchor_pos = next(
            (i for i, point in enumerate(pivots) if _is_parent_end(point)),
            None,
        )
        # ABC 的 0 点必须与父推动浪 W5 完全相接；无法找到锚点则不猜测。
        if anchor_pos is None or anchor_pos + 3 >= len(pivots):
            return candidates
        scan_positions = [anchor_pos]
        parent_direction = after_impulse.get("direction")

    for i in scan_positions:
        pts = pivots[i:i + 4]
        # 看跌修正 (从高开始): high→low→high→low
        if pts[0]["type"] == "high" and pts[1]["type"] == "low" and \
           pts[2]["type"] == "high" and pts[3]["type"] == "low" and \
           parent_direction != "bearish":
            a_len = pts[0]["price"] - pts[1]["price"]
            if a_len <= 0:
                continue
            b_retrace = (pts[2]["price"] - pts[1]["price"]) / a_len
            c_len = pts[2]["price"] - pts[3]["price"]
            c_ratio = c_len / a_len if a_len > 0 else 0

            b_score = _fib_match_score(b_retrace, FIB_IDEAL["wB_retrace"])
            c_score = _fib_match_score(c_ratio, FIB_IDEAL["wC_extension"])
            fib_score = (b_score * 50 + c_score * 50)

            candidate = {
                "pivots": pts,
                "prices": [pt["price"] for pt in pts],
                "direction": "bearish_correction",
                "fib_score": fib_score,
                "start_index": pts[0]["index"],
                "end_index": pts[3]["index"],
            }
            if after_impulse is not None:
                candidate.update({
                    "parent_impulse_start_index": after_impulse.get("start_index"),
                    "parent_impulse_end_index": after_impulse.get("end_index"),
                })
            candidates.append(candidate)

        # 看涨修正 (从低开始): low→high→low→high
        if pts[0]["type"] == "low" and pts[1]["type"] == "high" and \
           pts[2]["type"] == "low" and pts[3]["type"] == "high" and \
           parent_direction != "bullish":
            a_len = pts[1]["price"] - pts[0]["price"]
            if a_len <= 0:
                continue
            b_retrace = (pts[1]["price"] - pts[2]["price"]) / a_len
            c_len = pts[3]["price"] - pts[2]["price"]
            c_ratio = c_len / a_len if a_len > 0 else 0

            b_score = _fib_match_score(b_retrace, FIB_IDEAL["wB_retrace"])
            c_score = _fib_match_score(c_ratio, FIB_IDEAL["wC_extension"])
            fib_score = (b_score * 50 + c_score * 50)

            candidate = {
                "pivots": pts,
                "prices": [pt["price"] for pt in pts],
                "direction": "bullish_correction",
                "fib_score": fib_score,
                "start_index": pts[0]["index"],
                "end_index": pts[3]["index"],
            }
            if after_impulse is not None:
                candidate.update({
                    "parent_impulse_start_index": after_impulse.get("start_index"),
                    "parent_impulse_end_index": after_impulse.get("end_index"),
                })
            candidates.append(candidate)

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

    # 候选的存储顺序属于输出细节，当前位置必须显式按结束位置选最近结构。
    recent_impulse = max(
        impulses, key=lambda x: x.get("end_index", -1), default=None
    )
    recent_correction = max(
        corrections, key=lambda x: x.get("end_index", -1), default=None
    )

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

    # 透传当前浪的来源，使目标投射、支撑压力和调用方可绑定到同一浪型，
    # 避免误拿另一组“分数漂亮但无关”的历史结构。
    result.update({
        "source_pattern_type": latest_type,
        "source_pattern_direction": latest_pattern.get("direction"),
        "source_start_index": latest_pattern.get("start_index"),
        "source_end_index": latest_pattern.get("end_index"),
    })
    source_pivots = latest_pattern.get("pivots") or []
    if source_pivots:
        result["source_confirmed_index"] = source_pivots[-1].get("confirmed_index")
        result["source_confirmed_date"] = source_pivots[-1].get("confirmed_date")

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
    """基于当前浪位和其来源浪型计算 Fibonacci 目标。

    当前浪必须带 ``source_pattern_type/source_start_index/source_end_index``。
    起止位置任一缺失或找不到精确来源时宁可返回空目标，也不引用另一组历史浪型。
    """
    targets = []

    if not impulses and not corrections:
        return targets

    cw_type = current_wave.get("type", "unknown")
    cw_num = current_wave.get("wave_number", 0)
    source_type = current_wave.get("source_pattern_type")
    source_direction = current_wave.get("source_pattern_direction")
    source_end = current_wave.get("source_end_index")
    source_start = current_wave.get("source_start_index")

    def _source_of(patterns: List[Dict], expected_type: str) -> Optional[Dict]:
        if (
            source_type != expected_type
            or source_start is None
            or source_end is None
        ):
            return None
        matches = [p for p in patterns if p.get("end_index") == source_end]
        exact = [p for p in matches if p.get("start_index") == source_start]
        if source_direction is not None:
            exact = [
                p for p in exact
                if p.get("direction") == source_direction
            ]
        if not exact:
            return None
        matches = exact
        return max(matches, key=lambda x: x.get("fib_score", 0), default=None)

    def _append(price: float, label: str, ratio: float, direction: str) -> None:
        if np.isfinite(price) and price > 0:
            targets.append({
                "price": round(float(price), 2),
                "label": label,
                "ratio": f"{ratio * 100:.1f}%",
                "direction": direction,
            })

    if cw_type == "impulse":
        imp = _source_of(impulses, "impulse")
        if not imp:
            return targets
        prices = imp["prices"]
        if len(prices) < 6:
            return targets
        w1_len = abs(prices[1] - prices[0])
        if not np.isfinite(w1_len) or w1_len <= 0:
            return targets
        is_bullish = imp.get("direction") == "bullish"
        is_bearish = imp.get("direction") == "bearish"
        if not (is_bullish or is_bearish):
            return targets
        sign = 1.0 if is_bullish else -1.0
        target_direction = "up" if is_bullish else "down"

        if cw_num <= 3:
            # 预测W3目标
            w3_base = prices[2]
            for ratio, label in [(1.618, "W3目标(161.8%)"), (2.0, "W3目标(200%)")]:
                _append(
                    w3_base + sign * w1_len * ratio,
                    label,
                    ratio,
                    target_direction,
                )

        if cw_num >= 4:
            # 预测W5目标
            w5_base = prices[4]
            for ratio, label in [(1.0, "W5目标(=W1)"), (0.618, "W5目标(61.8%)")]:
                _append(
                    w5_base + sign * w1_len * ratio,
                    label,
                    ratio,
                    target_direction,
                )

    if cw_type == "corrective":
        corr = _source_of(corrections, "correction")
        if not corr:
            return targets
        prices = corr["prices"]
        if len(prices) < 4:
            return targets
        a_len = abs(prices[0] - prices[1])
        if not np.isfinite(a_len) or a_len <= 0:
            return targets
        corr_direction = str(corr.get("direction") or "")
        if "bearish" in corr_direction:
            sign, target_direction = -1.0, "down"
        elif "bullish" in corr_direction:
            sign, target_direction = 1.0, "up"
        else:
            return targets
        c_base = prices[2]
        for ratio, label in [(1.0, "C浪目标(=A)"), (1.618, "C浪目标(161.8%)")]:
            _append(
                c_base + sign * a_len * ratio,
                label,
                ratio,
                target_direction,
            )

    return targets[:4]  # 最多4个目标


# ── 构建浪标签 ──────────────────────────────────────────────────────

def _reliability_id(namespace: str, payload: Dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"elliott-{namespace}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _reliability_time(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _reliability_point(point: Dict, label: str) -> Dict:
    return {
        "index": int(point["index"]),
        "time": _reliability_time(point.get("date")),
        "price": round(float(point["price"]), 6),
        "label": label,
        "pivotType": str(point["type"]),
        "confirmedIndex": (
            int(point["confirmed_index"])
            if point.get("confirmed_index") is not None else None
        ),
        "confirmedTime": _reliability_time(point.get("confirmed_date")),
    }


def _confirmed_impulse_view(
    candidate: Dict,
    *,
    level: int = 0,
    parent_structure_id: Optional[str] = None,
    parent_leg: Optional[int] = None,
) -> Optional[Dict]:
    points = candidate.get("pivots") or []
    if len(points) != 6 or not points[-1].get("confirmed_date"):
        return None
    point_views = [
        _reliability_point(point, str(index))
        for index, point in enumerate(points)
    ]
    kind = "impulse" if level == 0 else "nested_impulse"
    identity = {
        "kind": kind,
        "level": int(level),
        "parentStructureId": parent_structure_id,
        "parentLeg": parent_leg,
        "direction": candidate["direction"],
        "points": point_views,
        "confirmedTime": point_views[-1]["confirmedTime"],
        "methodVersion": "elliott-canonical-reliability-1",
    }
    return {
        **identity,
        "structureId": _reliability_id(kind, identity),
        "startTime": point_views[0]["time"],
        "endTime": point_views[-1]["time"],
        "startIndex": point_views[0]["index"],
        "endIndex": point_views[-1]["index"],
        "startPrice": point_views[0]["price"],
        "endPrice": point_views[-1]["price"],
        "fibScore": round(float(candidate.get("fib_score", 0.0)), 3),
        "ruleMode": "strict",
        "violations": [],
        "nestedDirection": "consistent" if level > 0 else None,
        "validationBasis": (
            "strict_child_impulse_parent_leg_direction_and_confirmed_pivot"
            if level > 0
            else "three_strict_impulse_rules_and_confirmed_endpoint_pivot"
        ),
        "validated": True,
        "confirmed": True,
        "status": "confirmed",
        "scoreEligible": False,
        "alertEligible": False,
    }


def _correction_moves(points: List[Dict]) -> List[float]:
    return [
        float(right["price"]) - float(left["price"])
        for left, right in zip(points, points[1:])
    ]


def _is_contracting_triangle(points: List[Dict], parent_direction: str) -> bool:
    if len(points) != 6:
        return False
    moves = _correction_moves(points)
    expected_first = -1.0 if parent_direction == "bullish" else 1.0
    if moves[0] * expected_first <= 0:
        return False
    if any(left * right >= 0 for left, right in zip(moves, moves[1:])):
        return False
    amplitudes = [abs(move) for move in moves]
    return (
        all(nxt <= current * 1.15 for current, nxt in zip(amplitudes, amplitudes[1:]))
        and amplitudes[-1] <= amplitudes[0] * 0.75
    )


def _is_double_three(points: List[Dict], parent_direction: str) -> bool:
    if len(points) != 8:
        return False
    moves = _correction_moves(points)
    expected_first = -1.0 if parent_direction == "bullish" else 1.0
    signs = [expected_first * (-1.0 if index % 2 else 1.0) for index in range(7)]
    if any(move * sign <= 0 for move, sign in zip(moves, signs)):
        return False
    origin = float(points[0]["price"])
    first_c = float(points[3]["price"])
    x_price = float(points[4]["price"])
    second_c = float(points[7]["price"])
    if parent_direction == "bullish":
        return first_c < x_price < origin and second_c < x_price
    return origin < x_price < first_c and second_c > x_price


def _complex_correction_views(pivots: List[Dict], roots: List[tuple]) -> List[Dict]:
    structures = []
    labels = {
        "triangle": ["0", "A", "B", "C", "D", "E"],
        "double_three": ["0", "A", "B", "C", "X", "A2", "B2", "C2"],
    }
    for candidate, root_view in roots:
        root_points = candidate.get("pivots") or []
        if not root_points:
            continue
        endpoint = root_points[-1]
        anchor = next(
            (
                index for index, point in enumerate(pivots)
                if point.get("index") == endpoint.get("index")
                and point.get("type") == endpoint.get("type")
                and np.isclose(float(point.get("price")), float(endpoint.get("price")))
            ),
            None,
        )
        if anchor is None:
            continue
        follow = pivots[anchor + 1:]
        pattern = None
        correction_points = []
        if len(follow) >= 7:
            possible = [endpoint, *follow[:7]]
            if follow[6].get("confirmed_date") and _is_double_three(
                possible, candidate["direction"]
            ):
                pattern = "double_three"
                correction_points = possible
        if pattern is None and len(follow) >= 5:
            possible = [endpoint, *follow[:5]]
            if follow[4].get("confirmed_date") and _is_contracting_triangle(
                possible, candidate["direction"]
            ):
                pattern = "triangle"
                correction_points = possible
        if pattern is None:
            continue
        point_views = [
            _reliability_point(point, label)
            for point, label in zip(correction_points, labels[pattern])
        ]
        direction = "bearish" if candidate["direction"] == "bullish" else "bullish"
        identity = {
            "kind": "complex_correction",
            "pattern": pattern,
            "level": 0,
            "parentStructureId": root_view["structureId"],
            "direction": direction,
            "points": point_views,
            "confirmedTime": point_views[-1]["confirmedTime"],
            "methodVersion": "elliott-canonical-reliability-1",
        }
        structures.append({
            **identity,
            "structureId": _reliability_id(pattern, identity),
            "startTime": point_views[0]["time"],
            "endTime": point_views[-1]["time"],
            "startIndex": point_views[0]["index"],
            "endIndex": point_views[-1]["index"],
            "startPrice": point_views[0]["price"],
            "endPrice": point_views[-1]["price"],
            "validationBasis": (
                "confirmed_contracting_five_leg_correction"
                if pattern == "triangle"
                else "confirmed_two_abc_groups_with_x_connector"
            ),
            "validated": True,
            "confirmed": True,
            "status": "confirmed",
            "scoreEligible": False,
            "alertEligible": False,
        })
    return structures


def _scan_strict_impulses(
    frame: pd.DataFrame,
    windows: List[int],
    *,
    index_offset: int = 0,
) -> List[Dict]:
    from .signal_generator import detect_swing_highs_lows

    candidates = []
    seen = set()
    for window in windows:
        if len(frame) < window * 2 + 6:
            continue
        swings = detect_swing_highs_lows(frame, window=window)
        _restore_swing_timestamps(swings, frame.index)
        pivots = _build_zigzag(swings["swing_highs"], swings["swing_lows"])
        if index_offset:
            adjusted = []
            for point in pivots:
                copy = dict(point)
                copy["index"] = int(copy["index"]) + index_offset
                if copy.get("confirmed_index") is not None:
                    copy["confirmed_index"] = int(copy["confirmed_index"]) + index_offset
                adjusted.append(copy)
            pivots = adjusted
        for candidate in (
            _find_impulse_candidates(pivots, "bullish")
            + _find_impulse_candidates(pivots, "bearish")
        ):
            valid, violations = _validate_impulse_rules(candidate)
            if not valid or violations:
                continue
            candidate["fib_score"] = _score_fibonacci_alignment(candidate)
            key = (
                candidate["direction"],
                tuple(
                    (point.get("date"), round(float(point["price"]), 8))
                    for point in candidate["pivots"]
                ),
            )
            if key not in seen:
                seen.add(key)
                candidates.append(candidate)
    return candidates


def _nested_impulse_views(
    frame: pd.DataFrame,
    parent_candidate: Dict,
    parent_structure_id: str,
    *,
    level: int = 1,
    max_level: int = 3,
) -> Tuple[List[Dict], int]:
    if level > max_level:
        return [], 0
    structures = []
    rejected = 0
    points = parent_candidate.get("pivots") or []
    for leg_number, (left, right) in enumerate(zip(points, points[1:]), start=1):
        start = int(left["index"])
        end = int(right["index"])
        if end - start < 12 or start < 0 or end >= len(frame):
            continue
        leg_frame = frame.iloc[start:end + 1]
        expected = (
            "bullish" if float(right["price"]) > float(left["price"])
            else "bearish"
        )
        for child in _scan_strict_impulses(
            leg_frame, [1, 2, 3], index_offset=start
        ):
            if child["direction"] != expected:
                rejected += 1
                continue
            view = _confirmed_impulse_view(
                child,
                level=level,
                parent_structure_id=parent_structure_id,
                parent_leg=leg_number,
            )
            if view is None:
                continue
            structures.append(view)
            descendants, descendant_rejected = _nested_impulse_views(
                frame,
                child,
                view["structureId"],
                level=level + 1,
                max_level=max_level,
            )
            structures.extend(descendants)
            rejected += descendant_rejected
    unique = {item["structureId"]: item for item in structures}
    return list(unique.values()), rejected


def _frame_reliability_hash(frame: pd.DataFrame) -> str:
    columns = [
        name for name in ("Open", "High", "Low", "Close", "Volume")
        if name in frame.columns
    ]
    rows = []
    for index, values in zip(frame.index, frame[columns].to_numpy()):
        rows.append([
            _reliability_time(index),
            *[
                round(float(value), 8) if np.isfinite(float(value)) else None
                for value in values
            ],
        ])
    return _reliability_id("input", {"columns": columns, "rows": rows})


def _empty_reliability(frame: Optional[pd.DataFrame] = None) -> Dict:
    has_frame = isinstance(frame, pd.DataFrame) and not frame.empty
    return {
        "version": "elliott-reliability-1",
        "source": "modules.elliott_wave",
        "methodVersion": "elliott-canonical-reliability-1",
        "originTime": _reliability_time(frame.index[0]) if has_frame else None,
        "lastInputTime": _reliability_time(frame.index[-1]) if has_frame else None,
        "inputHash": _frame_reliability_hash(frame) if has_frame else None,
        "prefixPolicy": "append_only_confirmed_ids_while_origin_and_method_match",
        "detectionWindows": [],
        "confirmedStructures": [],
        "provisionalStructures": [],
        "levels": [],
        "nestedDirection": {
            "acceptedCount": 0,
            "rejectedMismatchCount": 0,
            "policy": "child_direction_must_match_parent_leg",
        },
    }


def _build_canonical_reliability(
    frame: pd.DataFrame,
    pivots: List[Dict],
    impulses: List[Dict],
    *,
    detection_window: int,
) -> Dict:
    result = _empty_reliability(frame)
    result["detectionWindows"] = [int(detection_window)]
    roots = []
    structures = []
    rejected = 0
    for candidate in impulses:
        view = _confirmed_impulse_view(candidate)
        if view is None:
            continue
        roots.append((candidate, view))
        structures.append(view)
        nested, nested_rejected = _nested_impulse_views(
            frame, candidate, view["structureId"]
        )
        structures.extend(nested)
        rejected += nested_rejected
    structures.extend(_complex_correction_views(pivots, roots))
    result["confirmedStructures"] = sorted(
        {item["structureId"]: item for item in structures}.values(),
        key=lambda item: (
            str(item.get("confirmedTime") or ""),
            int(item.get("level", 0)),
            item["structureId"],
        ),
    )
    result["nestedDirection"] = {
        "acceptedCount": sum(
            1 for item in result["confirmedStructures"]
            if item["kind"] == "nested_impulse"
        ),
        "rejectedMismatchCount": rejected,
        "policy": "child_direction_must_match_parent_leg",
    }
    return result


def _merge_reliability_streams(
    frame: pd.DataFrame,
    streams: List[Dict],
    current_wave: Dict,
) -> Dict:
    result = _empty_reliability(frame)
    structures = {}
    rejected = 0
    windows = set()
    for stream in streams:
        windows.update(stream.get("detectionWindows") or [])
        rejected += int(
            (stream.get("nestedDirection") or {}).get("rejectedMismatchCount", 0)
        )
        for item in stream.get("confirmedStructures") or []:
            structures[item["structureId"]] = item
    result["detectionWindows"] = sorted(int(value) for value in windows)
    result["confirmedStructures"] = sorted(
        structures.values(),
        key=lambda item: (
            str(item.get("confirmedTime") or ""),
            int(item.get("level", 0)),
            item["structureId"],
        ),
    )
    for level in sorted({int(item["level"]) for item in structures.values()}):
        items = [item for item in structures.values() if int(item["level"]) == level]
        result["levels"].append({
            "level": level,
            "name": "primary" if level == 0 else f"nested_{level}",
            "confirmedCount": len(items),
            "directions": sorted({item["direction"] for item in items}),
        })
    nested_count = sum(
        1 for item in structures.values() if item["kind"] == "nested_impulse"
    )
    result["nestedDirection"] = {
        "acceptedCount": nested_count,
        "rejectedMismatchCount": rejected,
        "policy": "child_direction_must_match_parent_leg",
    }
    identity = {
        "kind": "current_count",
        "type": current_wave.get("type"),
        "waveNumber": current_wave.get("wave_number"),
        "direction": current_wave.get("direction"),
        "lastInputTime": result["lastInputTime"],
        "methodVersion": "elliott-canonical-reliability-1",
    }
    result["provisionalStructures"] = [{
        **identity,
        "structureId": _reliability_id("provisional", identity),
        "validationBasis": "current_count_has_no_future_structure_confirmation",
        "validated": False,
        "confirmed": False,
        "status": "provisional",
        "scoreEligible": False,
        "alertEligible": False,
    }]
    return result


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
                "confirmed_index": pt.get("confirmed_index"),
                "confirmed_date": pt.get("confirmed_date"),
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
                "confirmed_index": pt.get("confirmed_index"),
                "confirmed_date": pt.get("confirmed_date"),
            })

    return sorted(labels, key=lambda x: (x.get("index", -1), x.get("wave_type", "")))


# ── 单周期主入口 ────────────────────────────────────────────────────

def detect_elliott_waves(df: pd.DataFrame, window: Optional[int] = None) -> Dict:
    """
    单周期波浪检测主入口

    Args:
        df: OHLCV DataFrame
        window: 指定时仅扫描该枢轴窗口；None 时自适应扫描 5/8/10/13

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
        "reliability": _empty_reliability(df if isinstance(df, pd.DataFrame) else None),
    }

    if df is None or df.empty or len(df) < 30:
        return empty_result

    # 直接调用入口时也统一为严格递增、唯一的时间轴，避免倒序或重复行情
    # 让位置索引与标签时间相互矛盾。重复时沿用行情模块的 keep-last 口径。
    try:
        df = df.sort_index(kind="stable")
        df = df.loc[~df.index.duplicated(keep="last")]
    except (AttributeError, TypeError, ValueError):
        return empty_result
    if len(df) < 30:
        return empty_result

    from .signal_generator import detect_swing_highs_lows

    # 未显式指定时保持原有自适应多窗口；传入 window 时严格尊重调用方参数。
    if window is None:
        scan_windows = [5, 8, 10, 13]
    else:
        if not isinstance(window, (int, np.integer)) or int(window) < 1:
            raise ValueError("window 必须是正整数或 None")
        scan_windows = [int(window)]

    # 跨窗口优先选择结束位置最近的有效结构，同等时效再比较 Fibonacci 总分。
    best_result = None
    best_selection_key = None
    reliability_streams = []

    for w in scan_windows:
        if len(df) < w * 2 + 6:
            continue

        swings = detect_swing_highs_lows(df, window=w)
        _restore_swing_timestamps(swings, df.index)
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

        reliability_streams.append(
            _build_canonical_reliability(
                df,
                zigzag,
                valid_impulses,
                detection_window=w,
            )
        )

        # 保留最近三组；同一结束位置再以 Fibonacci 评分择优。
        valid_impulses = _keep_latest_candidates(valid_impulses, limit=3)

        # ABC 必须与本窗口最近的有效推动浪 W5 相接；没有父推动浪则不猜测修正结构。
        parent_impulse = max(
            valid_impulses,
            key=lambda x: x.get("end_index", -1),
            default=None,
        )
        corrective = (
            _find_corrective_candidates(zigzag, after_impulse=parent_impulse)
            if parent_impulse is not None
            else []
        )
        corrective = _keep_latest_candidates(corrective, limit=3)

        # 评估总分
        total_score = sum(imp["fib_score"] for imp in valid_impulses) + \
                      sum(c["fib_score"] for c in corrective)

        pattern_ends = [
            x.get("end_index", -1) for x in valid_impulses + corrective
        ]
        has_pattern = bool(pattern_ends)
        latest_end = (
            max(pattern_ends)
            if has_pattern
            else max((p.get("index", -1) for p in zigzag), default=-1)
        )
        selection_key = (int(has_pattern), latest_end, total_score)

        if best_selection_key is None or selection_key > best_selection_key:
            best_selection_key = selection_key
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
                "detection_window": w,
            }

    if best_result is None:
        empty_result["reliability"] = _empty_reliability(df)
        return empty_result
    best_result["reliability"] = _merge_reliability_streams(
        df,
        reliability_streams,
        best_result["current_wave"],
    )
    return best_result


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

        # 方向调整：偏向表以“上涨推动 / 下跌修正”为基准。
        if wave_type == "impulse":
            if direction == "bearish":
                bias = -bias
            elif direction != "bullish":
                bias = 0
        elif wave_type == "corrective":
            if direction == "bullish":
                bias = -bias
            elif direction != "bearish":
                bias = 0

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
