"""
sr_confluence.py — 多来源支撑压力“汇合”引擎（①精确交易建议的地基）

把以下来源的价位统一、去重合并，并计算“几重支撑/压力”(confluence)：
  • classic：analysis 已有的 multi_tf_sr —— 斐波那契回撤、均线(EMA/MA)、前高前低、
    Pivot 枢轴、布林、VWAP 带等；
  • gann：江恩八分位 + 甘氏扇当前值（modules.gann）；
  • elliott：艾略特浪型端点 + Fibonacci 投射目标（modules.elliott_wave 输出）。

核心思想（交易的本质）：在**多方法汇合的强支撑**处入场，向下看**下一重支撑**作止损，
向上看**上方压力**作止盈，据此算盈亏比——而非预测涨跌。
"""

from typing import Dict, List, Optional


# =========================================================================
# 各来源 → 统一格式 [{price, method, source, type}]
# =========================================================================

def existing_sr_levels(sr_levels_dict: Optional[Dict], current_price: float) -> List[Dict]:
    """analysis 里的 multi_tf_sr(含 all_supports/all_resistances) 归一化。"""
    out: List[Dict] = []
    d = sr_levels_dict or {}
    for key in ("all_supports", "all_resistances"):
        for lv in d.get(key, []) or []:
            price = lv.get("price")
            if price and price > 0:
                out.append({
                    "price": round(float(price), 3),
                    "method": lv.get("method") or lv.get("label") or "经典支撑压力",
                    "source": "classic",
                    "type": "support" if float(price) <= current_price else "resistance",
                })
    return out


def elliott_sr_levels(ew_result: Optional[Dict], current_price: float) -> List[Dict]:
    """从艾略特结果抽取浪型端点 + 投射目标 → 统一 S/R 格式。"""
    out: List[Dict] = []
    if not ew_result:
        return out
    imp_labels = ["0", "1", "2", "3", "4", "5"]
    cor_labels = ["0", "A", "B", "C"]
    latest_wave = None
    latest_labels = None
    for wave_set, labels in ((ew_result.get("impulse_waves"), imp_labels),
                             (ew_result.get("corrective_waves"), cor_labels)):
        if not wave_set:
            continue
        candidate = max(
            wave_set,
            key=lambda x: (x.get("end_index", -1), x.get("fib_score", 0)),
        )
        if latest_wave is None or (
            candidate.get("end_index", -1),
            candidate.get("fib_score", 0),
        ) > (
            latest_wave.get("end_index", -1),
            latest_wave.get("fib_score", 0),
        ):
            latest_wave = candidate
            latest_labels = labels

    # 端点只取最近一组完整结构，避免高分但陈旧的历史浪主导当前 S/R。
    if latest_wave is not None and latest_labels is not None:
        for i, pv in enumerate(latest_wave.get("pivots", []) or []):
            price = pv.get("price")
            if price and price > 0:
                lab = latest_labels[i] if i < len(latest_labels) else str(i)
                out.append({
                    "price": round(float(price), 3),
                    "method": f"艾略特W{lab}({pv.get('type', '')})",
                    "source": "elliott",
                    "type": "support" if float(price) <= current_price else "resistance",
                })
    for t in ew_result.get("projected_targets", []) or []:
        price = t.get("price") if isinstance(t, dict) else None
        price = price or (t.get("target") if isinstance(t, dict) else None)
        if price and price > 0:
            lab = t.get("label") or t.get("wave") or "投射目标"
            out.append({
                "price": round(float(price), 3),
                "method": f"艾略特投射:{lab}",
                "source": "elliott",
                "type": "support" if float(price) <= current_price else "resistance",
            })
    return out


# =========================================================================
# 汇合（去重合并 + confluence 计数）
# =========================================================================

def _merge(levels: List[Dict], tol_pct: float) -> List[Dict]:
    """价位相近(默认 0.4%)的合并：method 拼接，confluence 按独立 source 计数。"""
    levels = sorted(levels, key=lambda x: x["price"])
    out: List[Dict] = []
    for lv in levels:
        if out and abs(lv["price"] - out[-1]["price"]) / max(out[-1]["price"], 1e-6) <= tol_pct:
            if lv["method"] not in out[-1]["method"]:
                out[-1]["method"] += " + " + lv["method"]
            out[-1]["_sources"].add(lv["source"])
            # confluence 表示独立来源数；同一来源在同一区位出现多个端点只计一次。
            out[-1]["confluence"] = len(out[-1]["_sources"])
        else:
            nl = dict(lv)
            nl["confluence"] = 1
            nl["_sources"] = {lv["source"]}
            out.append(nl)
    for o in out:
        o["sources"] = sorted(o.pop("_sources"))
        o["price"] = round(o["price"], 3)
    return out


def aggregate_sr(current_price: float,
                 sr_levels_dict: Optional[Dict] = None,
                 gann_result: Optional[Dict] = None,
                 ew_result: Optional[Dict] = None,
                 tol_pct: float = 0.004) -> Dict:
    """汇合全部来源 → {supports:[由近及远], resistances:[由近及远], all:[按价升序]}。

    每个价位含：price / method(多来源拼接) / sources(去重) / confluence(几重) / type。
    supports 按“离现价近→远”降序排列（最近支撑在前）；resistances 升序（最近压力在前）。
    """
    from modules.gann import gann_sr_levels

    levels: List[Dict] = []
    levels += existing_sr_levels(sr_levels_dict, current_price)
    if gann_result:
        levels += gann_sr_levels(gann_result, current_price)
    if ew_result:
        levels += elliott_sr_levels(ew_result, current_price)

    merged = _merge([l for l in levels if l.get("price", 0) > 0], tol_pct)
    supports = sorted([x for x in merged if x["type"] == "support"],
                      key=lambda x: -x["price"])           # 最近支撑在前
    resistances = sorted([x for x in merged if x["type"] == "resistance"],
                         key=lambda x: x["price"])          # 最近压力在前
    return {"supports": supports, "resistances": resistances, "all": merged}


# =========================================================================
# ① 精确交易建议：在支撑位博弈盈亏比
# =========================================================================

def _basis(level: Dict) -> Dict:
    """把一个价位精简成“依据”描述。"""
    return {"price": level["price"], "method": level.get("method", ""),
            "sources": level.get("sources", []), "confluence": level.get("confluence", 1)}


def build_trade_plan(current_price: float, agg: Dict, atr: Optional[float] = None) -> Dict:
    """据汇合支撑压力生成“在支撑位博弈盈亏比”的交易计划（做多视角）。

    入场 = 最近的强支撑（现价上方回落至此/或现价已在其附近）——标注来自哪一重(来源+汇合);
    止损 = 该支撑**下方的下一个支撑**（跌破即离场）——标注其来源;
    止盈 = 上方**多档压力位**，各带百分比与盈亏比。

    返回 {ok, direction, entry, entry_basis, entry_zone, at_support, stop, stop_basis,
          stop_pct, targets:[{level,price,method,sources,confluence,pct,rr}], primary_rr, note}
    """
    if not current_price or current_price <= 0:
        return {"ok": False, "reason": "无有效现价"}
    supports = agg.get("supports", [])
    resistances = agg.get("resistances", [])
    if not supports:
        return {"ok": False, "reason": "近端无有效支撑位，暂不建议在此博弈"}

    # 入场支撑：默认最近支撑；若第二近支撑汇合更强且离现价 ≤2%，取更强者
    entry_lv = supports[0]
    if len(supports) >= 2 and supports[1]["confluence"] > entry_lv["confluence"] \
            and (current_price - supports[1]["price"]) / current_price <= 0.02:
        entry_lv = supports[1]
    entry = float(entry_lv["price"])

    # 止损：入场支撑“下方的下一个支撑”（跳过与入场过近<0.5%的同区位）
    below = [s for s in supports if s["price"] < entry * 0.995]
    if below:
        stop_lv = below[0]
        stop = round(stop_lv["price"] * 0.997, 3)          # 略破 0.3%
        stop_basis = _basis(stop_lv)
    elif atr and atr > 0:
        stop = round(entry - atr * 1.5, 3)
        stop_basis = {"price": stop, "method": "ATR×1.5（下方无更多支撑）", "sources": [], "confluence": 0}
    else:
        stop = round(entry * 0.95, 3)
        stop_basis = {"price": stop, "method": "默认 -5%", "sources": [], "confluence": 0}

    risk = entry - stop
    targets = []
    for i, r in enumerate(resistances[:3]):
        rp = float(r["price"])
        targets.append({
            "level": i + 1, "price": rp,
            "method": r.get("method", ""), "sources": r.get("sources", []),
            "confluence": r.get("confluence", 1),
            "pct": round((rp - entry) / entry * 100, 2),
            "rr": round((rp - entry) / risk, 2) if risk > 0 else None,
        })

    return {
        "ok": True,
        "direction": "long",
        "entry": round(entry, 3),
        "entry_basis": _basis(entry_lv),
        "entry_zone": [round(entry * 0.997, 3), round(entry * 1.003, 3)],
        "at_support": abs(current_price - entry) / current_price <= 0.01,   # 现价是否已贴该支撑
        "pullback_pct": round((current_price - entry) / current_price * 100, 2),
        "stop": stop,
        "stop_basis": stop_basis,
        "stop_pct": round((entry - stop) / entry * 100, 2),
        "targets": targets,
        "primary_rr": targets[0]["rr"] if targets else None,
        "note": "在支撑位博弈盈亏比：入场=最近强支撑，止损=下一重支撑，止盈=上方压力位；跌破止损即离场。",
    }
