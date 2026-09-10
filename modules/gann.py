"""
gann.py — 江恩理论价位引擎：八分位 + 甘氏扇(角度线) + 时空周期。

产出两类东西：
  1) 支撑压力**价位**（gann_sr_levels）→ 统一格式，供“精确交易建议(①)”聚合；
  2) 副图所需的**线序列/时间标记**（compute_gann 里的 fan_series / time_cycles）→ 供江恩副图渲染。

理论说明与工程近似（江恩有主观性，这里做可复现的工程化实现）：
  • 八分位：把一段显著区间(近 lookback 的最高—最低)按 1/8 等分，并含 1/3、2/3。
    1/2、3/8~5/8 视为强支撑压力区，1/8、7/8 为极端区。
  • 甘氏扇(角度线)：自最近显著枢轴(高或低)按“价格/时间”比率画线：
    1x1(最重要)、1x2/2x1、1x3/3x1、1x4/4x1、1x8/8x1。
    关键在“单位价格/根K”——本实现取近 lookback 的 ATR 近似(可调)，使 1x1 斜率有意义。
  • 时空周期：自枢轴起的江恩数(30/45/60/90/120/144/180/270/360 根)为潜在变盘时间。
"""

from typing import Dict, List, Optional
import numpy as np
import pandas as pd

# 八分位比率（含 1/3、2/3）
GANN_OCTAVES = [0.0, 1/8, 1/4, 1/3, 3/8, 1/2, 5/8, 2/3, 3/4, 7/8, 1.0]
# 甘氏扇角度：price/time 比率
GANN_ANGLES = {"1x8": 1/8, "1x4": 1/4, "1x3": 1/3, "1x2": 1/2,
               "1x1": 1.0, "2x1": 2.0, "3x1": 3.0, "4x1": 4.0, "8x1": 8.0}
# 时空周期（根K）
GANN_CYCLES = [30, 45, 60, 90, 120, 144, 180, 270, 360]

_OCTAVE_LABELS = {0.0: "0/8(底)", 1/8: "1/8", 1/4: "2/8(1/4)", 1/3: "1/3", 3/8: "3/8",
                  1/2: "4/8(1/2)", 5/8: "5/8", 2/3: "2/3", 3/4: "6/8(3/4)", 7/8: "7/8", 1.0: "8/8(顶)"}
# 强区（更重要）：1/2、3/8、5/8、1/3、2/3
_STRONG = {1/3, 3/8, 1/2, 5/8, 2/3}


# =========================================================================
# 八分位
# =========================================================================

def gann_octaves(high: float, low: float) -> List[Dict]:
    """区间 [low, high] 的八分位价位（含 1/3、2/3）。返回 [{ratio,label,price,strong}]。"""
    rng = float(high) - float(low)
    if rng <= 0:
        return []
    out = []
    for r in GANN_OCTAVES:
        out.append({
            "ratio": r,
            "label": "江恩" + _OCTAVE_LABELS.get(r, f"{r:.3f}"),
            "price": round(float(low) + rng * r, 3),
            "strong": r in _STRONG,
        })
    return out


# =========================================================================
# 甘氏扇（角度线）
# =========================================================================

def gann_fan(n_bars: int, pivot_pos: int, pivot_price: float, unit: float,
             direction: str = "up") -> Dict[str, np.ndarray]:
    """自枢轴按 price/bar=unit 画各角度线。返回 {angle_name: price_series(len=n_bars)}。

    direction="up" 自低点上扬(支撑扇)；"down" 自高点下压(压力扇)。
    """
    bars = np.arange(n_bars) - pivot_pos
    sign = 1.0 if direction == "up" else -1.0
    lines = {}
    for name, ratio in GANN_ANGLES.items():
        lines[name] = pivot_price + sign * unit * ratio * bars
    return lines


def gann_fan_current_levels(lines: Dict[str, np.ndarray], current_price: float) -> List[Dict]:
    """取各角度线在最新一根的价位，作为“当前动态支撑压力”。返回 [{label,price,type}]。"""
    out = []
    for name, series in lines.items():
        price = float(series[-1])
        if not np.isfinite(price) or price <= 0:
            continue
        out.append({
            "label": f"江恩{name}角度线",
            "price": round(price, 3),
            "type": "support" if price <= current_price else "resistance",
        })
    return out


# =========================================================================
# 时空周期
# =========================================================================

def gann_time_cycles(n_bars: int, pivot_pos: int, index: Optional[pd.Index] = None) -> List[Dict]:
    """自枢轴起的江恩时间周期。返回 [{cycle,pos,date/future_bars}]。"""
    out = []
    last = n_bars - 1
    for c in GANN_CYCLES:
        pos = pivot_pos + c
        item = {"cycle": c, "pos": pos}
        if pos <= last:
            item["date"] = str(index[pos]) if index is not None else None
            item["future_bars"] = 0
        else:
            item["date"] = None
            item["future_bars"] = pos - last   # 距今还有多少根到该周期
        out.append(item)
    return out


# =========================================================================
# 枢轴 & 单位
# =========================================================================

def find_major_pivot(df: pd.DataFrame, lookback: int = 120):
    """近 lookback 内最显著的摆动高/低作为江恩起点。

    取“当前这条腿的起点”作枢轴（较早的那个极值），使甘氏扇/时空周期有可绘制的向后区间：
      低点在前(低→高，上升腿) → 自低点画上扬扇，direction="up"；
      高点在前(高→低，下降腿) → 自高点画下压扇，direction="down"。
    返回 (pivot_pos, pivot_price, direction)。
    """
    seg = df.tail(lookback)
    base = len(df) - len(seg)
    hi_lbl = seg["High"].idxmax()
    lo_lbl = seg["Low"].idxmin()
    hi_pos = base + seg.index.get_loc(hi_lbl)
    lo_pos = base + seg.index.get_loc(lo_lbl)
    if lo_pos <= hi_pos:
        return lo_pos, float(seg["Low"].min()), "up"
    return hi_pos, float(seg["High"].max()), "down"


def _price_unit(df: pd.DataFrame, lookback: int = 120) -> float:
    """甘氏扇的 price/bar 单位：优先近 lookback 的 ATR 均值，退回区间/根数。"""
    seg = df.tail(lookback)
    if "ATR" in seg.columns and seg["ATR"].notna().any():
        u = float(seg["ATR"].dropna().mean())
        if u > 0:
            return u
    h = float(seg["High"].max()); l = float(seg["Low"].min())
    return max((h - l) / max(len(seg), 1), 1e-6)


# =========================================================================
# 综合
# =========================================================================

def compute_gann(df: pd.DataFrame, lookback: int = 120) -> Dict:
    """江恩综合：八分位 + 甘氏扇 + 时空周期。df 需含 High/Low/Close(可选 ATR)。"""
    if df is None or len(df) < 10:
        return {}
    seg = df.tail(lookback)
    high = float(seg["High"].max())
    low = float(seg["Low"].min())
    current = float(df["Close"].iloc[-1])

    octaves = gann_octaves(high, low)
    pivot_pos, pivot_price, direction = find_major_pivot(df, lookback)
    unit = _price_unit(df, lookback)
    fan = gann_fan(len(df), pivot_pos, pivot_price, unit, direction)
    fan_levels = gann_fan_current_levels(fan, current)
    cycles = gann_time_cycles(len(df), pivot_pos, df.index)

    return {
        "range": {"high": round(high, 3), "low": round(low, 3), "lookback": lookback},
        "octaves": octaves,
        "pivot": {"pos": int(pivot_pos), "price": round(pivot_price, 3),
                  "direction": direction, "date": str(df.index[pivot_pos])},
        "unit": round(unit, 4),
        "fan_series": {k: v.tolist() for k, v in fan.items()},   # 副图用
        "fan_levels": fan_levels,                                 # 当前动态 S/R
        "time_cycles": cycles,
        "current_price": round(current, 3),
    }


def gann_sr_levels(gann_result: Dict, current_price: float,
                   include_weak: bool = False) -> List[Dict]:
    """把江恩八分位 + 扇线当前值 → 统一支撑压力格式 [{price, method, type, source}]。

    默认只取强八分位 + 全部扇线当前值；include_weak=True 时纳入全部八分位。
    """
    if not gann_result:
        return []
    levels = []
    for o in gann_result.get("octaves", []):
        if not include_weak and not o["strong"]:
            continue
        levels.append({"price": o["price"], "method": o["label"], "source": "gann_octave",
                       "type": "support" if o["price"] <= current_price else "resistance"})
    for f in gann_result.get("fan_levels", []):
        levels.append({"price": f["price"], "method": f["label"], "source": "gann_fan",
                       "type": f["type"]})
    # 去重(价位相近合并)
    return _dedup_levels(levels)


def _dedup_levels(levels: List[Dict], tol_pct: float = 0.003) -> List[Dict]:
    """价位相近(默认0.3%)的合并，method 拼接（体现“多重汇合”）。"""
    levels = sorted(levels, key=lambda x: x["price"])
    merged: List[Dict] = []
    for lv in levels:
        if merged and abs(lv["price"] - merged[-1]["price"]) / max(merged[-1]["price"], 1e-6) <= tol_pct:
            merged[-1]["method"] += " + " + lv["method"]
            merged[-1]["confluence"] = merged[-1].get("confluence", 1) + 1
        else:
            lv = dict(lv); lv["confluence"] = 1
            merged.append(lv)
    return merged
