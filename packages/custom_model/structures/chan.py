"""chan.py — 缠论（缠中说禅）本地引擎：包含处理 → 分型 → 笔 → 中枢 → 买卖点。

旧模型（../custom-model）没有缠论模块，本文件为 Web 端自建实现。
纯 pandas/numpy，输入 server 约定的小写 ohlcv + DatetimeIndex。

工程约定（缠论存在主观性，此处取常见可复现口径，描述中如实标注）：
  • 包含关系：相邻合并 K 出现包含时按方向合并 —— 向上取高高、向下取低低；
  • 分型：合并 K 序列上的顶/底分型（中间 K 高低点同时最高/最低）；
  • 笔：顶底分型交替、间隔 ≥4 根合并 K（老笔口径）；同类分型相邻取更极端者；
  • 中枢：连续三笔重叠区 [ZD, ZG]，其后笔与之重叠则延伸（GG/DD 记录极值），
    笔完全离开区间视为中枢结束（离开方向）；
  • 买卖点（简化判定）：1买/1卖 = 中枢外新极值且 MACD 柱面积背驰；
    2买/2卖 = 1买/卖后回抽不破前低/前高；3买/3卖 = 离开中枢后回抽不回中枢。
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping

import numpy as np
import pandas as pd

BI_MIN_GAP = 4  # 老笔：顶底分型间隔 ≥4 根合并 K（含两端即 5 根）


def _fmt_time(t) -> str:
    if hasattr(t, "isoformat"):
        has_clock_time = any(
            getattr(t, part, 0)
            for part in ("hour", "minute", "second", "microsecond", "nanosecond")
        )
        if has_clock_time or getattr(t, "tzinfo", None) is not None:
            return t.isoformat()
    return t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)[:10]


# ── 1. 包含处理 ──────────────────────────────────────────────────────
def _merge_bars(df: pd.DataFrame) -> list[dict]:
    """小写 ohlcv → 合并 K 序列 [{high, low, time, idx(原始终止根号)}]。"""
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    times = list(df.index)
    merged: list[dict] = []
    for i in range(len(df)):
        h, l = float(highs[i]), float(lows[i])
        if not (math.isfinite(h) and math.isfinite(l)):
            continue
        if not merged:
            merged.append({
                "high": h, "low": l, "time": times[i], "idx": i,
                "start_time": times[i], "start_idx": i,
                "high_time": times[i], "high_idx": i,
                "low_time": times[i], "low_idx": i,
            })
            continue
        last = merged[-1]
        inclusion = (h >= last["high"] and l <= last["low"]) or \
                    (h <= last["high"] and l >= last["low"])
        if not inclusion:
            merged.append({
                "high": h, "low": l, "time": times[i], "idx": i,
                "start_time": times[i], "start_idx": i,
                "high_time": times[i], "high_idx": i,
                "low_time": times[i], "low_idx": i,
            })
            continue
        # 方向：与再前一根合并 K 比较；首组包含时取新高/新低方向
        if len(merged) >= 2:
            up = merged[-1]["high"] > merged[-2]["high"]
        else:
            up = h >= last["high"]
        if up:
            if h > last["high"]:
                last["high"] = h
                last["high_time"] = times[i]
                last["high_idx"] = i
            if l > last["low"]:
                last["low"] = l
                last["low_time"] = times[i]
                last["low_idx"] = i
        else:
            if h < last["high"]:
                last["high"] = h
                last["high_time"] = times[i]
                last["high_idx"] = i
            if l < last["low"]:
                last["low"] = l
                last["low_time"] = times[i]
                last["low_idx"] = i
        last["time"] = times[i]
        last["idx"] = i
    return merged


# ── 2. 分型 ──────────────────────────────────────────────────────────
def _fractals(merged: list[dict]) -> list[dict]:
    """合并 K → 分型 [{pos(合并K序号), type: top|bottom, price, time}]。"""
    out: list[dict] = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b["high"] > a["high"] and b["high"] > c["high"] \
                and b["low"] > a["low"] and b["low"] > c["low"]:
            out.append({
                "pos": i, "type": "top", "price": b["high"],
                "time": b.get("high_time", b["time"]),
                "group_time": b["time"],
                "confirmed_time": c.get("start_time", c["time"]),
                "confirmed_idx": c.get("start_idx", c["idx"]),
            })
        elif b["low"] < a["low"] and b["low"] < c["low"] \
                and b["high"] < a["high"] and b["high"] < c["high"]:
            out.append({
                "pos": i, "type": "bottom", "price": b["low"],
                "time": b.get("low_time", b["time"]),
                "group_time": b["time"],
                "confirmed_time": c.get("start_time", c["time"]),
                "confirmed_idx": c.get("start_idx", c["idx"]),
            })
    return out


# ── 3. 笔 ────────────────────────────────────────────────────────────
def _more_extreme(f: dict, ref: dict) -> bool:
    """同类分型 f 是否比 ref 更极端（顶取更高、底取更低）。"""
    if f["type"] == "top":
        return f["price"] >= ref["price"]
    return f["price"] <= ref["price"]


def _bi_list(fractals: list[dict], min_gap: int = BI_MIN_GAP) -> tuple[list[dict], list[dict]]:
    """分型序列 → (笔端点 pivots, 笔列表 bi)。

    bi 项：{start_pos,end_pos(合并K序号), start_price,end_price, start_time,end_time,
             direction: up|down, high, low}
    """
    try:
        min_gap = max(1, int(min_gap))
    except (OverflowError, TypeError, ValueError):
        min_gap = BI_MIN_GAP

    # _fractals 本身按 pos 递增；这里仍做轻量清洗，避免私有函数被测试或
    # 其他调用方直接传入乱序/非法端点后破坏笔序列不变量。
    candidates: list[dict] = []
    for raw in fractals or []:
        if not isinstance(raw, dict) or raw.get("type") not in {"top", "bottom"}:
            continue
        try:
            pos = int(raw["pos"])
            price = float(raw["price"])
        except (KeyError, OverflowError, TypeError, ValueError):
            continue
        if pos < 0 or not math.isfinite(price) or "time" not in raw:
            continue
        candidates.append({**raw, "pos": pos, "price": price})
    candidates.sort(key=lambda x: x["pos"])

    # 同一合并 K 不允许同时充当顶/底端点；同类重复只保留更极端者。
    ordered: list[dict] = []
    for f in candidates:
        if ordered and f["pos"] == ordered[-1]["pos"]:
            if f["type"] == ordered[-1]["type"] and _more_extreme(f, ordered[-1]):
                ordered[-1] = f
            continue
        ordered.append(f)

    pivots: list[dict] = []
    for f in ordered:
        if not pivots:
            pivots.append(dict(f))
        else:
            last = pivots[-1]
            if f["type"] == last["type"]:
                if _more_extreme(f, last):
                    pivots[-1] = dict(f)
            elif f["pos"] - last["pos"] >= min_gap:
                pivots.append(dict(f))
            # 间隔不足不成笔。若 f 突破前一同类端点，说明夹在中间的反向端点
            # 失效：应删除该反向端点并把前一同类端点推进到 f，而不是把反向
            # 端点直接覆盖成 f（后者会制造 bottom-bottom / top-top 的非法笔）。
            elif len(pivots) >= 2 and _more_extreme(f, pivots[-2]):
                pivots[-2] = dict(f)
                pivots.pop()

    # 最终防线：输出必须严格按位置递增且顶底交替。
    normalized: list[dict] = []
    for p in pivots:
        if normalized and p["pos"] <= normalized[-1]["pos"]:
            continue
        if normalized and p["type"] == normalized[-1]["type"]:
            if _more_extreme(p, normalized[-1]):
                normalized[-1] = p
            continue
        normalized.append(p)
    pivots = normalized

    bis: list[dict] = []
    for a, b in zip(pivots, pivots[1:]):
        # 该简化引擎没有线段层或持久化锁定状态；交替的短间隔创新极值
        # 可以逐层改写任意历史笔。端点分型虽已有可用时点，笔本身仍只能
        # 如实标为 provisional，不能声称未来不可重绘。
        confirmed = False
        confirmed_time = None
        bis.append({
            "start_pos": a["pos"], "end_pos": b["pos"],
            "start_price": round(float(a["price"]), 3),
            "end_price": round(float(b["price"]), 3),
            "start_time": a["time"], "end_time": b["time"],
            "start_confirm_time": a.get("confirmed_time", a["time"]),
            "end_confirm_time": b.get("confirmed_time", b["time"]),
            "confirmed": confirmed, "confirmed_time": confirmed_time,
            "status": "confirmed" if confirmed else "provisional",
            "direction": "up" if b["type"] == "top" else "down",
            "high": round(max(float(a["price"]), float(b["price"])), 3),
            "low": round(min(float(a["price"]), float(b["price"])), 3),
        })
    return pivots, bis


def _stable_bi_list(fractals: list[dict], min_gap: int = BI_MIN_GAP) -> list[dict]:
    """Build a causal, prefix-stable stroke stream from confirmed fractals.

    The legacy :func:`_bi_list` intentionally remains redrawable.  This second
    stream is deliberately more conservative: a stroke is locked only after a
    qualifying opposite fractal has itself been confirmed.  Once emitted as
    ``confirmed`` it is not rewritten while the calculation retains the same
    input origin; only the final stroke may be ``provisional``.  The stateless
    API cannot preserve structures that fall outside its rolling lookback.

    This is an engineering reliability layer, not a claim that one particular
    school of Chan theory is the only valid interpretation.
    """
    try:
        min_gap = max(1, int(min_gap))
    except (OverflowError, TypeError, ValueError):
        min_gap = BI_MIN_GAP

    candidates: list[dict] = []
    for raw in fractals or []:
        if not isinstance(raw, dict) or raw.get("type") not in {"top", "bottom"}:
            continue
        try:
            pos = int(raw["pos"])
            price = float(raw["price"])
        except (KeyError, OverflowError, TypeError, ValueError):
            continue
        if pos < 0 or not math.isfinite(price) or "time" not in raw:
            continue
        # Only confirmed fractals may enter the stable stream.  _fractals
        # supplies confirmed_time; direct callers must do the same.
        if raw.get("confirmed_time") is None:
            continue
        candidates.append({**raw, "pos": pos, "price": price})
    candidates.sort(key=lambda item: item["pos"])

    def make_bi(start: dict, end: dict, confirmer: dict | None) -> dict:
        confirmed = confirmer is not None
        return {
            "start_pos": start["pos"],
            "end_pos": end["pos"],
            "start_price": round(float(start["price"]), 3),
            "end_price": round(float(end["price"]), 3),
            "start_time": start["time"],
            "end_time": end["time"],
            "start_confirm_time": start["confirmed_time"],
            "end_confirm_time": end["confirmed_time"],
            "direction": "up" if end["type"] == "top" else "down",
            "high": round(max(float(start["price"]), float(end["price"])), 3),
            "low": round(min(float(start["price"]), float(end["price"])), 3),
            "confirmed": confirmed,
            "confirmed_time": confirmer["confirmed_time"] if confirmer else None,
            "status": "confirmed" if confirmed else "provisional",
            "basis": "confirmed_fractal_reversal",
        }

    start: dict | None = None
    candidate: dict | None = None
    stable: list[dict] = []
    for fractal in candidates:
        if start is None:
            start = fractal
            continue
        if candidate is None:
            if fractal["type"] == start["type"]:
                # Before any stroke is locked the initial extreme may move.
                # A locked start is the endpoint of the preceding stable bi.
                if not stable and _more_extreme(fractal, start):
                    start = fractal
            elif fractal["pos"] - start["pos"] >= min_gap:
                candidate = fractal
            continue

        if fractal["type"] == candidate["type"]:
            if _more_extreme(fractal, candidate):
                candidate = fractal
            continue

        # An opposite endpoint sufficiently far from the candidate is the
        # causal confirmation event for start -> candidate.
        if fractal["pos"] - candidate["pos"] >= min_gap:
            stable.append(make_bi(start, candidate, fractal))
            start = candidate
            candidate = fractal

    if start is not None and candidate is not None:
        stable.append(make_bi(start, candidate, None))
    return stable


def _segment_list(stable_bis: list[dict]) -> list[dict]:
    """Build conservative line segments from *confirmed* stable strokes.

    A segment needs at least three alternating strokes.  Its endpoint is the
    latest same-direction extreme and it is confirmed only when a later,
    already-confirmed opposite stroke breaks the start of that endpoint
    stroke.  The breaker becomes the first stroke of the next candidate
    segment, so confirmed segments remain continuous and prefix-stable.
    """
    # Only the contiguous confirmed prefix is admissible.  Skipping an
    # unconfirmed interior stroke would join two non-adjacent strokes and
    # fabricate a structural break that never occurred.
    confirmed_bis: list[dict] = []
    for item in stable_bis or []:
        if not isinstance(item, dict) or item.get("confirmed") is not True:
            break
        confirmed_bis.append(item)
    segments: list[dict] = []
    start_index = 0

    def make_segment(
        first: int,
        endpoint: int,
        breaker: int | None,
    ) -> dict:
        start = confirmed_bis[first]
        end = confirmed_bis[endpoint]
        members = confirmed_bis[first:endpoint + 1]
        confirmed = breaker is not None
        return {
            "start_bi": first,
            "end_bi": endpoint,
            "breaker_bi": breaker,
            "direction": start["direction"],
            "start_time": start["start_time"],
            "end_time": end["end_time"],
            "start_price": start["start_price"],
            "end_price": end["end_price"],
            "high": round(max(float(item["high"]) for item in members), 3),
            "low": round(min(float(item["low"]) for item in members), 3),
            "stroke_count": len(members),
            "confirmed": confirmed,
            "confirmed_time": (
                confirmed_bis[breaker]["confirmed_time"] if confirmed else None
            ),
            "status": "confirmed" if confirmed else "provisional",
            "basis": "confirmed_bi_structural_break",
        }

    while start_index + 2 < len(confirmed_bis):
        direction = confirmed_bis[start_index].get("direction")
        if direction not in {"up", "down"}:
            break
        candidate_index = start_index + 2
        if confirmed_bis[candidate_index].get("direction") != direction:
            break

        confirmed_at: int | None = None
        scan = candidate_index + 1
        while scan < len(confirmed_bis):
            item = confirmed_bis[scan]
            if item.get("direction") == direction:
                old_end = float(confirmed_bis[candidate_index]["end_price"])
                new_end = float(item["end_price"])
                if (direction == "up" and new_end >= old_end) or \
                        (direction == "down" and new_end <= old_end):
                    candidate_index = scan
            else:
                candidate = confirmed_bis[candidate_index]
                broken = (
                    direction == "up"
                    and float(item["end_price"]) < float(candidate["start_price"])
                ) or (
                    direction == "down"
                    and float(item["end_price"]) > float(candidate["start_price"])
                )
                if broken:
                    confirmed_at = scan
                    break
            scan += 1

        segments.append(make_segment(start_index, candidate_index, confirmed_at))
        if confirmed_at is None:
            break
        # The breaking stroke begins at the confirmed segment endpoint.
        start_index = candidate_index + 1

    return segments


def _trend_structure(segments: list[dict]) -> dict:
    """Classify the higher-order trend using same-direction confirmed segments."""
    confirmed = [
        item for item in segments or []
        if isinstance(item, dict) and item.get("confirmed") is True
    ]
    base = {
        "type": "insufficient",
        "label": "结构不足",
        "confirmed": False,
        "confirmed_time": None,
        "status": "provisional",
        "evidence_segment_count": len(confirmed),
        "basis": "confirmed_same_direction_segments",
    }
    if len(confirmed) < 3:
        return base

    previous, current = confirmed[-3], confirmed[-1]
    if previous.get("direction") != current.get("direction"):
        return base
    if float(current["high"]) > float(previous["high"]) \
            and float(current["low"]) > float(previous["low"]):
        trend_type, label = "uptrend", "上升趋势"
    elif float(current["high"]) < float(previous["high"]) \
            and float(current["low"]) < float(previous["low"]):
        trend_type, label = "downtrend", "下降趋势"
    else:
        trend_type, label = "consolidation", "震荡整理"
    return {
        **base,
        "type": trend_type,
        "label": label,
        "confirmed": True,
        "confirmed_time": current.get("confirmed_time"),
        "status": "confirmed",
    }


# ── 4. 中枢 ──────────────────────────────────────────────────────────
def _zhongshu_list(bis: list[dict]) -> list[dict]:
    """笔序列 → 中枢列表 [{zg,zd,gg,dd,start_time,end_time,start_bi,end_bi,
    enter_direction,leave_direction}]。"""
    out: list[dict] = []
    n = len(bis)
    i = 0
    while i + 2 < n:
        zg = min(bis[i]["high"], bis[i + 1]["high"], bis[i + 2]["high"])
        zd = max(bis[i]["low"], bis[i + 1]["low"], bis[i + 2]["low"])
        if zg <= zd:
            i += 1
            continue
        gg = max(bis[i]["high"], bis[i + 1]["high"], bis[i + 2]["high"])
        dd = min(bis[i]["low"], bis[i + 1]["low"], bis[i + 2]["low"])
        end = i + 2
        j = i + 3
        while j < n:
            b = bis[j]
            if b["low"] > zg or b["high"] < zd:  # 完全离开中枢区间
                break
            gg = max(gg, b["high"])
            dd = min(dd, b["low"])
            end = j
            j += 1
        leave = None
        if j < n:
            leave = "up" if bis[j]["low"] > zg else "down"
        # 中枢建立在可重绘笔之上；没有线段层/持久锁定状态时同样只能标为
        # provisional。leave_direction 表示当前已观察到离开，不等于不可重绘。
        confirmed = False
        confirmed_time = None
        out.append({
            "zg": round(zg, 3), "zd": round(zd, 3),
            "gg": round(gg, 3), "dd": round(dd, 3),
            "start_time": bis[i]["start_time"], "end_time": bis[end]["end_time"],
            "start_bi": i, "end_bi": end,
            "leave_bi": j if j < n else None,
            "enter_direction": bis[i]["direction"],
            "leave_direction": leave,
            "confirmed": confirmed, "confirmed_time": confirmed_time,
            "status": "confirmed" if confirmed else "provisional",
        })
        i = j
    return out


# ── 5. 买卖点（简化判定） ─────────────────────────────────────────────
def _macd_hist(df: pd.DataFrame) -> np.ndarray:
    close = df["close"].astype(float)
    dif = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    dea = dif.ewm(span=9, adjust=False).mean()
    return ((dif - dea) * 2).to_numpy()


def _attach_macd_area(bis: list[dict], merged: list[dict], hist: np.ndarray):
    """每笔叠加 MACD 柱面积（下跌笔取绿柱、上涨笔取红柱，用于背驰比较）。"""
    for b in bis:
        s = merged[b["start_pos"]]["idx"]
        e = merged[b["end_pos"]]["idx"]
        seg = hist[s:e + 1]
        if b["direction"] == "down":
            area = float(-seg[seg < 0].sum()) if (seg < 0).any() else 0.0
        else:
            area = float(seg[seg > 0].sum()) if (seg > 0).any() else 0.0
        b["macd_area"] = round(area, 4)


def _completed_zhongshu_before(
        zhongshus: list[dict], bi_index: int) -> tuple[int, dict] | None:
    """取 bi_index 之前最近一个当前已观察到离开的中枢，不回退到更老中枢。

    “已离开”只描述本次计算所见结构；中枢仍建立在可重绘笔上，不代表永久确认。
    """
    best: tuple[int, int, dict] | None = None
    for z_index, z in enumerate(zhongshus or []):
        if not isinstance(z, dict) or z.get("leave_direction") not in {"up", "down"}:
            continue
        try:
            end_bi = int(z["end_bi"])
            zd, zg = float(z["zd"]), float(z["zg"])
        except (KeyError, OverflowError, TypeError, ValueError):
            continue
        if end_bi >= bi_index or not (math.isfinite(zd) and math.isfinite(zg) and zd < zg):
            continue
        normalized_z = {**z, "end_bi": end_bi, "zd": zd, "zg": zg}
        candidate = (end_bi, z_index, normalized_z)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return (best[1], best[2]) if best is not None else None


def _signal_item(cur: dict, bi_index: int, signal_type: str, label: str,
                 price: float, description: str) -> dict:
    """统一信号发生点与可用时点，禁止把枢轴日期伪装成当时已知的信号日期。"""
    pivot_time = cur["end_time"]
    available_time = cur.get("end_confirm_time", pivot_time)
    return {
        "type": signal_type,
        "label": label,
        "labels": [label],
        "time": available_time,
        "pivot_time": pivot_time,
        "price": price,
        "description": description,
        "bi_index": bi_index,
        "confirmed": bool(cur.get("confirmed", False)),
        "confirmed_time": cur.get("confirmed_time"),
        "status": "confirmed" if cur.get("confirmed", False) else "provisional",
    }


def _coalesce_signals(signals: list[dict]) -> list[dict]:
    """同一笔同方向的多重身份合并为一个事件，保留所有原始标签。"""
    out: list[dict] = []
    by_key: dict[tuple[int, str], dict] = {}
    for signal in signals:
        key = (int(signal["bi_index"]), str(signal["type"]))
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = signal
            out.append(signal)
            continue
        label = signal["label"]
        if label in existing["labels"]:
            continue
        existing["labels"].append(label)
        existing["label"] = "+".join(existing["labels"])
        existing["description"] += f"；同时满足{label}：{signal['description']}"
        existing["confirmed"] = existing["confirmed"] and signal["confirmed"]
        existing["status"] = (
            "confirmed" if existing["confirmed"] else "provisional"
        )
        if existing.get("confirmed_time") is None:
            existing["confirmed_time"] = signal.get("confirmed_time")
    return out


def _signals(bis: list[dict], zhongshus: list[dict]) -> list[dict]:
    """扫描笔序列产出买卖点 [{type,label,time,price,description}]（简化判定）。

    1 买/卖只和当前最近已离开且同向的中枢绑定；3 买/卖仅允许发生在该
    中枢离开后的首个/次个笔位，防止多年后重新引用陈旧中枢。
    中枢、笔和由其派生的信号均为可重绘结构。
    """
    out: list[dict] = []
    n = len(bis)
    last_first_buy: tuple[int, dict] | None = None
    last_first_sell: tuple[int, dict] | None = None
    third_signalled: set[int] = set()

    for i in range(2, n):
        cur = bis[i]
        prev = bis[i - 1]
        prev_same = bis[i - 2]  # 笔序列已保证交替，因此这是同向上一笔
        completed = _completed_zhongshu_before(zhongshus, i)
        z_index, z = completed if completed is not None else (None, None)

        made_first_buy = False
        made_first_sell = False

        # ── 1买/1卖：当前最近已离开中枢之外的新极值 + MACD 面积背驰 ──
        if z is not None and z["leave_direction"] == "down" \
                and cur["direction"] == "down" and prev_same["direction"] == "down":
            if cur["low"] < z["zd"] and cur["low"] < prev_same["low"] \
                    and 0 < cur["macd_area"] < prev_same["macd_area"] * 0.85:
                signal = _signal_item(
                    cur, i, "buy", "1买", cur["low"],
                    f"底背驰（简化，绑定当前最近已离开中枢 ZD {z['zd']:.2f}，"
                    "结构可重绘）："
                    f"新低 {cur['low']:.2f}，MACD绿柱面积 "
                    f"{cur['macd_area']:.2f} < 前下跌笔 "
                    f"{prev_same['macd_area']:.2f}",
                )
                out.append(signal)
                last_first_buy = (i, signal)
                made_first_buy = True

        if z is not None and z["leave_direction"] == "up" \
                and cur["direction"] == "up" and prev_same["direction"] == "up":
            if cur["high"] > z["zg"] and cur["high"] > prev_same["high"] \
                    and 0 < cur["macd_area"] < prev_same["macd_area"] * 0.85:
                signal = _signal_item(
                    cur, i, "sell", "1卖", cur["high"],
                    f"顶背驰（简化，绑定当前最近已离开中枢 ZG {z['zg']:.2f}，"
                    "结构可重绘）："
                    f"新高 {cur['high']:.2f}，MACD红柱面积 "
                    f"{cur['macd_area']:.2f} < 前上涨笔 "
                    f"{prev_same['macd_area']:.2f}",
                )
                out.append(signal)
                last_first_sell = (i, signal)
                made_first_sell = True

        # ── 2买/2卖：对应 1买/卖后的第一次反向笔回抽 ──
        if not made_first_buy and last_first_buy is not None:
            first_i, first = last_first_buy
            if i == first_i + 2 and cur["direction"] == "down" \
                    and prev["direction"] == "up" and cur["low"] > first["price"]:
                out.append(_signal_item(
                    cur, i, "buy", "2买", cur["low"],
                    f"1买 {first['price']:.2f} 后首次回抽未破前低"
                    f"（{cur['low']:.2f}）",
                ))
            if i >= first_i + 2:
                last_first_buy = None

        if not made_first_sell and last_first_sell is not None:
            first_i, first = last_first_sell
            if i == first_i + 2 and cur["direction"] == "up" \
                    and prev["direction"] == "down" and cur["high"] < first["price"]:
                out.append(_signal_item(
                    cur, i, "sell", "2卖", cur["high"],
                    f"1卖 {first['price']:.2f} 后首次回抽未过前高"
                    f"（{cur['high']:.2f}）",
                ))
            if i >= first_i + 2:
                last_first_sell = None

        # ── 3买/3卖：核对离开方向，只接受紧邻中枢的首次回抽 ──
        if z is not None and z_index not in third_signalled:
            distance = i - int(z["end_bi"])
            if distance in {1, 2} and z["leave_direction"] == "up" \
                    and cur["direction"] == "down" and prev["direction"] == "up" \
                    and prev["end_price"] > z["zg"] and cur["low"] > z["zg"]:
                out.append(_signal_item(
                    cur, i, "buy", "3买", cur["low"],
                    f"向上突破中枢（ZG {z['zg']:.2f}）后首次回抽"
                    "未回中枢（简化判定）",
                ))
                third_signalled.add(z_index)
            elif distance in {1, 2} and z["leave_direction"] == "down" \
                    and cur["direction"] == "up" and prev["direction"] == "down" \
                    and prev["end_price"] < z["zd"] and cur["high"] < z["zd"]:
                out.append(_signal_item(
                    cur, i, "sell", "3卖", cur["high"],
                    f"向下跌破中枢（ZD {z['zd']:.2f}）后首次回抽"
                    "未回中枢（简化判定）",
                ))
                third_signalled.add(z_index)
    return _coalesce_signals(out)


def _active_bi(pivots: list[dict], merged: list[dict]) -> dict | None:
    """从最后一个分型端点到当前极值构造活动笔；不写入结构 bi 列表。"""
    if not pivots or not merged:
        return None
    start = pivots[-1]
    try:
        start_pos = int(start["pos"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return None
    if start_pos < 0 or start_pos + 1 >= len(merged):
        return None

    positions = range(start_pos + 1, len(merged))
    if start["type"] == "bottom":
        end_pos = max(positions, key=lambda pos: (merged[pos]["high"], pos))
        direction = "up"
        end_price = float(merged[end_pos]["high"])
        end_time = merged[end_pos].get("high_time", merged[end_pos]["time"])
    elif start["type"] == "top":
        end_pos = min(positions, key=lambda pos: (merged[pos]["low"], -pos))
        direction = "down"
        end_price = float(merged[end_pos]["low"])
        end_time = merged[end_pos].get("low_time", merged[end_pos]["time"])
    else:
        return None
    start_price = float(start["price"])
    if not (math.isfinite(start_price) and math.isfinite(end_price)):
        return None
    if (direction == "up" and end_price <= start_price) \
            or (direction == "down" and end_price >= start_price):
        return None
    return {
        "direction": direction,
        "start_time": start["time"],
        "end_time": end_time,
        "start_price": round(start_price, 3),
        "end_price": round(end_price, 3),
        "confirmed": False,
        "confirmed_time": None,
        "status": "provisional",
    }


# ── 主入口 ───────────────────────────────────────────────────────────
def _chan_content_hash(namespace: str, payload: Mapping) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"chan-{namespace}-{hashlib.sha256(encoded).hexdigest()[:20]}"


def _stable_bi_view(item: dict) -> dict:
    core = {
        "kind": "stable_bi",
        "direction": item["direction"],
        "startTime": _fmt_time(item["start_time"]),
        "endTime": _fmt_time(item["end_time"]),
        "startPrice": round(float(item["start_price"]), 6),
        "endPrice": round(float(item["end_price"]), 6),
        "high": round(float(item["high"]), 6),
        "low": round(float(item["low"]), 6),
        "confirmedTime": (
            _fmt_time(item["confirmed_time"])
            if item.get("confirmed_time") is not None else None
        ),
        "basis": item["basis"],
    }
    confirmed = bool(item["confirmed"])
    return {
        **core,
        "structureId": _chan_content_hash("stable-bi", core),
        "endpointConfirmedTime": _fmt_time(item["end_confirm_time"]),
        "validationBasis": "causal_opposite_fractal_lock",
        "validated": confirmed,
        "confirmed": confirmed,
        "status": item["status"],
    }


def _segment_view(item: dict) -> dict:
    core = {
        "kind": "segment",
        "direction": item["direction"],
        "startTime": _fmt_time(item["start_time"]),
        "endTime": _fmt_time(item["end_time"]),
        "startPrice": round(float(item["start_price"]), 6),
        "endPrice": round(float(item["end_price"]), 6),
        "high": round(float(item["high"]), 6),
        "low": round(float(item["low"]), 6),
        "strokeCount": int(item["stroke_count"]),
        "confirmedTime": (
            _fmt_time(item["confirmed_time"])
            if item.get("confirmed_time") is not None else None
        ),
        "basis": item["basis"],
    }
    confirmed = bool(item["confirmed"])
    return {
        **core,
        "structureId": _chan_content_hash("segment", core),
        "validationBasis": "confirmed_stroke_break_segment",
        "validated": confirmed,
        "confirmed": confirmed,
        "status": item["status"],
    }


def _same_level_decomposition(
    segments: list[dict], max_levels: int = 4
) -> list[dict]:
    """Recursively decompose confirmed alternating structures by level."""

    current = [
        item
        for item in segments
        if bool(item.get("confirmed")) and item.get("confirmed_time") is not None
    ]
    result: list[dict] = []
    for level in range(1, max(0, int(max_levels)) + 1):
        next_level: list[dict] = []
        for first, middle, last in zip(current, current[1:], current[2:]):
            direction = first["direction"]
            if direction != last["direction"] or middle["direction"] == direction:
                continue
            extends = (
                float(last["end_price"]) > float(first["end_price"])
                if direction == "up"
                else float(last["end_price"]) < float(first["end_price"])
            )
            if not extends:
                continue
            children = [first, middle, last]
            child_ids = []
            for child in children:
                if child.get("kind") == "same_level":
                    child_ids.append(_same_level_view(child)["structureId"])
                else:
                    child_ids.append(_segment_view(child)["structureId"])
            next_level.append({
                "kind": "same_level",
                "type": "same_level_trend_leg",
                "level": level,
                "direction": direction,
                "start_time": first["start_time"],
                "end_time": last["end_time"],
                "start_price": first["start_price"],
                "end_price": last["end_price"],
                "high": max(float(item["high"]) for item in children),
                "low": min(float(item["low"]) for item in children),
                "segment_count": sum(
                    int(item.get("segment_count", 1)) for item in children
                ),
                "child_structure_ids": child_ids,
                "confirmed_time": last["confirmed_time"],
                "basis": f"three_confirmed_alternating_level_{level - 1}_structures",
                "confirmed": True,
                "status": "confirmed",
            })
        if not next_level:
            break
        result.extend(next_level)
        current = next_level
    return result


def _same_level_view(item: dict) -> dict:
    core = {
        "kind": "same_level",
        "type": item["type"],
        "level": int(item["level"]),
        "direction": item["direction"],
        "startTime": _fmt_time(item["start_time"]),
        "endTime": _fmt_time(item["end_time"]),
        "startPrice": round(float(item["start_price"]), 6),
        "endPrice": round(float(item["end_price"]), 6),
        "high": round(float(item["high"]), 6),
        "low": round(float(item["low"]), 6),
        "segmentCount": int(item["segment_count"]),
        "confirmedTime": _fmt_time(item["confirmed_time"]),
        "basis": item["basis"],
        "childStructureIds": list(item.get("child_structure_ids") or []),
    }
    return {
        **core,
        "structureId": _chan_content_hash("same-level", core),
        "validationBasis": "confirmed_same_level_three_segment_extension",
        "validated": True,
        "confirmed": True,
        "status": "confirmed",
    }


def _verified_prior_state(prior_state: Mapping, state_key: str) -> bool:
    if (
        prior_state.get("version") != "chan-state-1"
        or prior_state.get("methodVersion") != "chan-local-3"
        or prior_state.get("stateKey") != state_key
    ):
        return False
    supplied = prior_state.get("contentHash")
    if not isinstance(supplied, str):
        return False
    body = {key: value for key, value in prior_state.items() if key != "contentHash"}
    return supplied == _chan_content_hash("state", body)


def _state_items(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
        and isinstance(item.get("structureId"), str)
        and item.get("confirmed") is True
        and item.get("validated") is True
    ]


def _merge_confirmed_stream(previous: list[dict], current: list[dict]) -> tuple[list[dict], bool]:
    previous_anchors = {
        (
            item.get("kind"), item.get("direction"),
            item.get("startTime"), item.get("endTime"),
        ): item.get("structureId")
        for item in previous
    }
    for item in current:
        anchor = (
            item.get("kind"), item.get("direction"),
            item.get("startTime"), item.get("endTime"),
        )
        old_id = previous_anchors.get(anchor)
        if old_id is not None and old_id != item.get("structureId"):
            return current, False
    merged = {item["structureId"]: item for item in previous}
    merged.update({item["structureId"]: item for item in current})
    return sorted(
        merged.values(),
        key=lambda item: (
            str(item.get("confirmedTime") or ""),
            str(item.get("startTime") or ""),
            str(item["structureId"]),
        ),
    ), True


def _build_chan_state(
    *,
    stable_bis: list[dict],
    segments: list[dict],
    same_level: list[dict],
    origin_time: str | None,
    last_input_time: str | None,
    state_key: str | None,
    prior_state: Mapping | None,
) -> dict:
    key = state_key.strip() if isinstance(state_key, str) and state_key.strip() else None
    current = {
        "stableBi": [item for item in stable_bis if item["validated"]],
        "segment": [item for item in segments if item["validated"]],
        "sameLevel": [item for item in same_level if item["validated"]],
    }
    continuity = "unbound"
    prefix_stable = False
    effective_origin = origin_time
    if key is not None:
        continuity = "initialized"
        prefix_stable = True
        if prior_state is not None:
            if _verified_prior_state(prior_state, key):
                prior_streams = prior_state.get("confirmedStreams")
                prior_streams = prior_streams if isinstance(prior_streams, Mapping) else {}
                merged: dict[str, list[dict]] = {}
                valid = True
                for name, values in current.items():
                    merged[name], stream_valid = _merge_confirmed_stream(
                        _state_items(prior_streams.get(name)), values
                    )
                    valid = valid and stream_valid
                if valid:
                    current = merged
                    continuity = "continued"
                    effective_origin = str(prior_state.get("originTime") or origin_time)
                else:
                    continuity = "conflict_prior_rejected"
                    prefix_stable = False
            else:
                continuity = "invalid_prior_rejected"
                prefix_stable = False
    body = {
        "version": "chan-state-1",
        "methodVersion": "chan-local-3",
        "reliabilityVersion": "chan-reliability-1",
        "stateKey": key,
        "originTime": effective_origin,
        "lastInputTime": last_input_time,
        "continuityStatus": continuity,
        "prefixStable": prefix_stable,
        "persistenceEligible": key is not None,
        "confirmedStreams": current,
    }
    return {**body, "contentHash": _chan_content_hash("state", body)}


def analyze_chan(
    df: pd.DataFrame,
    lookback: int = 600,
    *,
    prior_state: Mapping | None = None,
    state_key: str | None = None,
) -> dict:
    """缠论分析主入口。输入小写 ohlcv + DatetimeIndex；返回契约 ChanAnalysis 结构。

    lookback: 只用最近 N 根 K 线计算（缠论关心近端结构，且控制开销）。
    """
    empty = {
        "methodVersion": "chan-local-3", "biMode": "old", "signalMode": "simplified",
        "reliabilityVersion": "chan-reliability-1",
        "trend": "数据不足", "biCount": 0, "zhongshuCount": 0, "mergedBars": 0,
        "stableBiCount": 0, "segmentCount": 0, "sameLevelCount": 0,
        "lastBi": None, "activeBi": None, "positionVsZhongshu": "none",
        "description": "数据不足，无法进行缠论分析",
        "fenxing": [], "bi": [], "stableBi": [], "segment": [], "sameLevel": [],
        "zhongshu": [], "signals": [],
        "trendStructure": {
            "type": "insufficient", "label": "结构不足",
            "basis": "confirmed_same_direction_segments",
            "evidenceSegmentCount": 0, "confirmed": False,
            "confirmedTime": None, "status": "provisional",
        },
    }
    empty["state"] = _build_chan_state(
        stable_bis=[], segments=[], same_level=[], origin_time=None,
        last_input_time=None, state_key=state_key, prior_state=None,
    )
    if not isinstance(df, pd.DataFrame) or len(df) < 30:
        return empty
    # 防御：live 数据可能出现重复列名（yfinance 多层列降级），去重后取首列
    df = df.loc[:, ~df.columns.duplicated()].copy()
    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        return empty
    try:
        lookback = int(lookback)
    except (OverflowError, TypeError, ValueError):
        return empty
    if lookback <= 0:
        return empty

    # 主入口也可被 API 之外直接调用：统一行序、重复日期和非法 OHLC，避免
    # 私有步骤因脏输入抛错或使用倒序行情。
    try:
        df = df.sort_index()
        df = df.loc[~df.index.duplicated(keep="last")]
        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        finite = np.isfinite(df[["high", "low", "close"]].to_numpy(dtype=float)).all(axis=1)
        valid_ohlc = (df["high"] >= df["low"]) \
            & (df["close"] >= df["low"]) & (df["close"] <= df["high"])
        df = df.loc[finite & valid_ohlc]
    except (KeyError, TypeError, ValueError):
        return empty
    if len(df) < 30:
        return empty

    seg = df.tail(lookback)
    if len(seg) < 30:
        return empty
    merged = _merge_bars(seg)
    if len(merged) < 10:
        return empty
    fractals = _fractals(merged)
    pivots, bis = _bi_list(fractals)
    stable_bis = _stable_bi_list(fractals)
    segments = _segment_list(stable_bis)
    same_level = _same_level_decomposition(segments)
    trend_structure = _trend_structure(segments)
    zhongshus = _zhongshu_list(bis)
    active_bi = _active_bi(pivots, merged)
    if bis:
        _attach_macd_area(bis, merged, _macd_hist(seg))
    signals = _signals(bis, zhongshus)
    stable_bi_views = [_stable_bi_view(item) for item in stable_bis]
    segment_views = [_segment_view(item) for item in segments]
    same_level_views = [_same_level_view(item) for item in same_level]
    state = _build_chan_state(
        stable_bis=stable_bi_views,
        segments=segment_views,
        same_level=same_level_views,
        origin_time=_fmt_time(seg.index[0]),
        last_input_time=_fmt_time(seg.index[-1]),
        state_key=state_key,
        prior_state=prior_state,
    )

    # ── 当前状态 ──
    last_bi = bis[-1] if bis else None
    last_z = zhongshus[-1] if zhongshus else None
    cur_price = float(seg["close"].iloc[-1])
    pos_vs_z = "none"
    if last_z is not None:
        if cur_price > last_z["zg"]:
            pos_vs_z = "above"
        elif cur_price < last_z["zd"]:
            pos_vs_z = "below"
        else:
            pos_vs_z = "inside"
    trend = "震荡"
    if len(zhongshus) >= 2:
        a, b = zhongshus[-2], zhongshus[-1]
        if b["zg"] > a["zg"] and b["zd"] > a["zd"]:
            trend = "上升（中枢上移）"
        elif b["zg"] < a["zg"] and b["zd"] < a["zd"]:
            trend = "下跌（中枢下移）"
    elif active_bi is not None:
        trend = "上涨笔（未确认）" if active_bi["direction"] == "up" else "下跌笔（未确认）"
    elif last_bi is not None:
        trend = "上涨笔（可重绘）" if last_bi["direction"] == "up" else "下跌笔（可重绘）"

    pos_txt = {"above": "中枢上方", "below": "中枢下方", "inside": "中枢内部",
               "none": "暂无中枢"}[pos_vs_z]
    desc_parts = [f"合并K {len(merged)} 根，{len(fractals)} 个分型，{len(bis)} 笔，"
                  f"{len(zhongshus)} 个中枢"]
    if last_bi:
        desc_parts.append(
            f"最近结构{'上涨' if last_bi['direction'] == 'up' else '下跌'}笔"
            "（端点已确认，整体仍可重绘）"
        )
    if active_bi:
        desc_parts.append(
            f"当前形成中{'上涨' if active_bi['direction'] == 'up' else '下跌'}笔（未确认）")
    desc_parts.append(f"现价位于{pos_txt}")
    if last_z:
        desc_parts.append(f"最近中枢 [{last_z['zd']:.2f} ~ {last_z['zg']:.2f}]"
                          f"（{_fmt_time(last_z['start_time'])} ~ {_fmt_time(last_z['end_time'])}）")
    if signals:
        s = signals[-1]
        desc_parts.append(f"最近信号：{s['label']}（{str(s['time'])[:10]} @ {s['price']:.2f}）")

    return {
        "methodVersion": "chan-local-3",
        "biMode": "old",
        "signalMode": "simplified",
        "reliabilityVersion": "chan-reliability-1",
        "trend": trend,
        "biCount": len(bis),
        "stableBiCount": len(stable_bis),
        "segmentCount": len(segments),
        "sameLevelCount": len(same_level_views),
        "zhongshuCount": len(zhongshus),
        "mergedBars": len(merged),
        "lastBi": ({
            "direction": last_bi["direction"],
            "startTime": _fmt_time(last_bi["start_time"]),
            "endTime": _fmt_time(last_bi["end_time"]),
            "startPrice": last_bi["start_price"],
            "endPrice": last_bi["end_price"],
            "endpointConfirmedTime": _fmt_time(last_bi["end_confirm_time"]),
            "confirmed": bool(last_bi["confirmed"]),
            "status": last_bi.get("status", "provisional"),
            "confirmedTime": (
                _fmt_time(last_bi["confirmed_time"])
                if last_bi.get("confirmed_time") is not None else None
            ),
        } if last_bi else None),
        "activeBi": ({
            "direction": active_bi["direction"],
            "startTime": _fmt_time(active_bi["start_time"]),
            "endTime": _fmt_time(active_bi["end_time"]),
            "startPrice": active_bi["start_price"],
            "endPrice": active_bi["end_price"],
            "confirmed": False,
            "confirmedTime": None,
            "status": "provisional",
        } if active_bi else None),
        "positionVsZhongshu": pos_vs_z,
        "description": "；".join(desc_parts) + "。（本地缠论引擎，简化判定口径）",
        "fenxing": [{
            "time": _fmt_time(f["time"]), "type": f["type"],
            "price": round(float(f["price"]), 3),
            "confirmed": True,
            "status": "confirmed",
            "confirmedTime": _fmt_time(f["confirmed_time"]),
        } for f in fractals[-120:]],
        "bi": [{
            "direction": b["direction"],
            "startTime": _fmt_time(b["start_time"]), "endTime": _fmt_time(b["end_time"]),
            "startPrice": b["start_price"], "endPrice": b["end_price"],
            "high": b["high"], "low": b["low"], "macdArea": b.get("macd_area"),
            "endpointConfirmedTime": _fmt_time(b["end_confirm_time"]),
            "confirmed": bool(b["confirmed"]),
            "status": b.get("status", "provisional"),
            "confirmedTime": (
                _fmt_time(b["confirmed_time"])
                if b.get("confirmed_time") is not None else None
            ),
        } for b in bis[-120:]],
        "stableBi": stable_bi_views[-120:],
        "segment": segment_views[-40:],
        "sameLevel": same_level_views[-40:],
        "state": state,
        "trendStructure": {
            "type": trend_structure["type"],
            "label": trend_structure["label"],
            "basis": trend_structure["basis"],
            "evidenceSegmentCount": trend_structure["evidence_segment_count"],
            "confirmed": bool(trend_structure["confirmed"]),
            "confirmedTime": (
                _fmt_time(trend_structure["confirmed_time"])
                if trend_structure.get("confirmed_time") is not None else None
            ),
            "status": trend_structure["status"],
        },
        "zhongshu": [{
            "zg": z["zg"], "zd": z["zd"], "gg": z["gg"], "dd": z["dd"],
            "startTime": _fmt_time(z["start_time"]), "endTime": _fmt_time(z["end_time"]),
            "enterDirection": z["enter_direction"], "leaveDirection": z["leave_direction"],
            "confirmed": bool(z["confirmed"]),
            "status": z.get("status", "provisional"),
            "confirmedTime": (
                _fmt_time(z["confirmed_time"])
                if z.get("confirmed_time") is not None else None
            ),
        } for z in zhongshus[-20:]],
        "signals": [{
            "type": s["type"], "label": s["label"], "time": _fmt_time(s["time"]),
            "labels": list(s["labels"]),
            "pivotTime": _fmt_time(s["pivot_time"]),
            "price": round(float(s["price"]), 3), "description": s["description"],
            "confirmed": bool(s["confirmed"]),
            "status": s.get("status", "provisional"),
            "confirmedTime": (
                _fmt_time(s["confirmed_time"])
                if s.get("confirmed_time") is not None else None
            ),
        } for s in signals[-12:]],
    }
