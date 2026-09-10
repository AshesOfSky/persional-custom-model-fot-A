"""tech_analysis.py — 深度技术分析载荷构建（契约 v3，GET /api/tech/{code}）。

把 v5 run_full_analysis 的巨型结果 dict 映射为 Web 契约 TechAnalysis：
支撑压力汇合 / 斐波那契 / 江恩时空 / 艾略特波浪 / 缠论 / K线形态 / 图表形态 /
背离 / 量价 / 多周期共振 / 指标解读 / 交易计划，外加图表叠加层 overlay。

缠论为本地自建引擎（chan.py，旧模型无此模块），v5/fallback 两种模式都产出；
斐波那契/江恩在 v5 不可用时由本地同公式降级计算（engine 如实标注 fallback）。
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import logging

import pandas as pd

from . import analysis_rules, chan, elliott_local

FIB_RATIOS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
GANN_FAN_SHOW = ("1x4", "1x2", "1x1", "2x1", "4x1")  # 与旧版主图一致的 5 条角度线
logger = logging.getLogger(__name__)


def _f(x, nd=2):
    try:
        v = float(x)
        return round(v, nd) if math.isfinite(v) else None
    except Exception:  # noqa: BLE001
        return None


def _date_of(df: pd.DataFrame, pos: int) -> str | None:
    try:
        pos = int(pos)
        if pos < 0:
            pos = len(df) + pos
        if 0 <= pos < len(df):
            t = df.index[pos]
            return t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)[:10]
    except Exception:  # noqa: BLE001
        pass
    return None


def _d10(x) -> str:
    return str(x)[:10] if x else ""


# ── 斐波那契（v5/本地同公式；fallback 时本地计算） ─────────────────────
def _fibonacci(df: pd.DataFrame, raw: dict | None) -> dict | None:
    hi = _f((raw or {}).get("fib_high"))
    lo = _f((raw or {}).get("fib_low"))
    if hi is None or lo is None or hi <= lo:
        hi = _f(df["high"].max())
        lo = _f(df["low"].min())
    if hi is None or lo is None or hi <= lo:
        return None
    cur = _f(df["close"].iloc[-1])
    levels = []
    for r in FIB_RATIOS:
        price = round(lo + (hi - lo) * r, 3)
        levels.append({
            "ratio": r,
            "label": f"{r * 100:.1f}%",
            "price": price,
            "role": ("support" if cur is not None and price <= cur else "resistance"),
        })
    pos = round((cur - lo) / (hi - lo) * 100, 1) if cur is not None else None
    return {"high": hi, "low": lo, "currentPct": pos, "levels": levels}


# ── 江恩（v5 原始结构 → 契约；fallback 本地同公式） ─────────────────────
def _gann_local(df: pd.DataFrame, lookback: int = 120) -> dict | None:
    """v5 不可用时的本地江恩（与 modules/gann.py 同公式：八分位+甘氏扇+时空周期）。"""
    if df is None or len(df) < 10:
        return None
    seg = df.tail(lookback)
    hi, lo = float(seg["high"].max()), float(seg["low"].min())
    cur = float(df["close"].iloc[-1])
    rng = hi - lo
    if rng <= 0:
        return None
    strong = {1 / 3, 3 / 8, 1 / 2, 5 / 8, 2 / 3}
    octaves = [{
        "ratio": r, "label": f"{r:.3f}",
        "price": round(lo + rng * r, 3), "strong": r in strong,
    } for r in (0.0, 1 / 8, 1 / 4, 1 / 3, 3 / 8, 1 / 2, 5 / 8, 2 / 3, 3 / 4, 7 / 8, 1.0)]
    hi_pos = len(df) - len(seg) + int(seg["high"].to_numpy().argmax())
    lo_pos = len(df) - len(seg) + int(seg["low"].to_numpy().argmin())
    if lo_pos <= hi_pos:
        pivot_pos, pivot_price, direction = lo_pos, lo, "up"
    else:
        pivot_pos, pivot_price, direction = hi_pos, hi, "down"
    unit = rng / max(len(seg), 1)
    sign = 1.0 if direction == "up" else -1.0
    fan_levels, fan_series = [], {}
    for name, ratio in (("1x4", 0.25), ("1x2", 0.5), ("1x1", 1.0), ("2x1", 2.0), ("4x1", 4.0)):
        end_price = pivot_price + sign * unit * ratio * (len(df) - 1 - pivot_pos)
        fan_series[name] = [round(pivot_price, 3), round(end_price, 3)]
        if end_price > 0:
            fan_levels.append({"label": f"江恩{name}角度线", "price": round(end_price, 3),
                               "type": "support" if end_price <= cur else "resistance"})
    cycles = []
    for c in (30, 45, 60, 90, 120, 144, 180, 270, 360):
        pos = pivot_pos + c
        cycles.append({"cycle": c, "date": _date_of(df, pos),
                       "futureBars": max(0, pos - (len(df) - 1))})
    return {
        "range": {"high": round(hi, 3), "low": round(lo, 3), "lookback": lookback},
        "pivot": {"date": _date_of(df, pivot_pos), "price": round(pivot_price, 3),
                  "direction": direction},
        "octaves": octaves, "fanLevels": fan_levels, "fanRays": fan_series,
        "timeCycles": cycles, "currentPrice": round(cur, 3),
    }


def _gann_from_v5(raw: dict, df: pd.DataFrame) -> dict | None:
    g = raw.get("gann") or {}
    if not g:
        return None
    pivot = g.get("pivot") or {}
    pivot_pos = pivot.get("pos")
    fan_series = g.get("fan_series") or {}
    fan_rays = {}
    for name in GANN_FAN_SHOW:
        arr = fan_series.get(name)
        if isinstance(arr, list) and arr:
            end_v = _f(arr[-1], 3)
            if end_v is not None:
                fan_rays[name] = [_f(pivot.get("price"), 3), end_v]
    return {
        "range": g.get("range"),
        "pivot": {"date": _d10(pivot.get("date")) or _date_of(df, pivot_pos or 0),
                  "price": _f(pivot.get("price"), 3),
                  "direction": pivot.get("direction")},
        "octaves": [{"ratio": _f(o.get("ratio"), 4), "label": str(o.get("label", "")),
                     "price": _f(o.get("price"), 3), "strong": bool(o.get("strong"))}
                    for o in g.get("octaves") or []],
        "fanLevels": [{"label": str(x.get("label", "")), "price": _f(x.get("price"), 3),
                       "type": x.get("type")} for x in g.get("fan_levels") or []],
        "fanRays": fan_rays,
        "timeCycles": [{"cycle": c.get("cycle"), "date": _d10(c.get("date")) or None,
                        "futureBars": c.get("future_bars")}
                       for c in g.get("time_cycles") or []],
        "currentPrice": _f(g.get("current_price"), 3),
    }


# ── 各小节映射（输入 v5 raw；任一小节失败返回 None，不拖垮整体） ─────────
def _map_summary(raw: dict) -> dict | None:
    c = raw.get("comprehensive_probability") or {}
    if not c:
        return None

    def _strongest(x):
        if isinstance(x, dict):
            return str(x.get("name") or x.get("label") or "") or None
        return str(x) if x else None

    return {
        "overallBias": c.get("overall_bias"),
        "overallScore": _f(c.get("overall_score"), 1),
        "bullishPct": _f(c.get("bullish_pct"), 1),
        "bearishPct": _f(c.get("bearish_pct"), 1),
        "neutralPct": _f(c.get("neutral_pct"), 1),
        "confirmationLevel": c.get("confirmation_level"),
        "confirmationScore": _f(c.get("confirmation_score"), 1),
        "strongestBullish": _strongest(c.get("strongest_bullish")),
        "strongestBearish": _strongest(c.get("strongest_bearish")),
        "interpretation": c.get("interpretation"),
    }


def _map_trade_plan(raw: dict) -> dict | None:
    tp = raw.get("trade_plan") or {}
    if not tp.get("ok"):
        return None
    basis = tp.get("entry_basis") or {}
    stop_basis = tp.get("stop_basis") or {}
    return {
        "direction": tp.get("direction"),
        "entry": _f(tp.get("entry"), 3),
        "entryZone": [_f(x, 3) for x in (tp.get("entry_zone") or [])],
        "entryBasis": str(basis.get("method") or ""),
        "entryConfluence": basis.get("confluence"),
        "atSupport": bool(tp.get("at_support")),
        "pullbackPct": _f(tp.get("pullback_pct")),
        "stop": _f(tp.get("stop"), 3),
        "stopPct": _f(tp.get("stop_pct")),
        "stopBasis": str(stop_basis.get("method") or ""),
        "targets": [{
            "level": t.get("level"), "price": _f(t.get("price"), 3),
            "method": str(t.get("method") or ""), "confluence": t.get("confluence"),
            "pct": _f(t.get("pct")), "rr": _f(t.get("rr")),
        } for t in tp.get("targets") or []],
        "primaryRr": _f(tp.get("primary_rr")),
        "note": tp.get("note"),
    }


# 支撑压力来源 → 判断依据（契约 v3.1，技术页展示用）
SR_SOURCE_LABEL = {
    "classic": "经典技术位",
    "gann_octave": "江恩八分位",
    "gann_fan": "甘氏扇角度线",
    "elliott": "艾略特波浪",
    "local": "本地规则",
}
SR_SOURCE_BASIS = {
    "classic": "Pivot 轴心点/前高前低/EMA 隧道/布林带轨/VWAP，按触及次数与近度计强",
    "gann_octave": "近 120 根高低区间按 1/8 等分（含 1/3、2/3），4/8、3/8~5/8 为强区",
    "gann_fan": "自最近显著枢轴（高/低点）按价格/时间比率（1x1、1x2、2x1 等）画射线，取当前价位",
    "elliott": "艾略特浪型端点与斐波那契投射目标位（W3 161.8%/200% 等）",
    "local": "±3 根局部极值摆动点聚类（近 120 根，触及次数+近度计强）+ EMA55/144/布林轨",
}


def _map_confluence(raw: dict, cur: float | None) -> dict | None:
    agg = raw.get("sr_confluence") or {}
    if not agg:
        return None

    def _lv(x):
        price = _f(x.get("price"), 3)
        if price is None:
            return None
        sources = [str(s) for s in (x.get("sources") or [])]
        return {
            "price": price,
            "method": str(x.get("method") or ""),
            "sources": sources,
            "sourceLabels": [SR_SOURCE_LABEL.get(s, s) for s in sources],
            "basis": "；".join(SR_SOURCE_BASIS.get(s, s) for s in sources) or None,
            "confluence": x.get("confluence") or 1,
            "distancePct": (round((price - cur) / cur * 100, 2)
                            if cur and cur > 0 else None),
        }

    supports = [y for y in (_lv(x) for x in agg.get("supports") or []) if y]
    resistances = [y for y in (_lv(x) for x in agg.get("resistances") or []) if y]
    if not supports and not resistances:
        return None
    return {"supports": supports[:8], "resistances": resistances[:8]}


def _map_elliott(raw: dict, df: pd.DataFrame) -> tuple[dict | None, dict | None]:
    """返回 (elliott 小节, overlay 用 elliott 部分)。

    overlay 取"最近一个完整推动浪 + 最近一个完整修正浪"的枢轴连线：
    推动浪标 0/1/2/3/4/5、修正浪标 0/A/B/C，每个枢轴带 dir（与前一枢轴的方向），
    前端据此画带箭头的浪型线段；若无有效浪则回退到 v5 wave_labels 全量。
    """
    ew = raw.get("elliott_wave_summary") or {}
    if not ew:
        return None, None
    cw = ew.get("current_wave") or {}
    section = {
        "source": "v5",
        "methodVersion": "v5-window-confirmed",
        "currentWave": {
            "type": cw.get("type"), "waveNumber": cw.get("wave_number"),
            "direction": cw.get("direction"), "confidence": _f(cw.get("confidence"), 0),
            "phase": cw.get("phase"), "description": cw.get("description"),
        },
        "targets": [{"price": _f(t.get("price"), 3), "label": str(t.get("label") or ""),
                     "ratio": str(t.get("ratio") or ""), "direction": t.get("direction")}
                    for t in ew.get("projected_targets") or []],
    }
    if isinstance(ew.get("reliability"), dict):
        section["reliability"] = ew["reliability"]
    mtf = raw.get("elliott_wave_multi_tf") or {}
    syn = mtf.get("synthesis") or {}
    if syn:
        section["multiTf"] = {
            "alignment": syn.get("alignment"), "bias": syn.get("bias"),
            "confidence": _f(syn.get("confidence"), 0),
            "suggestedAction": syn.get("suggested_action"),
            "entryTiming": syn.get("entry_timing"),
            "reasoning": [str(r) for r in (syn.get("reasoning") or [])][:6],
        }

    def _pivots_of(wave: dict) -> list[dict]:
        pts = []
        for p in wave.get("pivots") or []:
            t = _d10(p.get("date")) or _date_of(df, p.get("index") or 0)
            price = _f(p.get("price"), 3)
            if t and price is not None:
                pts.append({"time": t, "price": price, "pivotType": p.get("type")})
        return pts

    # 最近（end_index 最大）的完整推动浪/修正浪，最贴近当前窗口
    impulses = ew.get("impulse_waves") or []
    correctives = ew.get("corrective_waves") or []
    best_imp = max(impulses, key=lambda w: w.get("end_index") or 0, default=None)
    best_cor = max(correctives, key=lambda w: w.get("end_index") or 0, default=None)

    segments: list[dict] = []
    labels: list[dict] = []
    for wave, names, kind in ((best_imp, ["0", "1", "2", "3", "4", "5"], "impulse"),
                              (best_cor, ["0", "A", "B", "C"], "corrective")):
        if not wave:
            continue
        pts = _pivots_of(wave)
        if len(pts) < 2:
            continue
        for i, p in enumerate(pts):
            prev = pts[i - 1] if i > 0 else None
            labels.append({
                "time": p["time"], "price": p["price"],
                "label": names[i] if i < len(names) else str(i),
                "pivotType": p["pivotType"], "kind": kind,
                "dir": (("up" if p["price"] >= prev["price"] else "down")
                        if prev else None),
            })
        for a, b in zip(pts, pts[1:]):
            segments.append({"kind": kind,
                             "points": [{"time": a["time"], "price": a["price"]},
                                        {"time": b["time"], "price": b["price"]}]})

    # 回退：无有效浪结构时用 v5 wave_labels（全量，可能含较老浪型）
    if not labels:
        for lb in ew.get("wave_labels") or []:
            t = _d10(lb.get("date")) or _date_of(df, lb.get("index") or 0)
            price = _f(lb.get("price"), 3)
            if t and price is not None:
                labels.append({"time": t, "price": price,
                               "label": str(lb.get("label") or ""),
                               "pivotType": lb.get("pivot_type"),
                               "kind": ("impulse" if "impulse" in str(lb.get("wave_type") or "")
                                        else "corrective"),
                               "dir": None})
        labels.sort(key=lambda x: x["time"])
        for a, b in zip(labels, labels[1:]):
            segments.append({"kind": a.get("kind") or "impulse",
                             "points": [{"time": a["time"], "price": a["price"]},
                                        {"time": b["time"], "price": b["price"]}]})

    overlay = {
        "segments": segments,
        "labels": labels,
        "targets": [{"price": t["price"], "label": t["label"]}
                    for t in section["targets"] if t["price"] is not None],
    }
    return section, overlay


def _map_candlestick(raw: dict, df: pd.DataFrame) -> dict | None:
    cs = raw.get("candlestick_summary") or {}
    patterns = cs.get("patterns")
    if not patterns:
        return None
    out = []
    for p in patterns:
        out.append({
            "nameCn": str(p.get("name_cn") or ""),
            "type": p.get("type"), "direction": p.get("direction"),
            "reliability": p.get("reliability"),
            "time": _date_of(df, p.get("bar_index") or 0),
            "description": p.get("description"),
            "biasScore": _f(p.get("bias_score"), 0),
        })
    return {
        "patterns": out,
        "count": cs.get("pattern_count"),
        "bullish": cs.get("bullish_patterns"),
        "bearish": cs.get("bearish_patterns"),
        "strongest": cs.get("strongest_signal"),
        "combinedBias": _f(cs.get("combined_bias_score"), 1),
    }


def _map_chart_patterns(raw: dict) -> dict | None:
    cp = raw.get("chart_pattern_summary") or {}
    patterns = cp.get("patterns")
    if not patterns:
        return None
    strongest = cp.get("strongest_pattern") or {}
    return {
        "patterns": [{
            "nameCn": str(p.get("name_cn") or ""),
            "type": p.get("type"), "direction": p.get("direction"),
            "neckline": _f(p.get("neckline"), 3),
            "target": _f(p.get("target_price"), 3),
            "stop": _f(p.get("stop_price"), 3),
            "confidence": _f(p.get("confidence"), 0),
            "description": p.get("description"),
            "biasScore": _f(p.get("bias_score"), 0),
        } for p in patterns],
        "strongest": str(strongest.get("name_cn") or "") or None,
    }


def _map_divergences(raw: dict) -> dict | None:
    dv = raw.get("divergence_summary") or {}
    items = dv.get("divergences")
    if not items:
        return None
    return {
        "list": [{
            "type": d.get("type"), "indicator": d.get("indicator"),
            "startDate": _d10(d.get("start_date")), "endDate": _d10(d.get("end_date")),
            "spanBars": d.get("span_bars"), "reliability": d.get("reliability"),
            "description": d.get("description"), "biasScore": _f(d.get("bias_score"), 0),
        } for d in items],
        "activeCount": dv.get("active_count"),
        "hasBullish": bool(dv.get("has_bullish_divergence")),
        "hasBearish": bool(dv.get("has_bearish_divergence")),
        "strongest": dv.get("strongest_divergence"),
    }


def _map_volume_price(raw: dict) -> dict | None:
    vp = raw.get("volume_price_analysis") or {}
    if not vp:
        return None
    vpd = vp.get("volume_price_divergence") or {}
    tr = vp.get("turnover_rate") or {}
    vr = vp.get("volume_ratio") or {}
    ad = vp.get("accumulation_distribution") or {}
    wy = vp.get("wyckoff_phase") or {}
    return {
        "divergence": {
            "detected": bool(vpd.get("detected")), "type": vpd.get("type"),
            "description": vpd.get("description"), "biasScore": _f(vpd.get("bias_score"), 0),
        },
        "turnover": ({
            "current": _f(tr.get("current")), "avg5": _f(tr.get("avg_5d")),
            "avg20": _f(tr.get("avg_20d")), "level": tr.get("level"),
            "trend": tr.get("trend"),
        } if tr.get("available") else None),
        "volumeRatio": {
            "value": _f(vr.get("value")), "level": vr.get("level"),
            "interpretation": vr.get("interpretation"),
            "biasScore": _f(vr.get("bias_score"), 0),
        },
        "accumulation": {
            "phase": ad.get("phase"), "trend": ad.get("ad_trend"),
            "description": ad.get("description"),
        },
        "wyckoff": {
            "phase": wy.get("phase"), "phaseEn": wy.get("phase_en"),
            "confidence": _f(wy.get("confidence"), 0), "description": wy.get("description"),
        },
        "combinedBias": _f(vp.get("combined_bias_score"), 1),
    }


def _map_multi_tf(raw: dict) -> dict | None:
    mtf = raw.get("multi_timeframe_summary") or {}
    tfs = mtf.get("timeframes") or {}
    if not tfs:
        return None
    rows = []
    for tf in ("日线", "周线", "月线"):
        info = tfs.get(tf) or {}
        if not info:
            continue
        rows.append({
            "tf": tf, "trend": info.get("trend"),
            "strength": _f(info.get("strength"), 0),
            "score": _f(info.get("score"), 0),
            "emaAlignment": info.get("ema_alignment"),
            "macdDirection": info.get("macd_direction"),
            "rsiZone": info.get("rsi_zone"),
            "details": info.get("details"),
        })
    return {
        "timeframes": rows,
        "alignment": mtf.get("alignment"),
        "alignmentScore": _f(mtf.get("alignment_score"), 0),
        "dominantTrend": mtf.get("dominant_trend"),
        "summary": mtf.get("summary"),
    }


def _map_interpretations(raw: dict) -> list[dict] | None:
    items = raw.get("indicator_interpretations") or []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        out.append({
            "name": it.get("name"), "value": it.get("value"),
            "bias": it.get("bias"), "biasScore": _f(it.get("bias_score"), 0),
            "category": it.get("category"),
            "interpretation": it.get("interpretation"),
            "actionHint": it.get("action_hint"),
        })
    return out[:15] or None


# ── fallback：v5 不可用时的本地降级载荷 ─────────────────────────────────
def _fallback_sr(code: str, df: pd.DataFrame) -> dict | None:
    try:
        res = analysis_rules.rule_based_analysis(code, df)
    except Exception:  # noqa: BLE001
        return None
    cur = _f(df["close"].iloc[-1])

    def _lv(x):
        return {"price": x.get("price"), "method": str(x.get("sources") or "本地摆动点"),
                "sources": ["local"], "sourceLabels": [SR_SOURCE_LABEL["local"]],
                "basis": SR_SOURCE_BASIS["local"], "confluence": None,
                "distancePct": (round((x["price"] - cur) / cur * 100, 2)
                                if cur and x.get("price") else x.get("distancePct"))}

    supports = [_lv(x) for x in res.get("supports") or []]
    resistances = [_lv(x) for x in res.get("resistances") or []]
    if not supports and not resistances:
        return None
    return {"supports": supports, "resistances": resistances}


# ── 主入口 ────────────────────────────────────────────────────────────
def build_tech_payload(
    code: str,
    name: str,
    df: pd.DataFrame,
    raw: dict | None,
    *,
    state_key: str | None = None,
    prior_state: Mapping | None = None,
) -> dict:
    """构建 TechAnalysis 契约载荷。raw = v5 run_full_analysis 结果（None → 本地降级）。

    缠论、艾略特本地引擎、斐波那契、江恩与支撑压力在 fallback 模式也可产出；
    形态/背离/量价/多周期/解读/交易计划仍依赖 v5。
    """
    engine = "v5" if raw else "fallback"
    # 防御重复列（live 数据脏列），保证后续标量取值安全
    df = df.loc[:, ~df.columns.duplicated()].copy()
    cur = _f(df["close"].iloc[-1])
    last_time = df.index[-1]
    last_time = last_time.strftime("%Y-%m-%d") if hasattr(last_time, "strftime") \
        else str(last_time)[:10]

    try:
        chan_res = chan.analyze_chan(
            df,
            state_key=state_key,
            prior_state=prior_state,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Chan analysis failed for %s", code)
        chan_res = chan.analyze_chan(None)  # 空结构兜底
    fib = _fibonacci(df, raw)
    gann = None
    if raw:
        try:
            gann = _gann_from_v5(raw, df)
        except Exception:  # noqa: BLE001
            gann = None
    if gann is None and not raw:
        gann = _gann_local(df)
    sr = None
    if raw:
        try:
            sr = _map_confluence(raw, cur)
        except Exception:  # noqa: BLE001
            sr = None
    if sr is None and not raw:
        sr = _fallback_sr(code, df)

    elliott = ew_overlay = None
    try:
        ew_local = elliott_local.analyze_elliott(df)
    except Exception:  # noqa: BLE001
        logger.exception("Local Elliott reliability analysis failed for %s", code)
        ew_local = None
    candle = chart_pats = divs = vp = mtf = summary = plan = interp = None
    # One response must use one authoritative Elliott snapshot.  When v5 has a
    # usable count it is also the source used by v5 SR/trade-plan aggregation,
    # so keep section, overlay and SR aligned.  The ATR engine is the explicit
    # fallback when v5 has no usable wave result.
    if raw:
        try:
            elliott, ew_overlay = _map_elliott(raw, df)
            if elliott and (elliott.get("currentWave") or {}).get("type") in (None, "unknown"):
                elliott = ew_overlay = None
        except Exception:  # noqa: BLE001
            logger.exception("v5 Elliott mapping failed for %s", code)
            elliott = ew_overlay = None
    if elliott is None:
        if ew_local:
            ew_overlay = ew_local["overlay"]
            elliott = {
                "source": "local",
                "methodVersion": ew_local.get("methodVersion", "elliott-local-2"),
                "currentWave": ew_local["currentWave"],
                "targets": ew_local["targets"],
            }
    if (
        elliott is not None
        and "reliability" not in elliott
        and ew_local
        and ew_local.get("reliability")
    ):
        # The display count remains the single v5/local authoritative count.
        # The local companion contributes only an independently versioned,
        # non-scoring confirmed-structure stream over the identical bars.
        elliott["reliability"] = ew_local["reliability"]
    if raw:
        try:
            summary = _map_summary(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            plan = _map_trade_plan(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            candle = _map_candlestick(raw, df)
        except Exception:  # noqa: BLE001
            pass
        try:
            chart_pats = _map_chart_patterns(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            divs = _map_divergences(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            vp = _map_volume_price(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            mtf = _map_multi_tf(raw)
        except Exception:  # noqa: BLE001
            pass
        try:
            interp = _map_interpretations(raw)
        except Exception:  # noqa: BLE001
            pass

    # ── 图表叠加层（日线坐标；前端仅日线 timeframe 使用） ──
    overlay = {
        "fib": ([{"ratio": x["ratio"], "label": x["label"], "price": x["price"]}
                 for x in fib["levels"]] if fib else []),
        "gannOctaves": ([{"label": o["label"], "price": o["price"],
                          "strong": o["strong"]} for o in gann["octaves"]]
                        if gann else []),
        "gannFan": [],
        "elliottSegments": (ew_overlay or {}).get("segments") or [],
        "elliottLabels": (ew_overlay or {}).get("labels") or [],
        "elliottTargets": (ew_overlay or {}).get("targets") or [],
        "chanBi": [{"direction": b["direction"],
                    "confirmed": bool(b.get("confirmed", False)),
                    "confirmedTime": b.get("confirmedTime"),
                    "endpointConfirmedTime": b.get("endpointConfirmedTime"),
                    "points": [{"time": b["startTime"], "price": b["startPrice"]},
                               {"time": b["endTime"], "price": b["endPrice"]}]}
                   for b in chan_res.get("bi", [])[-60:]],
        "chanActiveBi": ({
            "direction": chan_res["activeBi"]["direction"],
            "confirmed": False,
            "points": [
                {"time": chan_res["activeBi"]["startTime"],
                 "price": chan_res["activeBi"]["startPrice"]},
                {"time": chan_res["activeBi"]["endTime"],
                 "price": chan_res["activeBi"]["endPrice"]},
            ],
        } if chan_res.get("activeBi") else None),
        "chanZhongshu": [{"startTime": z["startTime"], "endTime": z["endTime"],
                          "zg": z["zg"], "zd": z["zd"],
                          "confirmed": bool(z.get("confirmed", False)),
                          "confirmedTime": z.get("confirmedTime")}
                         for z in chan_res.get("zhongshu", [])[-5:]],
        "chanFenxing": chan_res.get("fenxing", [])[-60:],
        "chanSignals": [{"time": s["time"], "price": s["price"],
                          "pivotTime": s.get("pivotTime"),
                          "label": s["label"], "type": s["type"],
                          "confirmed": bool(s.get("confirmed", False)),
                          "confirmedTime": s.get("confirmedTime")}
                         for s in chan_res.get("signals", [])[-10:]],
    }
    if gann and gann.get("fanRays") and gann.get("pivot", {}).get("date"):
        p_date = gann["pivot"]["date"]
        end_date = last_time
        for angle, (p0, p1) in gann["fanRays"].items():
            if p0 is None or p1 is None:
                continue
            overlay["gannFan"].append({
                "label": f"江恩{angle}", "ratio": angle,
                "points": [{"time": p_date, "value": p0},
                           {"time": end_date, "value": p1}],
            })

    return {
        "code": code,
        "name": name,
        "timeframe": "日线",
        "currentPrice": cur,
        "time": last_time,
        "engine": engine,
        "engineNote": ("v5 综合引擎（艾略特使用与交易位一致的 v5 快照；"
                       "缠论为本地自建模块）"
                       if raw else
                       "本地降级（v5 不可用）：艾略特 ATR 引擎、缠论、"
                       "斐波那契、江恩与支撑压力可用，其余维度需 v5）"),
        "summary": summary,
        "tradePlan": plan,
        "sr": sr,
        "fibonacci": fib,
        "gann": ({k: v for k, v in gann.items() if k != "fanRays"} if gann else None),
        "elliott": elliott,
        "chan": {k: v for k, v in chan_res.items()},
        "candlestick": candle,
        "chartPatterns": chart_pats,
        "divergences": divs,
        "volumePrice": vp,
        "multiTf": mtf,
        "interpretations": interp,
        "overlay": overlay,
    }
