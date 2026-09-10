"""
buy_action_plan.py — 买入操作计划生成器

当综合信号建议「买入」时，生成一份结构化的持仓操作计划：
  1. 综合买入评估分数（0-100）
  2. ATR + 支撑压力位计算的止盈 / 止损价位
  3. 买入后 T+1 ~ T+5 逐日操作建议
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional


# ── 综合买入评估 ────────────────────────────────────────────────────────

# 评分维度及权重
_SCORE_WEIGHTS = {
    "trend":       0.25,  # 趋势维度（EMA/多周期/ADX）
    "momentum":    0.20,  # 动量维度（MACD/RSI/KDJ）
    "volume":      0.15,  # 量能维度
    "support":     0.15,  # 支撑维度（距支撑位远近、布林位置）
    "risk_reward": 0.15,  # 风险收益比
    "signal":      0.10,  # 信号桶命中数
}


def _score_trend(analysis: Dict, df: pd.DataFrame) -> float:
    """趋势维度评分 0-100"""
    score = 50.0

    # Canonical trend score. The former key (`trend_strength`) did not match
    # analysis.run_full_analysis (`trend_strength_score`), so this dimension
    # silently stayed at 50. When the canonical score exists it already
    # includes ADX and multi-timeframe evidence; do not count them twice.
    trend_str = analysis.get(
        "trend_strength_score", analysis.get("trend_strength")
    )
    if isinstance(trend_str, (int, float)):
        return max(0.0, min(100.0, float(trend_str)))

    # Legacy-only fallback for older analysis payloads without a canonical
    # trend score.
    if "ADX" in df.columns:
        adx = float(df["ADX"].iloc[-1])
        if adx > 30:
            score += 10
        elif adx > 25:
            score += 5
        elif adx < 15:
            score -= 15

    # 多周期共振
    mtf = analysis.get("multi_timeframe_summary", {})
    alignment = mtf.get("alignment", "")
    if "多" in alignment:
        score += 10
    elif "空" in alignment:
        score -= 15

    return max(0, min(100, score))


def _score_momentum(analysis: Dict, df: pd.DataFrame) -> float:
    """动量维度评分 0-100"""
    score = 50.0

    # MACD
    if "MACD_hist" in df.columns:
        hist = float(df["MACD_hist"].iloc[-1])
        hist_prev = float(df["MACD_hist"].iloc[-2]) if len(df) >= 2 else hist
        if hist > 0:
            score += 15
        if hist > hist_prev:
            score += 10  # 柱放大

    # RSI
    if "RSI" in df.columns:
        rsi = float(df["RSI"].iloc[-1])
        if 40 <= rsi <= 60:
            score += 10  # 中性偏强
        elif 30 <= rsi < 40:
            score += 15  # 超卖反弹区
        elif rsi < 30:
            score += 5   # 过度超卖有风险
        elif rsi > 70:
            score -= 15  # 超买风险

    # KDJ
    if "KDJ_K" in df.columns and "KDJ_D" in df.columns:
        k = float(df["KDJ_K"].iloc[-1])
        d = float(df["KDJ_D"].iloc[-1])
        if k > d and k < 50:
            score += 10  # 低位金叉
        elif k > 80:
            score -= 10  # 高位风险

    return max(0, min(100, score))


def _score_volume(analysis: Dict, df: pd.DataFrame) -> float:
    """量能维度评分 0-100"""
    score = 50.0

    volume_ma5_column = "Vol_MA_5" if "Vol_MA_5" in df.columns else "Vol_MA5"
    if "Volume" in df.columns and volume_ma5_column in df.columns:
        vol = float(df["Volume"].iloc[-1])
        vol_ma5 = float(df[volume_ma5_column].iloc[-1])
        if vol_ma5 > 0:
            ratio = vol / vol_ma5
            if ratio > 2.0:
                score += 25  # 显著放量
            elif ratio > 1.5:
                score += 15
            elif ratio > 1.0:
                score += 5
            elif ratio < 0.6:
                score -= 15  # 严重缩量

    # 资金流向
    mf = analysis.get("money_flow_status", "")
    if "流入" in str(mf):
        score += 10
    elif "流出" in str(mf):
        score -= 10

    return max(0, min(100, score))


def _score_support(current_price: float, sr_levels: Dict,
                   df: pd.DataFrame) -> float:
    """支撑维度评分 0-100"""
    score = 50.0

    supports = sr_levels.get("all_supports", [])
    resistances = sr_levels.get("all_resistances", [])

    if supports and current_price > 0:
        nearest_sup = supports[0].get("price", 0)
        if nearest_sup > 0:
            dist_pct = (current_price - nearest_sup) / current_price * 100
            if dist_pct < 2:
                score += 20  # 紧贴支撑
            elif dist_pct < 5:
                score += 10
            elif dist_pct > 15:
                score -= 10  # 远离支撑

    # 布林带位置
    if "BB_lower" in df.columns and "BB_upper" in df.columns:
        bb_lower = float(df["BB_lower"].iloc[-1])
        bb_upper = float(df["BB_upper"].iloc[-1])
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_pos = (current_price - bb_lower) / bb_range
            if bb_pos < 0.3:
                score += 15  # 靠近下轨
            elif bb_pos > 0.8:
                score -= 10  # 靠近上轨

    return max(0, min(100, score))


def _score_risk_reward(suggestion: Dict) -> float:
    """风险收益比评分 0-100"""
    rr = suggestion.get("risk_reward_ratio", 0)
    if rr >= 3.0:
        return 95
    elif rr >= 2.0:
        return 80
    elif rr >= 1.5:
        return 65
    elif rr >= 1.0:
        return 45
    elif rr > 0:
        return 25
    return 10


def _score_signal(comp_signals: Dict) -> float:
    """信号桶命中数评分 0-100"""
    buy_signals = comp_signals.get("buy_signals", [])
    if not buy_signals:
        return 20
    latest = buy_signals[-1]
    n_buckets = len(latest.get("buckets", []))
    confidence = latest.get("confidence", 0)
    components = latest.get("confidence_components")
    if (
        isinstance(components, dict)
        and components.get("semantics")
        == "signal_evidence_score_not_probability"
    ):
        if comp_signals.get("current_signal") != "买入":
            return 20
        return max(0, min(100, float(confidence)))
    return min(100, confidence + n_buckets * 5)


def compute_buy_score(
    analysis: Dict,
    df: pd.DataFrame,
    sr_levels: Dict,
    comp_signals: Dict,
    suggestion: Dict,
) -> Dict:
    """
    计算综合买入评估分数。

    返回:
        {
            "total_score": float,       # 0-100 综合得分
            "grade": str,               # A/B/C/D/F 等级
            "dimensions": {             # 各维度明细
                "trend": {"score": float, "weight": float},
                ...
            },
            "verdict": str,             # 一句话结论
        }
    """
    current_price = float(df["Close"].iloc[-1]) if len(df) > 0 else 0

    scores = {
        "trend":       _score_trend(analysis, df),
        "momentum":    _score_momentum(analysis, df),
        "volume":      _score_volume(analysis, df),
        "support":     _score_support(current_price, sr_levels, df),
        "risk_reward": _score_risk_reward(suggestion),
        "signal":      _score_signal(comp_signals),
    }

    total = sum(scores[k] * _SCORE_WEIGHTS[k] for k in _SCORE_WEIGHTS)
    total = round(total, 1)

    if total >= 80:
        grade, verdict = "A", "强烈建议买入，多维度共振确认，风险可控"
    elif total >= 65:
        grade, verdict = "B", "建议买入，信号较强但需注意个别维度风险"
    elif total >= 50:
        grade, verdict = "C", "谨慎买入，信号中等，建议轻仓试探"
    elif total >= 35:
        grade, verdict = "D", "不建议买入，信号偏弱，等待更好时机"
    else:
        grade, verdict = "F", "明确回避，信号极弱或风险过高"

    dimensions = {}
    dim_labels = {
        "trend": "趋势", "momentum": "动量", "volume": "量能",
        "support": "支撑", "risk_reward": "风险收益比", "signal": "信号强度",
    }
    for k, w in _SCORE_WEIGHTS.items():
        dimensions[k] = {
            "label": dim_labels[k],
            "score": round(scores[k], 1),
            "weight": w,
            "weighted": round(scores[k] * w, 1),
        }

    return {
        "total_score": total,
        "grade": grade,
        "dimensions": dimensions,
        "verdict": verdict,
    }


# ── ATR + 支撑压力位 止盈止损计算 ──────────────────────────────────────

def compute_price_targets(
    current_price: float,
    df: pd.DataFrame,
    sr_levels: Dict,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
) -> Dict:
    """
    基于 ATR + 支撑压力位计算止盈止损价位。

    返回:
        {
            "entry_price": float,
            "stop_loss": float,         # 止损价
            "stop_loss_pct": float,     # 止损百分比
            "stop_method": str,         # 止损依据说明
            "take_profit_1": float,     # 第一目标
            "tp1_pct": float,
            "tp1_method": str,
            "take_profit_2": float,     # 第二目标
            "tp2_pct": float,
            "tp2_method": str,
            "take_profit_3": float,     # 第三目标（激进）
            "tp3_pct": float,
            "tp3_method": str,
            "risk_reward_1": float,     # 风险收益比（对应 TP1）
            "risk_reward_2": float,
            "atr": float,
            "atr_mult": float,
        }
    """
    # 计算 ATR
    atr = 0.0
    if df is not None and len(df) >= 14:
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift(1)).abs(),
            (df["Low"] - df["Close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())

    # ATR 倍数（按板块调整）
    from .risk_management import BOARD_ATR_MULT, _cap_adjust
    base_mult = BOARD_ATR_MULT.get(board, 2.0)
    cap_adj = _cap_adjust(market_cap_yi)
    atr_mult = base_mult * cap_adj

    supports = sr_levels.get("all_supports", [])
    resistances = sr_levels.get("all_resistances", [])

    # ── 止损价：ATR 与支撑位取较近者 ──
    atr_stop = current_price - atr * atr_mult if atr > 0 else current_price * 0.95
    # 硬约束：止损不超过 12%，不少于 2%
    atr_stop = max(atr_stop, current_price * 0.88)
    atr_stop = min(atr_stop, current_price * 0.98)

    sup_stop = None
    stop_method = f"ATR×{atr_mult:.1f}"
    if supports:
        nearest_sup = supports[0].get("price", 0)
        if nearest_sup > 0:
            sup_stop = nearest_sup * 0.995  # 支撑位下方 0.5%
            # 选择 ATR 止损和支撑止损中较高者（更保守）
            if sup_stop > atr_stop and sup_stop < current_price * 0.98:
                stop_loss = sup_stop
                stop_method = f"支撑位{nearest_sup:.2f}下方0.5%"
            else:
                stop_loss = atr_stop
    else:
        stop_loss = atr_stop

    stop_loss = round(stop_loss, 4)
    stop_pct = (current_price - stop_loss) / current_price

    # ── 止盈价：阶梯目标 ──
    # TP1: 最近压力位 或 1:1.5 风险收益
    risk_dist = current_price - stop_loss
    tp1_rr = current_price + risk_dist * 1.5  # 1:1.5 RR

    if resistances:
        r1_price = resistances[0].get("price", 0)
        if r1_price > current_price * 1.01:
            tp1 = r1_price
            tp1_method = f"压力位{r1_price:.2f}"
        else:
            tp1 = tp1_rr
            tp1_method = "1:1.5 风险收益比"
    else:
        tp1 = tp1_rr
        tp1_method = "1:1.5 风险收益比"

    # TP2: 第二压力位 或 1:2.5 风险收益
    tp2_rr = current_price + risk_dist * 2.5

    if len(resistances) >= 2:
        r2_price = resistances[1].get("price", 0)
        if r2_price > tp1 * 1.005:
            tp2 = r2_price
            tp2_method = f"第二压力位{r2_price:.2f}"
        else:
            tp2 = tp2_rr
            tp2_method = "1:2.5 风险收益比"
    else:
        tp2 = tp2_rr
        tp2_method = "1:2.5 风险收益比"

    # TP3: 布林上轨 或 1:4 风险收益（激进目标）
    tp3_rr = current_price + risk_dist * 4.0
    if "BB_upper" in df.columns:
        bb_upper = float(df["BB_upper"].iloc[-1])
        if bb_upper > tp2 * 1.005:
            tp3 = bb_upper
            tp3_method = f"布林上轨{bb_upper:.2f}"
        else:
            tp3 = tp3_rr
            tp3_method = "1:4 风险收益比"
    else:
        tp3 = tp3_rr
        tp3_method = "1:4 风险收益比"

    rr1 = (tp1 - current_price) / risk_dist if risk_dist > 0 else 0
    rr2 = (tp2 - current_price) / risk_dist if risk_dist > 0 else 0

    return {
        "entry_price": round(current_price, 4),
        "stop_loss": stop_loss,
        "stop_loss_pct": round(stop_pct * 100, 2),
        "stop_method": stop_method,
        "take_profit_1": round(tp1, 4),
        "tp1_pct": round((tp1 - current_price) / current_price * 100, 2),
        "tp1_method": tp1_method,
        "take_profit_2": round(tp2, 4),
        "tp2_pct": round((tp2 - current_price) / current_price * 100, 2),
        "tp2_method": tp2_method,
        "take_profit_3": round(tp3, 4),
        "tp3_pct": round((tp3 - current_price) / current_price * 100, 2),
        "tp3_method": tp3_method,
        "risk_reward_1": round(rr1, 2),
        "risk_reward_2": round(rr2, 2),
        "atr": round(atr, 4),
        "atr_mult": round(atr_mult, 2),
    }


# ── 逐日操作建议 ────────────────────────────────────────────────────────

def generate_daily_plan(
    targets: Dict,
    buy_score: Dict,
    analysis: Dict,
    df: pd.DataFrame,
) -> List[Dict]:
    """
    生成买入后 T+0 ~ T+5 逐日操作建议。

    每天包含：
        day: int (0=买入日)
        title: str
        actions: List[str]
        watch_levels: Dict  (关键观察价位)
        position_advice: str (仓位建议)
    """
    entry = targets["entry_price"]
    sl = targets["stop_loss"]
    tp1 = targets["take_profit_1"]
    tp2 = targets["take_profit_2"]
    tp3 = targets["take_profit_3"]
    atr = targets["atr"]
    grade = buy_score["grade"]

    # 根据评级决定初始仓位
    if grade == "A":
        init_pos = "60-70%"
        add_pos = "加仓至80-90%"
    elif grade == "B":
        init_pos = "40-50%"
        add_pos = "加仓至60-70%"
    elif grade == "C":
        init_pos = "20-30%"
        add_pos = "加仓至40-50%"
    else:
        init_pos = "10-20%（试探仓）"
        add_pos = "不加仓，等待确认"

    # 计算关键价位
    entry_add = round(entry + atr * 0.5, 2) if atr > 0 else round(entry * 1.02, 2)
    breakeven_trail = round(entry + atr * 0.3, 2) if atr > 0 else round(entry * 1.01, 2)

    plan = []

    # T+0 买入日
    plan.append({
        "day": 0,
        "title": "T+0 买入日",
        "actions": [
            f"以 {entry:.2f} 附近分批建仓，初始仓位 {init_pos}",
            f"立即设置止损价 {sl:.2f}（{targets['stop_method']}，亏损 {targets['stop_loss_pct']:.1f}%）",
            "收盘前确认持仓成本和止损单是否挂好",
            "记录买入理由和当前市场情绪，后续复盘用",
        ],
        "watch_levels": {
            "入场价": entry,
            "止损价": sl,
        },
        "position_advice": f"建仓 {init_pos}",
    })

    # T+1
    plan.append({
        "day": 1,
        "title": "T+1 确认日",
        "actions": [
            f"若开盘高于 {breakeven_trail:.2f}，趋势确认，可{add_pos}",
            f"若回踩不破 {entry:.2f} 并放量反弹 → 加仓信号",
            f"若跌破 {sl:.2f} → 果断止损，不犹豫",
            "关注成交量是否维持或放大（缩量上涨需警惕）",
        ],
        "watch_levels": {
            "加仓触发": entry_add,
            "入场成本": entry,
            "止损价": sl,
        },
        "position_advice": f"确认趋势后{add_pos}，否则维持初始仓位",
    })

    # T+2
    trail_stop_1 = round(entry + (tp1 - entry) * 0.3, 2)
    plan.append({
        "day": 2,
        "title": "T+2 趋势跟踪",
        "actions": [
            f"若股价接近第一目标 {tp1:.2f}（+{targets['tp1_pct']:.1f}%），减仓 1/3 锁定利润",
            f"若持续走强，止损上移到 {trail_stop_1:.2f}（成本+30%浮盈保护）",
            "若横盘震荡无方向 → 维持仓位，耐心持有",
            f"若跌破入场价 {entry:.2f} → 减仓至试探仓位",
        ],
        "watch_levels": {
            "第一目标": tp1,
            "移动止损": trail_stop_1,
            "入场成本": entry,
        },
        "position_advice": "盈利减仓1/3，亏损减仓至底仓",
    })

    # T+3
    trail_stop_2 = round(entry + (tp1 - entry) * 0.6, 2)
    plan.append({
        "day": 3,
        "title": "T+3 利润管理",
        "actions": [
            f"若已到达 TP1 {tp1:.2f}，止损上移到 {trail_stop_2:.2f}（锁定60%浮盈）",
            f"若突破 TP1 向 TP2 {tp2:.2f}（+{targets['tp2_pct']:.1f}%）进发 → 持有剩余仓位",
            "注意观察 MACD 柱是否开始缩短（动量衰减信号）",
            "若出现明显卖出信号（顶背离/放量长上影）→ 全部止盈",
        ],
        "watch_levels": {
            "第二目标": tp2,
            "移动止损": trail_stop_2,
        },
        "position_advice": "动态调整止损，逐步锁定利润",
    })

    # T+4
    plan.append({
        "day": 4,
        "title": "T+4 高位评估",
        "actions": [
            f"若接近 TP2 {tp2:.2f} → 再减仓 1/3，只保留底仓博 TP3",
            f"激进目标 TP3: {tp3:.2f}（+{targets['tp3_pct']:.1f}%），需强趋势配合",
            "检查周线级别趋势是否仍然健康",
            "重新评估持仓理由是否仍然成立",
        ],
        "watch_levels": {
            "第二目标": tp2,
            "激进目标": tp3,
        },
        "position_advice": "TP2 附近减至底仓，TP3 为加分项",
    })

    # T+5
    plan.append({
        "day": 5,
        "title": "T+5 周期复盘",
        "actions": [
            "无论盈亏，进行一周复盘：入场理由是否正确？执行是否到位？",
            "若仍持仓：重新评估趋势强度，决定继续持有或全部了结",
            "若已止盈：记录成功模式，寻找下一个机会",
            "若已止损：分析失败原因，避免重复错误",
        ],
        "watch_levels": {},
        "position_advice": "复盘总结，更新交易日志",
    })

    return plan


# ── 完整操作计划入口 ────────────────────────────────────────────────────

def generate_buy_action_plan(
    df: pd.DataFrame,
    analysis: Dict,
    sr_levels: Dict,
    comp_signals: Dict,
    suggestion: Dict,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
    current_price: Optional[float] = None,
) -> Optional[Dict]:
    """
    完整买入操作计划生成器。

    常态化：无论当前建议是否为「买入」都生成计划（含买入置信度），
    由调用方据 confidence 判断是否适合买入；不再在非买入时返回 None。

    返回:
        {
            "buy_score": Dict,      # 综合买入评分
            "targets": Dict,        # 止盈止损价位
            "daily_plan": List,     # T+0 ~ T+5 逐日计划
            "confidence": Dict,     # 买入置信度结论（score/grade/suitable/label/color）
            "summary": str,         # 一句话摘要
        }
    """
    if df is None or df.empty:
        return None

    # 1. 综合买入评分
    buy_score = compute_buy_score(analysis, df, sr_levels, comp_signals, suggestion)

    # 2. 止盈止损价位（优先用实时现价 → 与看板“最新价”一致；否则退回最后日线收盘）
    if not (current_price and current_price > 0):
        current_price = float(df["Close"].iloc[-1])
    else:
        current_price = float(current_price)
    targets = compute_price_targets(
        current_price, df, sr_levels,
        board=board, market_cap_yi=market_cap_yi,
    )

    # 3. 逐日操作计划
    daily_plan = generate_daily_plan(targets, buy_score, analysis, df)

    # 4. 摘要
    grade = buy_score["grade"]
    total = buy_score["total_score"]
    sl_pct = targets["stop_loss_pct"]
    tp1_pct = targets["tp1_pct"]
    rr = targets["risk_reward_1"]
    summary = (
        f"综合评分 {total:.0f}/100（{grade}级）| "
        f"止损 -{sl_pct:.1f}% | 目标1 +{tp1_pct:.1f}% | "
        f"风险收益比 1:{rr:.1f}"
    )

    # 5. 买入置信度结论（据综合评分判定是否适合买入）
    _g = buy_score["grade"]
    if _g in ("A", "B"):
        _suit, _color = "适合买入", "#26A69A"
    elif _g == "C":
        _suit, _color = "谨慎/轻仓试探", "#FF9800"
    else:
        _suit, _color = "不适合买入", "#EF5350"
    confidence = {
        "score": buy_score["total_score"],   # 0~100 买入置信度
        "grade": _g,
        "suitable": _g in ("A", "B"),
        "label": _suit,
        "color": _color,
    }

    return {
        "buy_score": buy_score,
        "targets": targets,
        "daily_plan": daily_plan,
        "confidence": confidence,
        "summary": summary,
    }


# ── 持仓复盘 / 诊断 ──────────────────────────────────────────────────────

def review_position(
    buy_price: float,
    buy_date,
    df: pd.DataFrame,
    targets: Dict,
    buy_time=None,
    analysis: Optional[Dict] = None,
) -> Dict:
    """录入实际买入价/日期，对比当前行情，给出操作评价与后续建议。

    参数：
        buy_price : 实际买入价
        buy_date  : 买入日期（date 或 'YYYY-MM-DD'）
        df        : 含 OHLCV 的指标 DataFrame（DatetimeIndex，截至最新）
        targets   : compute_price_targets 输出（止损/TP1-3/entry_price）
        buy_time  : 买入时间（仅记录，可选）
        analysis  : 分析结果（可选，用于趋势判断）

    返回：{valid, current_price, pnl_pct, days_held, cal_days, max_gain, max_dd,
           zone, touched_stop, reached_tp1/2/3, evaluation[], advice, advice_action, advice_color}
    """
    out = {"valid": False}
    if df is None or df.empty or not buy_price or buy_price <= 0:
        return out

    current = float(df["Close"].iloc[-1])
    pnl_pct = (current - buy_price) / buy_price * 100.0

    # 买入后价格路径
    try:
        bd = pd.to_datetime(buy_date)
    except Exception:
        bd = df.index[-1]
    since = df[df.index >= bd]
    if since.empty:
        since = df.tail(1)
    days_held = max(0, len(since) - 1)                       # 已观察交易日(不含买入当日)
    cal_days = max(0, (df.index[-1] - bd).days)
    hi = float(since["High"].max()) if "High" in since else current
    lo = float(since["Low"].min()) if "Low" in since else current
    max_gain = (hi - buy_price) / buy_price * 100.0
    max_dd = (lo - buy_price) / buy_price * 100.0

    sl = float(targets.get("stop_loss", buy_price * 0.92))
    tp1 = float(targets.get("take_profit_1", buy_price * 1.05))
    tp2 = float(targets.get("take_profit_2", buy_price * 1.10))
    tp3 = float(targets.get("take_profit_3", buy_price * 1.20))
    plan_entry = float(targets.get("entry_price", buy_price))

    touched_stop = lo <= sl
    reached_tp1, reached_tp2, reached_tp3 = hi >= tp1, hi >= tp2, hi >= tp3

    if current <= sl:
        zone = "已破止损"
    elif current >= tp3:
        zone = "超目标3"
    elif current >= tp2:
        zone = "目标2~3"
    elif current >= tp1:
        zone = "目标1~2"
    elif current >= buy_price:
        zone = "成本~目标1"
    else:
        zone = "止损~成本(浮亏)"

    # 动量是否转弱（用于建议微调）
    momentum_weak = False
    try:
        if "MACD_hist" in df.columns and len(df) >= 2:
            h0, h1 = float(df["MACD_hist"].iloc[-1]), float(df["MACD_hist"].iloc[-2])
            if h0 < h1 and h0 < 0:
                momentum_weak = True
        rsi_col = "RSI_14" if "RSI_14" in df.columns else ("RSI" if "RSI" in df.columns else None)
        if rsi_col and float(df[rsi_col].iloc[-1]) >= 70:
            momentum_weak = True
    except Exception:
        pass

    # 评价操作
    evaluation = []
    if buy_price <= plan_entry * 1.005:
        evaluation.append(f"入场价 {buy_price:.2f} 不高于计划入场价 {plan_entry:.2f}，位置合理。")
    else:
        evaluation.append(f"入场价 {buy_price:.2f} 高于计划入场价 {plan_entry:.2f}（追高 {(buy_price/plan_entry-1)*100:.1f}%），成本偏高。")
    _pl = "浮盈" if pnl_pct >= 0 else "浮亏"
    evaluation.append(f"持有 {days_held} 个交易日（{cal_days} 天），当前{_pl} {pnl_pct:+.2f}%；期间最大浮盈 {max_gain:+.1f}%、最大回撤 {max_dd:+.1f}%。")
    if touched_stop and current > sl:
        evaluation.append(f"⚠️ 期间盘中曾跌破止损价 {sl:.2f} 后回升——若当时未止损属违纪，注意执行力。")
    if reached_tp1 and current < tp1:
        evaluation.append(f"曾触及目标1 {tp1:.2f} 但已回落，未及时锁利。")
    if days_held > 5:
        evaluation.append("已超出 T+0~T+5 计划周期，应重新评估持有理由。")

    # 后续操作建议
    if current <= sl:
        action, color = "止损离场", "#EF5350"
        advice = f"已跌破止损价 {sl:.2f}，按纪律止损离场，不抱侥幸、不向下补仓。"
    elif current >= tp2:
        action, color = "止盈减仓", "#26A69A"
        advice = f"已达目标2 {tp2:.2f}，建议再减仓 1/3、仅留底仓博目标3 {tp3:.2f}，止损上移至 {tp1:.2f} 锁定利润。"
    elif current >= tp1:
        action, color = "减仓锁利", "#26A69A"
        advice = f"已达目标1 {tp1:.2f}，减仓 1/3 锁利，止损上移至成本 {buy_price:.2f} 保本，剩余看向目标2 {tp2:.2f}。"
    elif current >= buy_price:
        if momentum_weak:
            action, color = "持有偏减", "#FF9800"
            advice = f"浮盈但动量转弱（MACD 柱缩短/RSI 偏高），可部分止盈，止损上移至成本 {buy_price:.2f} 保本。"
        else:
            action, color = "持有", "#2196F3"
            advice = f"浮盈持有，止损上移至成本 {buy_price:.2f} 保本；接近目标1 {tp1:.2f} 再减仓 1/3。"
    else:  # 浮亏，介于止损与成本之间
        if momentum_weak or days_held >= 5:
            action, color = "减仓/严守止损", "#FF9800"
            advice = f"浮亏 {pnl_pct:.1f}% 但未破止损 {sl:.2f}，严守止损；反弹无量或趋势走弱则减仓，切勿向下补仓。"
        else:
            action, color = "持有观察", "#FF9800"
            advice = f"浮亏但仍在止损 {sl:.2f} 之上，持有观察；一旦跌破止损坚决离场。"

    return {
        "valid": True,
        "current_price": round(current, 2),
        "buy_price": round(buy_price, 2),
        "pnl_pct": round(pnl_pct, 2),
        "days_held": days_held,
        "cal_days": cal_days,
        "max_gain": round(max_gain, 1),
        "max_dd": round(max_dd, 1),
        "zone": zone,
        "touched_stop": bool(touched_stop),
        "reached_tp1": bool(reached_tp1),
        "reached_tp2": bool(reached_tp2),
        "reached_tp3": bool(reached_tp3),
        "evaluation": evaluation,
        "advice": advice,
        "advice_action": action,
        "advice_color": color,
    }
