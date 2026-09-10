"""elliott_local.py — 艾略特波浪本地引擎（zigzag → 铁律校验 → 斐波那契评分）。

为什么不用 v5 的 elliott_wave 模块做图表叠加：v5 严格规则在全历史找 Top3 浪型，
对多数股票产出为空或枢轴过老，图表上看不到东西。本引擎面向"当前窗口可读性"：
  • ATR 自适应 zigzag（粗/细两个级别），不依赖固定窗口摆动点；
  • 三大铁律硬校验（浪2不破起点 / 浪3非最短 / 浪4不入浪1区）+ 斐波那契评分；
    无完全合规浪时放宽容差取最佳近似并如实标注；
  • 输出最近一个完整推动浪（0-1-2-3-4-5）+ 其后修正浪（0-A-B-C），
    并对最长主浪做细级别子浪标注（i-ii-iii-iv-v）；
  • 投射目标：浪3 1.618/2.618、浪5 0.618/1.0、C浪 0.618/1.0。

输入 server 约定的小写 ohlcv + DatetimeIndex；纯 pandas/numpy。
"""
from __future__ import annotations

import hashlib
import json
import math

import numpy as np
import pandas as pd

# 斐波那契理想区间（评分用）
_IDEAL = {
    "w2": (0.50, 0.618, 0.786),     # 浪2/浪1 回撤
    "w3": (1.618, 2.0, 2.618),      # 浪3/浪1
    "w4": (0.236, 0.309, 0.382),    # 浪4/浪3 回撤
    "w5": (0.618, 0.809, 1.0),      # 浪5/浪1
    "b": (0.50, 0.618, 0.886),      # B/A 回撤
    "c": (0.618, 0.809, 1.0),       # C/A
}


def _fmt_time(t) -> str:
    return t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)[:10]


def _prepare_df(df: pd.DataFrame, lookback: int, min_rows: int = 60) -> pd.DataFrame | None:
    """Return a chronological, finite OHLC window or ``None``.

    Live providers occasionally return duplicate columns/index rows, reversed
    history, infinities, or malformed high/low rows.  Structure detection must
    not silently turn those rows into pivots.
    """
    if not isinstance(df, pd.DataFrame) or lookback <= 0:
        return None
    out = df.loc[:, ~df.columns.duplicated()].copy()
    required = ("high", "low", "close")
    if any(col not in out.columns for col in required):
        return None
    out = out.sort_index(kind="stable")
    out = out.loc[~out.index.duplicated(keep="last")]
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=list(required))
    valid = (
        (out["high"] >= out["low"])
        & (out["low"] > 0)
        & (out["close"] > 0)
        & (out["close"] <= out["high"])
        & (out["close"] >= out["low"])
    )
    out = out.loc[valid].tail(lookback)
    return out if len(out) >= min_rows else None


def _atr(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    h, l, c = df["high"].to_numpy(float), df["low"].to_numpy(float), df["close"].to_numpy(float)
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    out = pd.Series(tr).rolling(n, min_periods=1).mean().to_numpy()
    return out


# ── 1. ATR 自适应 zigzag ─────────────────────────────────────────────
def _zigzag(df: pd.DataFrame, atr_mult: float = 2.5, min_bars: int = 4) -> list[dict]:
    """返回枢轴 [{pos, type: high|low, price, time, unconfirmed?}]（顶底交替）。"""
    n = len(df)
    if n < min_bars * 4:
        return []
    h = df["high"].to_numpy(float)
    l = df["low"].to_numpy(float)
    idx = list(df.index)
    atr = _atr(df)
    fallback_th = float(np.nanmean(h - l)) * atr_mult

    def th(i: int) -> float:
        v = atr[i] * atr_mult
        return v if math.isfinite(v) and v > 0 else max(fallback_th, 1e-6)

    hi_i = int(np.argmax(h[:min_bars]))
    lo_i = int(np.argmin(l[:min_bars]))
    pivots: list[dict] = []
    if hi_i < lo_i:  # 先高后低 → 首腿向下
        pivots.append({"pos": hi_i, "type": "high", "price": float(h[hi_i])})
        trend, ex_pos, ex_price = "down", lo_i, float(l[lo_i])
    else:
        pivots.append({"pos": lo_i, "type": "low", "price": float(l[lo_i])})
        trend, ex_pos, ex_price = "up", hi_i, float(h[hi_i])

    for i in range(min_bars, n):
        if trend == "up":
            if h[i] >= ex_price:
                ex_price, ex_pos = float(h[i]), i
            elif ex_price - l[i] >= th(i) and i - ex_pos >= min_bars:
                pivots.append({"pos": ex_pos, "type": "high", "price": ex_price})
                trend, ex_pos, ex_price = "down", i, float(l[i])
        else:
            if l[i] <= ex_price:
                ex_price, ex_pos = float(l[i]), i
            elif h[i] - ex_price >= th(i) and i - ex_pos >= min_bars:
                pivots.append({"pos": ex_pos, "type": "low", "price": ex_price})
                trend, ex_pos, ex_price = "up", i, float(h[i])
    # 当前未确认腿端点（最后一个枢轴可能是临时极值）
    pivots.append({"pos": ex_pos, "type": "high" if trend == "up" else "low",
                   "price": ex_price, "unconfirmed": True})
    # 合并相邻同型（取更极端）
    merged: list[dict] = []
    for p in pivots:
        if merged and merged[-1]["type"] == p["type"]:
            better = (p["type"] == "high" and p["price"] >= merged[-1]["price"]) or \
                     (p["type"] == "low" and p["price"] <= merged[-1]["price"])
            if better:
                merged[-1] = p
            continue
        if merged and merged[-1]["pos"] == p["pos"]:
            continue
        merged.append(p)
    for p in merged:
        p["time"] = idx[p["pos"]]
    return merged


# ── 2. 推动浪校验与评分 ───────────────────────────────────────────────
def _wave_sizes(pts: list[dict], direction: str) -> list[float]:
    """各浪幅度（带方向符号，推动方向为正）。"""
    out = []
    sign = 1.0 if direction == "bullish" else -1.0
    for a, b in zip(pts, pts[1:]):
        out.append(sign * (b["price"] - a["price"]))
    return out


def _ratio_score(r: float, ideal: tuple[float, float, float], w: float = 1.0) -> float:
    """比率落在理想区间内满分，偏离线性衰减。"""
    lo, mid, hi = ideal
    if lo <= r <= hi:
        # 区间内：越接近 mid 越高（0.75~1.0）
        return w * (0.75 + 0.25 * max(0.0, 1.0 - abs(r - mid) / max(hi - lo, 1e-9)))
    dev = (lo - r) if r < lo else (r - hi)
    return max(0.0, w * (0.75 - dev / max(mid, 1e-9) * 1.5))


def _score_impulse(pts: list[dict], direction: str, tol: float = 0.0):
    """三大铁律校验 + 斐波那契评分。返回 (通过?, 得分, 违规列表)。"""
    w = _wave_sizes(pts, direction)
    if len(w) != 5 or w[0] <= 0 or w[2] <= 0 or w[4] <= 0 or w[1] >= 0 or w[3] >= 0:
        return False, 0.0, ["浪型方向交替不符"]
    p = pts
    viol = []
    # 铁律1：浪2 不跌破浪1 起点
    if abs(w[1]) >= abs(w[0]) * (1 + tol):
        viol.append("浪2回撤超浪1起点")
    # 铁律2：浪3 非最短
    if abs(w[2]) < abs(w[0]) * (1 - tol) and abs(w[2]) < abs(w[4]) * (1 - tol):
        viol.append("浪3为最短浪")
    # 铁律3：浪4 不入浪1 价格区。容差必须以 W1 浪幅为基准，而不能按
    # 股票绝对价格计算；否则高价、低波动股票会得到异常宽松的重叠空间。
    overlap_tol = abs(w[0]) * tol
    if direction == "bullish":
        if p[4]["price"] < p[1]["price"] - overlap_tol:
            viol.append("浪4进入浪1区间")
    else:
        if p[4]["price"] > p[1]["price"] + overlap_tol:
            viol.append("浪4进入浪1区间")

    r2 = abs(w[1]) / abs(w[0])
    r3 = abs(w[2]) / abs(w[0])
    r4 = abs(w[3]) / abs(w[2])
    r5 = abs(w[4]) / abs(w[0])
    score = (_ratio_score(r2, _IDEAL["w2"], 1.0) + _ratio_score(r3, _IDEAL["w3"], 1.4)
             + _ratio_score(r4, _IDEAL["w4"], 0.9) + _ratio_score(r5, _IDEAL["w5"], 1.1))
    return len(viol) == 0, round(score, 3), viol


def _find_best_impulse(pivots: list[dict], n_bars: int, cur_price: float):
    """在 zigzag 枢轴上滑动 6 点窗口，找评分最优的推动浪。

    分级近度优先：先在最近 30% K 线内结束的候选中选，其次 60%，最后全量——
    避免一个陈旧但斐波那契漂亮的浪盖过当前真正相关的浪。
    """
    candidates: list[dict] = []
    for direction, types in (("bullish", ["low", "high"] * 3),
                             ("bearish", ["high", "low"] * 3)):
        for i in range(0, len(pivots) - 5):
            pts = pivots[i:i + 6]
            if [p["type"] for p in pts] != types:
                continue
            end_pos = pts[-1]["pos"]
            # 浪型显著性：浪3 幅度 ≥ 1% 价格
            if abs(pts[3]["price"] - pts[2]["price"]) / max(cur_price, 1e-6) < 0.01:
                continue
            strict_ok, strict_score, strict_viol = _score_impulse(pts, direction)
            if strict_ok:
                score = strict_score
                mode = "strict"
                violations: list[str] = []
                tolerance_pct = 0.0
            else:
                relaxed_ok, relaxed_score, _relaxed_viol = _score_impulse(
                    pts, direction, tol=0.02
                )
                if not relaxed_ok:
                    continue
                score = relaxed_score * 0.75
                mode = "approximate"
                # Preserve the strict-rule failures.  A tolerance pass is not
                # equivalent to a fully compliant Elliott count.
                violations = strict_viol
                tolerance_pct = 2.0
            recency = end_pos / max(n_bars - 1, 1)
            final = score * (0.55 + 0.45 * recency)
            candidates.append({
                "pts": pts,
                "direction": direction,
                "fib_score": round(score, 1),
                "violations": violations,
                "ruleMode": mode,
                "tolerancePct": tolerance_pct,
                "confirmed": not bool(pts[-1].get("unconfirmed")),
                "final": final,
                "end_pos": end_pos,
                "start_pos": pts[0]["pos"],
            })

    # A strict count is preferable to an approximate count.  Within the same
    # quality class, retain the former near/mid/full-window recency policy.
    for mode in ("strict", "approximate"):
        quality = [c for c in candidates if c["ruleMode"] == mode]
        for frac in (0.7, 0.4, 0.0):
            recent = [c for c in quality if c["end_pos"] >= n_bars * frac]
            if recent:
                return max(recent, key=lambda c: c["final"])
    return None


def _find_inprogress_impulse(pivots: list[dict], n_bars: int, cur_price: float):
    """识别**进行中**的推动浪：最后 4/5 个枢轴构成 0-1-2(-3[-4]) 且末端贴近最新价。

    返回 {"pts", "direction", "wave_no", "fib_score"} 或 None。
    用于正在走主升/主跌的股票（此时标一个过时的完整浪会误导）。
    """
    if len(pivots) < 4:
        return None
    last_pos = pivots[-1]["pos"]
    if n_bars - 1 - last_pos > 40:  # 最后一个枢轴离得太久，不算进行中
        return None
    for direction, types4 in (("bullish", ["low", "high", "low", "high"]),
                              ("bearish", ["high", "low", "high", "low"])):
        types5 = types4 + [types4[0]]
        # 0-1-2-3 进行中（第3浪/第3浪末端）
        pts = pivots[-4:]
        if [p["type"] for p in pts] == types4:
            w = _wave_sizes(pts, direction)
            if len(w) == 3 and w[0] > 0 and w[2] > 0 and w[1] < 0:
                r2 = abs(w[1]) / abs(w[0])
                r3 = abs(w[2]) / abs(w[0])
                if r2 < 1.0 and r3 >= 1.0:  # 浪2未破起点 & 浪3不短于浪1
                    score = (_ratio_score(r2, _IDEAL["w2"], 1.0)
                             + _ratio_score(r3, _IDEAL["w3"], 1.4))
                    return {"pts": pts, "direction": direction, "wave_no": 3,
                            "fib_score": round(score, 1),
                            "end_pos": pts[-1]["pos"], "start_pos": pts[0]["pos"],
                            "violations": [], "ruleMode": "strict",
                            "tolerancePct": 0.0, "confirmed": False}
        # 0-1-2-3-4 进行中（第4浪）
        pts = pivots[-5:]
        if len(pts) == 5 and [p["type"] for p in pts] == types5:
            w = _wave_sizes(pts, direction)
            if len(w) == 4 and w[0] > 0 and w[2] > 0 and w[1] < 0 and w[3] < 0:
                r2 = abs(w[1]) / abs(w[0])
                r3 = abs(w[2]) / abs(w[0])
                r4 = abs(w[3]) / abs(w[2])
                in_w1_zone = (pts[4]["price"] < pts[1]["price"]) if direction == "bullish" \
                    else (pts[4]["price"] > pts[1]["price"])
                if r2 < 1.0 and r3 >= 1.0 and not in_w1_zone:
                    score = (_ratio_score(r2, _IDEAL["w2"], 1.0)
                             + _ratio_score(r3, _IDEAL["w3"], 1.4)
                             + _ratio_score(r4, _IDEAL["w4"], 0.9))
                    return {"pts": pts, "direction": direction, "wave_no": 4,
                            "fib_score": round(score, 1),
                            "end_pos": pts[-1]["pos"], "start_pos": pts[0]["pos"],
                            "violations": [], "ruleMode": "strict",
                            "tolerancePct": 0.0, "confirmed": False}
    return None


# ── 3. 修正浪 ABC ────────────────────────────────────────────────────
def _corrective_after(pivots: list[dict], imp: dict):
    """推动浪结束后的 0-A-B-C（按现有枢轴标注，进行中也标）。"""
    follow = [p for p in pivots if p["pos"] > imp["end_pos"]]
    if not follow:
        return None
    pts = [imp["pts"][-1]] + follow[:3]  # 0, A, B, C
    if len(pts) < 2:
        return None
    direction = "bearish" if imp["direction"] == "bullish" else "bullish"
    w = _wave_sizes(pts, direction)
    # B 回撤不超过 A（宽松校验）
    score = 0.0
    if len(w) >= 2 and w[0] > 0:
        rb = abs(w[1]) / w[0] if w[1] < 0 else 9.9
        score += _ratio_score(rb, _IDEAL["b"], 1.0)
    if len(w) >= 3 and w[0] > 0 and w[2] > 0:
        rc = w[2] / w[0]
        score += _ratio_score(rc, _IDEAL["c"], 1.0)
    return {"pts": pts, "direction": direction, "fib_score": round(score, 2),
            "complete": len(pts) >= 4 and not pts[-1].get("unconfirmed")}


def _reliability_id(namespace: str, payload: dict) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"elliott-{namespace}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _reliability_point(point: dict, label: str) -> dict:
    return {
        "time": _fmt_time(point["time"]),
        "price": round(float(point["price"]), 6),
        "label": label,
        "pivotType": str(point["type"]),
    }


def _confirmed_impulse_candidates(pivots: list[dict]) -> list[dict]:
    """Return every strictly valid, causally confirmed six-pivot impulse.

    Unlike the display count, this stream is not ranked by current price or
    recency.  Once the next opposite pivot confirms point 5, future bars can
    only append candidates while the input origin is unchanged.
    """

    candidates: list[dict] = []
    for direction, types in (
        ("bullish", ["low", "high"] * 3),
        ("bearish", ["high", "low"] * 3),
    ):
        for index in range(0, len(pivots) - 6):
            points = pivots[index:index + 6]
            if [point["type"] for point in points] != types:
                continue
            reference = max(
                abs(float(points[0]["price"])),
                abs(float(points[-1]["price"])),
                1e-6,
            )
            if abs(float(points[3]["price"]) - float(points[2]["price"])) / reference < 0.01:
                continue
            strict_ok, fib_score, violations = _score_impulse(points, direction)
            if not strict_ok or violations or points[-1].get("unconfirmed"):
                continue
            confirmation_pivot = pivots[index + 6]
            point_views = [
                _reliability_point(point, str(label))
                for point, label in zip(points, range(6))
            ]
            identity = {
                "kind": "impulse",
                "level": 0,
                "direction": direction,
                "points": point_views,
                "confirmedTime": _fmt_time(confirmation_pivot["time"]),
                "methodVersion": "elliott-reliability-1",
            }
            view = {
                **identity,
                "structureId": _reliability_id("impulse", identity),
                "parentStructureId": None,
                "startTime": point_views[0]["time"],
                "endTime": point_views[-1]["time"],
                "startPrice": point_views[0]["price"],
                "endPrice": point_views[-1]["price"],
                "fibScore": round(float(fib_score), 3),
                "ruleMode": "strict",
                "violations": [],
                "validationBasis": (
                    "three_strict_impulse_rules_and_next_opposite_pivot"
                ),
                "validated": True,
                "confirmed": True,
                "status": "confirmed",
            }
            candidates.append(
                {
                    "view": view,
                    "points": points,
                    "pivotIndex": index,
                }
            )
    candidates.sort(
        key=lambda item: (
            item["view"]["confirmedTime"],
            item["view"]["startTime"],
            item["view"]["structureId"],
        )
    )
    return candidates


def _is_triangle(points: list[dict], parent_direction: str) -> bool:
    if len(points) != 6:
        return False
    moves = [
        float(right["price"]) - float(left["price"])
        for left, right in zip(points, points[1:])
    ]
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


def _is_double_three(points: list[dict], parent_direction: str) -> bool:
    if len(points) != 8:
        return False
    moves = [
        float(right["price"]) - float(left["price"])
        for left, right in zip(points, points[1:])
    ]
    expected_first = -1.0 if parent_direction == "bullish" else 1.0
    expected_signs = [expected_first * (-1.0 if index % 2 else 1.0) for index in range(7)]
    if any(move * sign <= 0 for move, sign in zip(moves, expected_signs)):
        return False
    origin = float(points[0]["price"])
    x_price = float(points[4]["price"])
    first_c = float(points[3]["price"])
    second_c = float(points[7]["price"])
    if parent_direction == "bullish":
        return first_c < x_price < origin and second_c < x_price
    return origin < x_price < first_c and second_c > x_price


def _complex_correction_stream(
    pivots: list[dict], roots: list[dict]
) -> list[dict]:
    structures: list[dict] = []
    labels_by_pattern = {
        "triangle": ["0", "A", "B", "C", "D", "E"],
        "double_three": ["0", "A", "B", "C", "X", "A2", "B2", "C2"],
    }
    for root in roots:
        start_index = int(root["pivotIndex"]) + 6
        follow = pivots[start_index:]
        pattern: str | None = None
        correction_points: list[dict] = []
        confirmation_pivot: dict | None = None
        if len(follow) >= 8:
            candidate = [root["points"][-1], *follow[:7]]
            if not follow[6].get("unconfirmed") and _is_double_three(
                candidate, root["view"]["direction"]
            ):
                pattern = "double_three"
                correction_points = candidate
                confirmation_pivot = follow[7]
        if pattern is None and len(follow) >= 6:
            candidate = [root["points"][-1], *follow[:5]]
            if not follow[4].get("unconfirmed") and _is_triangle(
                candidate, root["view"]["direction"]
            ):
                pattern = "triangle"
                correction_points = candidate
                confirmation_pivot = follow[5]
        if pattern is None or confirmation_pivot is None:
            continue
        point_views = [
            _reliability_point(point, label)
            for point, label in zip(correction_points, labels_by_pattern[pattern])
        ]
        direction = (
            "bearish" if root["view"]["direction"] == "bullish" else "bullish"
        )
        identity = {
            "kind": "complex_correction",
            "pattern": pattern,
            "level": 0,
            "parentStructureId": root["view"]["structureId"],
            "direction": direction,
            "points": point_views,
            "confirmedTime": _fmt_time(confirmation_pivot["time"]),
            "methodVersion": "elliott-reliability-1",
        }
        structures.append(
            {
                **identity,
                "structureId": _reliability_id(pattern, identity),
                "startTime": point_views[0]["time"],
                "endTime": point_views[-1]["time"],
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
            }
        )
    return structures


def _nested_impulse_stream(
    frame: pd.DataFrame, roots: list[dict]
) -> tuple[list[dict], int]:
    structures: list[dict] = []
    rejected_direction = 0
    for root in roots:
        parent_points = root["points"]
        for leg_number, (left, right) in enumerate(
            zip(parent_points, parent_points[1:]), start=1
        ):
            if int(right["pos"]) - int(left["pos"]) < 12:
                continue
            leg_frame = frame.iloc[int(left["pos"]):int(right["pos"]) + 1]
            fine_pivots = _zigzag(leg_frame, atr_mult=1.2, min_bars=3)
            expected_direction = (
                "bullish"
                if float(right["price"]) > float(left["price"])
                else "bearish"
            )
            for child in _confirmed_impulse_candidates(fine_pivots):
                child_view = dict(child["view"])
                if child_view["direction"] != expected_direction:
                    rejected_direction += 1
                    continue
                identity = {
                    "kind": "nested_impulse",
                    "level": 1,
                    "parentStructureId": root["view"]["structureId"],
                    "parentLeg": leg_number,
                    "direction": child_view["direction"],
                    "points": child_view["points"],
                    "confirmedTime": child_view["confirmedTime"],
                    "methodVersion": "elliott-reliability-1",
                }
                structures.append(
                    {
                        **child_view,
                        **identity,
                        "structureId": _reliability_id("nested", identity),
                        "nestedDirection": "consistent",
                        "validationBasis": (
                            "strict_child_impulse_and_parent_leg_direction_match"
                        ),
                    }
                )
    unique = {item["structureId"]: item for item in structures}
    return list(unique.values()), rejected_direction


def _frame_reliability_hash(frame: pd.DataFrame) -> str:
    columns = [name for name in ("open", "high", "low", "close", "volume") if name in frame]
    rows = [
        [
            _fmt_time(index),
            *[
                round(float(value), 8) if math.isfinite(float(value)) else None
                for value in row
            ],
        ]
        for index, row in zip(frame.index, frame[columns].to_numpy())
    ]
    return _reliability_id("input", {"columns": columns, "rows": rows})


def _build_elliott_reliability(frame: pd.DataFrame, pivots: list[dict]) -> dict:
    roots = _confirmed_impulse_candidates(pivots)
    root_views = [dict(item["view"]) for item in roots]
    corrections = _complex_correction_stream(pivots, roots)
    nested, rejected_direction = _nested_impulse_stream(frame, roots)
    confirmed = [*root_views, *corrections, *nested]
    confirmed.sort(
        key=lambda item: (
            item["confirmedTime"],
            int(item["level"]),
            item["structureId"],
        )
    )
    levels = []
    for level in sorted({int(item["level"]) for item in confirmed}):
        level_items = [item for item in confirmed if int(item["level"]) == level]
        levels.append(
            {
                "level": level,
                "name": "primary" if level == 0 else "nested",
                "confirmedCount": len(level_items),
                "directions": sorted({str(item["direction"]) for item in level_items}),
            }
        )
    return {
        "version": "elliott-reliability-1",
        "source": "apps.web.server.elliott_local",
        "methodVersion": "elliott-reliability-1",
        "originTime": _fmt_time(frame.index[0]),
        "lastInputTime": _fmt_time(frame.index[-1]),
        "inputHash": _frame_reliability_hash(frame),
        "prefixPolicy": "append_only_confirmed_ids_while_origin_and_method_match",
        "confirmedStructures": confirmed,
        "provisionalStructures": [],
        "levels": levels,
        "nestedDirection": {
            "acceptedCount": len(nested),
            "rejectedMismatchCount": rejected_direction,
            "policy": "child_direction_must_match_parent_leg",
        },
    }


def _attach_provisional_current(reliability: dict, current_wave: dict) -> dict:
    result = dict(reliability)
    identity = {
        "kind": "current_count",
        "type": current_wave.get("type"),
        "waveNumber": current_wave.get("waveNumber"),
        "direction": current_wave.get("direction"),
        "lastInputTime": reliability["lastInputTime"],
        "methodVersion": "elliott-reliability-1",
    }
    result["provisionalStructures"] = [
        {
            **identity,
            "structureId": _reliability_id("provisional", identity),
            "validationBasis": "current_count_has_no_future_confirmation_anchor",
            "validated": False,
            "confirmed": False,
            "status": "provisional",
        }
    ]
    return result


# ── 主入口 ───────────────────────────────────────────────────────────
def analyze_elliott(df: pd.DataFrame, lookback: int = 500) -> dict:
    """返回 {currentWave, targets, overlay: {segments, labels, targets}}；失败返回 None。

    优先展示"进行中"的推动浪（0-1-2[-3[-4]]，末端贴近最新价）；否则最近一个
    完整推动浪（0-5）+ 接续修正浪（0-A-B-C）；再对最长主浪做细级别子浪标注。
    """
    seg_df = _prepare_df(df, lookback)
    if seg_df is None:
        return None
    n = len(seg_df)
    cur_price = float(seg_df["close"].iloc[-1])
    pivots = _zigzag(seg_df, atr_mult=2.5, min_bars=5)
    if len(pivots) < 4:
        return None

    best = _find_best_impulse(pivots, n, cur_price)
    inprog = _find_inprogress_impulse(pivots, n, cur_price)
    if best is None and inprog is None:
        # 降低反转阈值重试一次，让平缓浪型产生更多候选枢轴。
        pivots = _zigzag(seg_df, atr_mult=1.8, min_bars=4)
        if len(pivots) >= 4:
            best = _find_best_impulse(pivots, n, cur_price)
            inprog = _find_inprogress_impulse(pivots, n, cur_price)
        if best is None and inprog is None:
            return None

    labels: list[dict] = []
    segments: list[dict] = []

    def _push_wave(pts, names, kind):
        for i, p in enumerate(pts):
            prev = pts[i - 1] if i > 0 else None
            labels.append({
                "time": _fmt_time(p["time"]), "price": round(p["price"], 3),
                "label": names[i] if i < len(names) else str(i),
                "pivotType": p["type"], "kind": kind,
                "dir": (("up" if p["price"] >= prev["price"] else "down")
                        if prev else None),
            })
        for a, b in zip(pts, pts[1:]):
            segments.append({"kind": kind,
                             "points": [{"time": _fmt_time(a["time"]), "price": round(a["price"], 3)},
                                        {"time": _fmt_time(b["time"]), "price": round(b["price"], 3)}]})

    def _sub_of_leg(a: dict, b: dict):
        """Only label a validated, same-direction five-wave subdivision."""
        if b["pos"] - a["pos"] < 12:
            return
        seg = seg_df.iloc[a["pos"]:b["pos"] + 1]
        fine = _zigzag(seg, atr_mult=1.2, min_bars=3)
        if len(fine) < 6:
            return
        expected_direction = "bullish" if b["price"] > a["price"] else "bearish"
        sub_impulse = _find_best_impulse(fine, len(seg), max(abs(b["price"]), 1e-6))
        if sub_impulse is None or sub_impulse["direction"] != expected_direction:
            return
        subs = []
        for p in sub_impulse["pts"]:
            q = dict(p)
            q["pos"] = a["pos"] + p["pos"]
            q["time"] = seg_df.index[q["pos"]]
            subs.append(q)
        _push_wave(subs, ["0", "i", "ii", "iii", "iv", "v"], "sub")

    def _finalize_overlay() -> dict:
        # lightweight-charts requires monotonically ordered markers.  When a
        # corrective 0 or subwave endpoint coincides with a main-wave endpoint,
        # prefer the main label rather than stacking contradictory text.
        rank = {"impulse": 0, "corrective": 1, "sub": 2}
        ordered = sorted(
            labels,
            key=lambda x: (x["time"], rank.get(x.get("kind"), 9), x["price"], x["label"]),
        )
        unique: list[dict] = []
        occupied: set[tuple[str, float]] = set()
        for label in ordered:
            key = (label["time"], round(float(label["price"]), 6))
            if key in occupied:
                continue
            occupied.add(key)
            unique.append(label)
        sorted_segments = sorted(
            segments,
            key=lambda x: (
                x["points"][0]["time"],
                x["points"][-1]["time"],
                rank.get(x.get("kind"), 9),
            ),
        )
        return {"segments": sorted_segments, "labels": unique, "targets": []}

    last_pivot_pos = pivots[-1]["pos"]
    # 进行中浪型优先：最新结构可读性最强；完整浪若同样新鲜则保留完整浪
    use_inprog = inprog is not None and (
        best is None or last_pivot_pos - best["end_pos"] > 10)
    
    if use_inprog:
        pts = inprog["pts"]
        wno = inprog["wave_no"]
        names = ["0", "1", "2", "3", "4"][:len(pts)]
        _push_wave(pts, names, "impulse")
        _sub_of_leg(pts[-2], pts[-1])
        imp_dir_cn = "上涨" if inprog["direction"] == "bullish" else "下跌"
        phase = f"{imp_dir_cn}推动浪第{wno}浪进行中"
        desc = (f"识别到进行中的{imp_dir_cn}推动浪：当前处于第{wno}浪"
                f"{'（主升/主跌段，趋势最强）' if wno == 3 else '（调整段，注意浪4终点勿入浪1区）'}"
                f"（本地波浪引擎：ATR zigzag + 铁律/斐波那契评分）")
        w1 = pts[1]["price"] - pts[0]["price"]
        targets = []
        if wno == 3:
            base = pts[2]["price"]
            for r, lab in ((1.618, "W3目标(1.618×W1)"), (2.618, "W3目标(2.618×W1)")):
                targets.append({"price": round(base + w1 * r, 3), "label": lab, "ratio": f"{r}",
                                "direction": inprog["direction"]})
        else:
            base = pts[4]["price"]
            for r, lab in ((0.618, "W5目标(0.618×W1)"), (1.0, "W5目标(1.0×W1)")):
                targets.append({"price": round(base + w1 * r, 3), "label": lab, "ratio": f"{r}",
                                "direction": inprog["direction"]})
        confidence = min(85.0, max(35.0, inprog["fib_score"] / 2.4 * 100))
        overlay = _finalize_overlay()
        overlay["targets"] = targets
        current_wave = {"type": "impulse", "waveNumber": wno,
                        "direction": inprog["direction"],
                        "confidence": round(confidence, 0), "phase": phase,
                        "description": desc,
                        "fibScore": inprog["fib_score"], "violations": [],
                        "ruleMode": inprog["ruleMode"],
                        "tolerancePct": inprog["tolerancePct"],
                        "confirmed": False}
        reliability = _attach_provisional_current(
            _build_elliott_reliability(seg_df, pivots), current_wave
        )
        return {
            "currentWave": current_wave,
            "targets": targets,
            "overlay": overlay,
            "methodVersion": "elliott-local-2",
            "reliability": reliability,
        }

    # ── 完整推动浪 + 修正浪 ──
    imp = best
    _push_wave(imp["pts"], ["0", "1", "2", "3", "4", "5"], "impulse")
    cor = _corrective_after(pivots, imp)
    if cor:
        _push_wave(cor["pts"], ["0", "A", "B", "C"], "corrective")
    pts = imp["pts"]
    legs = [(pts[i], pts[i + 1]) for i in range(5)]
    li = int(np.argmax([abs(b["price"] - a["price"]) for a, b in legs]))
    _sub_of_leg(*legs[li])

    follow_cnt = len([p for p in pivots if p["pos"] > imp["end_pos"]])
    imp_dir_cn = "上涨" if imp["direction"] == "bullish" else "下跌"
    approx = "（近似浪型：" + "、".join(imp["violations"]) + "）" if imp["violations"] else ""
    if follow_cnt == 0:
        phase = f"{imp_dir_cn}推动浪第5浪末端"
        desc = f"第5浪可能{'已完成' if pivots[-1].get('unconfirmed') is None else '延伸中'}，注意修正风险{approx}"
        wave_type, wave_no, direction = "impulse", 5, imp["direction"]
    elif follow_cnt <= 3:
        stage = {1: "修正A浪", 2: "修正B浪"}.get(follow_cnt, "修正C浪")
        phase = f"{imp_dir_cn}推动浪完成，进入{stage}"
        desc = (f"0-5 推动浪（{imp_dir_cn}）已完成，当前处于 {stage}"
                f"{'（C浪或已结束，关注新推动浪启动）' if follow_cnt >= 3 else ''}{approx}")
        wave_type, wave_no = "corrective", follow_cnt
        direction = "bearish" if imp["direction"] == "bullish" else "bullish"
    else:
        phase = f"{imp_dir_cn}推动浪及其ABC修正已结束，等待新推动浪确认"
        desc = (
            "旧周期之后已出现超过 ABC 的后续枢轴，但尚未形成合规的新推动浪；"
            f"当前按过渡结构处理，避免把后续枢轴继续误标为 C 浪{approx}"
        )
        wave_type, wave_no, direction = "transition", 0, "neutral"

    targets = []
    p = imp["pts"]
    w1 = p[1]["price"] - p[0]["price"]
    if follow_cnt == 0:
        base = p[4]["price"]
        for r, lab in ((0.618, "W5目标(0.618×W1)"), (1.0, "W5目标(1.0×W1)")):
            targets.append({"price": round(base + w1 * r, 3), "label": lab, "ratio": f"{r}",
                            "direction": imp["direction"]})
    elif follow_cnt <= 3 and cor and len(cor["pts"]) >= 3:
        a_len = cor["pts"][1]["price"] - cor["pts"][0]["price"]
        base = cor["pts"][2]["price"]
        for r, lab in ((0.618, "C浪目标(0.618×A)"), (1.0, "C浪目标(1.0×A)")):
            targets.append({"price": round(base + a_len * r, 3), "label": lab, "ratio": f"{r}",
                            "direction": cor["direction"]})

    confirmed = bool(imp.get("confirmed", not imp["pts"][-1].get("unconfirmed")))
    confidence = min(90.0, max(30.0, imp["fib_score"] / 4.4 * 100))
    if imp.get("ruleMode") == "approximate":
        confidence *= 0.75
    if not confirmed:
        confidence *= 0.85
    if wave_type == "transition":
        confidence = min(confidence, 35.0)
    overlay = _finalize_overlay()
    overlay["targets"] = targets
    current_wave = {
            "type": wave_type, "waveNumber": wave_no, "direction": direction,
            "confidence": round(confidence, 0), "phase": phase,
            "description": desc + "（本地波浪引擎：ATR zigzag + 铁律/斐波那契评分）",
            "fibScore": imp["fib_score"],
            "violations": imp["violations"],
            "ruleMode": imp.get("ruleMode", "strict"),
            "tolerancePct": imp.get("tolerancePct", 0.0),
            "confirmed": confirmed,
        }
    reliability = _attach_provisional_current(
        _build_elliott_reliability(seg_df, pivots), current_wave
    )
    return {
        "currentWave": current_wave,
        "targets": targets,
        "overlay": overlay,
        "methodVersion": "elliott-local-2",
        "reliability": reliability,
    }
