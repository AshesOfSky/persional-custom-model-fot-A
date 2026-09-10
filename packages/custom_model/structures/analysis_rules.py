"""规则化分析 — 无 live 时基于指标生成评分/信号/支撑压力/买入计划。

评分（满分 100）：EMA 隧道 30 + MACD 20 + RSI 15 + BOLL 15 + 量能趋势 20。
支撑/压力：近 120 日摆动高低点聚类 + EMA55/EMA144、BOLL 上下轨候选。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind


def _f(x, nd=2):
    try:
        v = float(x)
        return round(v, nd) if np.isfinite(v) else None
    except Exception:  # noqa: BLE001
        return None


# ── 评分 ──────────────────────────────────────────────────────────────
def _score(df: pd.DataFrame, m: dict) -> tuple[int, list[str]]:
    close = df["close"]
    c = float(close.iloc[-1])
    notes: list[str] = []
    s = 0

    # EMA 隧道（30）
    e8, e21, e55, e144 = (float(m[k].iloc[-1]) for k in ("ema8", "ema21", "ema55", "ema144"))
    if c > e21:
        s += 8
    if e8 > e21:
        s += 8
    if len(close) > 5 and float(m["ema21"].iloc[-1]) > float(m["ema21"].iloc[-6]):
        s += 7
    if c > e55:
        s += 4
    if e55 > e144:
        s += 3
    if c > e21 and e8 > e21:
        notes.append("价格站上 EMA21 且 EMA8>EMA21，短周期多头排列")

    # MACD（20）
    dif, dea, hist = float(m["dif"].iloc[-1]), float(m["dea"].iloc[-1]), float(m["hist"].iloc[-1])
    if dif > dea:
        s += 8
    if dif > 0:
        s += 6
    if len(close) > 3 and hist > float(m["hist"].iloc[-2]) > float(m["hist"].iloc[-3]):
        s += 6
        notes.append("MACD 红柱连续放大，动能增强")

    # RSI（15）
    r = float(m["rsi14"].iloc[-1])
    if 45 <= r <= 65:
        s += 15
    elif 30 <= r < 45 or 65 < r <= 75:
        s += 8
    else:
        s += 2
    if r >= 80:
        notes.append(f"RSI14={r:.0f} 超买区，注意回撤风险")
    elif r <= 25:
        notes.append(f"RSI14={r:.0f} 超卖区，存在反弹需求")

    # BOLL（15）
    mid, up = float(m["boll_mid"].iloc[-1]), float(m["boll_upper"].iloc[-1])
    if c > mid:
        s += 8
    if c < up:
        s += 7
    else:
        notes.append("收盘价突破 BOLL 上轨，短线偏热")

    # 量能/趋势（20）
    vol = df["volume"]
    if len(vol) >= 20:
        if float(vol.iloc[-5:].mean()) > float(vol.iloc[-20:].mean()):
            s += 8
            notes.append("5 日均量大于 20 日均量，量能温和放大")
        g20 = c / float(close.iloc[-21]) - 1 if len(close) > 21 else 0.0
        if g20 > 0:
            s += 6
        g60 = c / float(close.iloc[-61]) - 1 if len(close) > 61 else 0.0
        if g60 > 0:
            s += 6
    return min(100, s), notes


def _rating(score: int) -> str:
    if score >= 75:
        return "强烈关注"
    if score >= 60:
        return "积极关注"
    if score >= 45:
        return "中性偏多"
    if score >= 30:
        return "中性偏空"
    return "建议回避"


def _trend_items(df: pd.DataFrame, m: dict) -> list[dict]:
    close = df["close"]
    c = float(close.iloc[-1])
    e8, e21, e55, e144 = (float(m[k].iloc[-1]) for k in ("ema8", "ema21", "ema55", "ema144"))

    def d_up(cond):
        return "up" if cond else "down"

    short_up = c > e8 and e8 > e21
    mid_slope = len(close) > 5 and float(m["ema21"].iloc[-1]) > float(m["ema21"].iloc[-6])
    long_up = e55 > e144
    vol = df["volume"]
    vol_up = len(vol) >= 20 and float(vol.iloc[-5:].mean()) > float(vol.iloc[-20:].mean())
    return [
        {"label": "短期趋势", "value": "多头" if short_up else "走弱", "direction": d_up(short_up)},
        {"label": "中期趋势", "value": "向上" if (c > e21 and mid_slope) else "震荡/向下",
         "direction": "up" if (c > e21 and mid_slope) else ("flat" if c > e21 else "down")},
        {"label": "长期趋势", "value": "多头" if long_up else "空头", "direction": d_up(long_up)},
        {"label": "量能趋势", "value": "放量" if vol_up else "缩量", "direction": d_up(vol_up)},
    ]


# ── 支撑 / 压力 ───────────────────────────────────────────────────────
def _swing_levels(df: pd.DataFrame, k: int = 3, window: int = 120):
    """摆动高低点聚类：近 window 根、±k 局部极值，1.5% 内归并。"""
    tail = df.tail(window)
    h, l = tail["high"].to_numpy(), tail["low"].to_numpy()
    idx = tail.index
    swings: list[tuple[float, str, pd.Timestamp]] = []
    for i in range(k, len(tail) - k):
        if h[i] >= h[i - k:i + k + 1].max():
            swings.append((float(h[i]), "resistance", idx[i]))
        if l[i] <= l[i - k:i + k + 1].min():
            swings.append((float(l[i]), "support", idx[i]))
    # 按价格聚类（相邻价差 ≤1.5% 归并）
    clusters: dict[str, list[list[tuple[float, pd.Timestamp]]]] = {"support": [], "resistance": []}
    for price, kind, ts in sorted(swings, key=lambda x: x[0]):
        groups = clusters[kind]
        if groups and abs(price - np.mean([p for p, _ in groups[-1]])) / max(price, 1e-9) <= 0.015:
            groups[-1].append((price, ts))
        else:
            groups.append([(price, ts)])
    levels: list[dict] = []
    last_ts = idx[-1]
    for kind, groups in clusters.items():
        for g in groups:
            prices = [p for p, _ in g]
            recent = any((last_ts - ts).days <= 15 for _, ts in g)
            strength = min(5, len(g) + (1 if recent else 0) + (1 if len(g) >= 3 else 0))
            levels.append({
                "price": round(float(np.mean(prices)), 2),
                "type": kind,
                "strength": int(max(1, strength)),
                "sources": f"{len(g)} 次摆动{'高点' if kind == 'resistance' else '低点'}",
            })
    return levels


def _sr_levels(df: pd.DataFrame, m: dict) -> tuple[list[dict], list[dict]]:
    cur = float(df["close"].iloc[-1])
    cands = _swing_levels(df)
    # 均线 / BOLL 候选
    for key, label in (("ema55", "EMA55 均线"), ("ema144", "EMA144 均线"),
                       ("boll_lower", "BOLL 下轨"), ("boll_upper", "BOLL 上轨")):
        v = _f(m[key].iloc[-1])
        if v:
            cands.append({"price": v, "type": "support" if v <= cur else "resistance",
                          "strength": 2, "sources": label})
    # 去重（±1% 视为同一水平，保留强度更高者）
    cands.sort(key=lambda x: (x["price"], -x["strength"]))
    merged: list[dict] = []
    for lv in cands:
        if merged and abs(lv["price"] - merged[-1]["price"]) / max(lv["price"], 1e-9) <= 0.01 \
                and merged[-1]["type"] == lv["type"]:
            if lv["strength"] > merged[-1]["strength"]:
                merged[-1] = lv
            elif lv["sources"] not in merged[-1]["sources"]:
                merged[-1]["sources"] += f" + {lv['sources']}"
        else:
            merged.append(dict(lv))
    supports, resistances = [], []
    for lv in merged:
        lv["distancePct"] = round((lv["price"] - cur) / cur * 100, 2)
        if lv["type"] == "support" and lv["price"] <= cur * 1.005:
            supports.append(lv)
        elif lv["type"] == "resistance" and lv["price"] >= cur * 0.995:
            resistances.append(lv)
    supports.sort(key=lambda x: -x["price"])
    resistances.sort(key=lambda x: x["price"])
    return supports[:3], resistances[:3]


# ── 信号 ──────────────────────────────────────────────────────────────
def _signals(df: pd.DataFrame, m: dict, lookback: int = 60) -> list[dict]:
    close = df["close"]
    out: list[dict] = []
    n = len(df)
    start = max(1, n - lookback)
    e8, e21 = m["ema8"].to_numpy(), m["ema21"].to_numpy()
    dif, dea = m["dif"].to_numpy(), m["dea"].to_numpy()
    r = m["rsi14"].to_numpy()
    mid = m["boll_mid"].to_numpy()
    up, low_ = m["boll_upper"].to_numpy(), m["boll_lower"].to_numpy()
    c = close.to_numpy()
    for i in range(start, n):
        ts = close.index[i].strftime("%Y-%m-%d")
        price = round(float(c[i]), 2)
        if e8[i - 1] <= e21[i - 1] and e8[i] > e21[i]:
            out.append({"time": ts, "kind": "buy", "text": "EMA8 上穿 EMA21（金叉）", "price": price})
        if e8[i - 1] >= e21[i - 1] and e8[i] < e21[i]:
            out.append({"time": ts, "kind": "sell", "text": "EMA8 下穿 EMA21（死叉）", "price": price})
        if dif[i - 1] <= dea[i - 1] and dif[i] > dea[i]:
            out.append({"time": ts, "kind": "buy", "text": "MACD DIF 上穿 DEA", "price": price})
        if dif[i - 1] >= dea[i - 1] and dif[i] < dea[i]:
            out.append({"time": ts, "kind": "sell", "text": "MACD DIF 下穿 DEA", "price": price})
        if np.isfinite(r[i]) and r[i] <= 28 and (i == start or r[i - 1] > 28):
            out.append({"time": ts, "kind": "buy", "text": f"RSI14={r[i]:.0f} 进入超卖区", "price": price})
        if np.isfinite(r[i]) and r[i] >= 72 and (i == start or r[i - 1] < 72):
            out.append({"time": ts, "kind": "warn", "text": f"RSI14={r[i]:.0f} 进入超买区", "price": price})
        if np.isfinite(up[i]) and c[i] > up[i] and c[i - 1] <= up[i - 1]:
            out.append({"time": ts, "kind": "warn", "text": "突破 BOLL 上轨，短线过热", "price": price})
        if np.isfinite(low_[i]) and c[i] < low_[i] and c[i - 1] >= low_[i - 1]:
            out.append({"time": ts, "kind": "buy", "text": "跌破 BOLL 下轨，关注超跌反弹", "price": price})
        if np.isfinite(mid[i]) and c[i - 1] < mid[i - 1] and c[i] > mid[i]:
            out.append({"time": ts, "kind": "buy", "text": "站上 BOLL 中轨", "price": price})
    return out[-10:][::-1]  # 最新在前，最多 10 条


def _buy_plan(score: int, supports: list[dict], resistances: list[dict], cur: float) -> dict | None:
    if score < 35 or not supports:
        return None
    sup = supports[0]["price"]
    entry = round(min(cur, sup * 1.02), 2)
    stop = round(sup * 0.95, 2)
    if resistances:
        target = round(resistances[0]["price"], 2)
    else:
        target = round(entry * 1.15, 2)
    if target <= entry:
        target = round(entry * 1.12, 2)
    position = int(max(10, min(60, 20 + score * 0.4)))
    note = (f"参考最近支撑位 {sup} 附近分批介入，跌破 {stop} 止损；"
            f"第一目标 {target}。评分 {score}，建议仓位 ≤{position}%。")
    return {"entry": entry, "stop": stop, "target": target, "positionPct": position, "note": note}


# ── 主入口 ────────────────────────────────────────────────────────────
def rule_based_analysis(code: str, df: pd.DataFrame) -> dict:
    df = df.dropna(subset=["close"])
    if len(df) < 60:
        raise ValueError(f"{code} 可用 K 线不足（{len(df)} 根），无法生成分析")
    m = ind.compute_all(df)
    score, notes = _score(df, m)
    rating = _rating(score)
    trend = _trend_items(df, m)
    supports, resistances = _sr_levels(df, m)
    signals = _signals(df, m)
    cur = float(df["close"].iloc[-1])
    plan = _buy_plan(score, supports, resistances, cur)

    g20 = (cur / float(df["close"].iloc[-21]) - 1) * 100 if len(df) > 21 else 0.0
    summary = (f"{_rating(score)}（综合评分 {score}/100）。现价 {cur:.2f}，20 日涨幅 {g20:+.1f}%。")
    if notes:
        summary += "；" .join(notes[:3]) + "。"
    if supports:
        summary += f" 下方支撑 {supports[0]['price']}（{supports[0]['sources']}）"
    if resistances:
        summary += f"，上方压力 {resistances[0]['price']}（{resistances[0]['sources']}）"

    return {
        "code": code,
        "score": int(score),
        "rating": rating,
        "summary": summary,
        "trend": trend,
        "supports": supports,
        "resistances": resistances,
        "signals": signals,
        "buyPlan": plan,
    }
