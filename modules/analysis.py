"""
analysis.py — 趋势判断规则引擎
基于 EMA 排列、隧道位置、斐波那契位给出操作建议
"""

import pandas as pd
import numpy as np
from .technical import INNER_EMAS, OUTER_EMAS, calc_emas, calc_fibonacci
from .indicator_interpreter import interpret_all_indicators
from .support_resistance import calc_daily_levels, calc_weekly_levels
from .risk_management import dynamic_stop_loss


# ─── 趋势判断 ────────────────────────────────────────────────────────────────

def get_ema_alignment(df: pd.DataFrame) -> dict:
    """
    判断 EMA 排列状态（取最后一行）
    返回：trend_direction / inner_trend / outer_trend / tunnel_signal
    """
    if df.empty:
        return {}

    last = df.iloc[-1]

    def get_ema(n):
        col = f"EMA_{n}"
        return last.get(col, np.nan)

    close = last["Close"]

    # 内隧道均值
    inner_vals = [get_ema(n) for n in INNER_EMAS]

    # 数据不足回退：若 EMA_233 为 NaN（数据 < 233根），回退到 [55, 144]
    effective_outer = OUTER_EMAS
    if len(df) < max(OUTER_EMAS) * 1.1:
        effective_outer = [n for n in OUTER_EMAS if n <= len(df) * 0.8]
        if not effective_outer:
            effective_outer = [55]  # 最小回退
    outer_vals = [get_ema(n) for n in effective_outer]

    inner_vals = [v for v in inner_vals if not np.isnan(v)]
    outer_vals = [v for v in outer_vals if not np.isnan(v)]

    if not inner_vals or not outer_vals:
        return {}

    inner_top    = max(inner_vals)
    inner_bottom = min(inner_vals)
    outer_top    = max(outer_vals)
    outer_bottom = min(outer_vals)

    # ── 趋势方向（根据外隧道排列） ──────────────────────────────────────
    outer_sorted_asc  = sorted(outer_vals) == outer_vals  # 从小到大排列（EMA55最小）
    outer_sorted_desc = sorted(outer_vals, reverse=True) == outer_vals

    # 检查内隧道是否全部在外隧道上方
    inner_above_outer = inner_bottom > outer_top
    inner_below_outer = inner_top < outer_bottom

    # EMA8 > EMA13 > EMA21 → 内隧道多头排列
    inner_bull = get_ema(8) > get_ema(13) > get_ema(21)
    inner_bear = get_ema(8) < get_ema(13) < get_ema(21)

    # 外隧道多头 = EMA55 > EMA233（精简后）
    ema_55 = get_ema(55)
    ema_233 = get_ema(233)
    # 兼容旧数据：如果没有 EMA_233 但有 EMA_288，用 EMA_288
    if np.isnan(ema_233):
        ema_233 = get_ema(288)
    outer_bull = ema_55 > ema_233 if not np.isnan(ema_55) and not np.isnan(ema_233) else None

    # ── 价格相对隧道位置 ─────────────────────────────────────────────────
    if close > outer_top:
        tunnel_position = "价格在外隧道上方"
        strength = "强势多头"
    elif close > inner_top:
        tunnel_position = "价格在内外隧道之间（上方）"
        strength = "多头"
    elif close >= inner_bottom and close <= inner_top:
        tunnel_position = "价格穿越内隧道"
        strength = "回调区"
    elif close > outer_bottom and close < inner_bottom:
        tunnel_position = "价格在内外隧道之间（下方）"
        strength = "偏弱"
    elif close < outer_bottom:
        tunnel_position = "价格在外隧道下方"
        strength = "弱势空头"
    else:
        tunnel_position = "震荡区"
        strength = "震荡"

    # ── 内隧道穿越外隧道信号 ─────────────────────────────────────────────
    if inner_above_outer:
        crossover_signal = "内隧道在外隧道上方（趋势延续）"
    elif inner_below_outer:
        crossover_signal = "内隧道在外隧道下方（空头格局）"
    else:
        crossover_signal = "内外隧道交织（趋势转换区）"

    # ── 综合趋势方向（放宽版：Close > EMA55 + 内隧道多头即为多头）────────
    # 外隧道位置仅作为强度加成，不再是多头硬性门槛
    if close > ema_55 and inner_bull:
        trend = "多头"
        trend_emoji = "🟢"
        # 强度加成：外隧道上方 → 强势多头
        if close > outer_top:
            strength = "强势多头"
    elif close < outer_bottom and not inner_bull:
        trend = "空头"
        trend_emoji = "🔴"
    elif close < ema_55 and inner_bear:
        trend = "空头"
        trend_emoji = "🔴"
    else:
        trend = "震荡"
        trend_emoji = "🟡"

    # ── EMA具体数值 ─────────────────────────────────────────────────────
    ema_values = {f"EMA_{n}": round(get_ema(n), 2) if not np.isnan(get_ema(n)) else None
                  for n in INNER_EMAS + OUTER_EMAS}

    return {
        "trend":            trend,
        "trend_emoji":      trend_emoji,
        "strength":         strength,
        "tunnel_position":  tunnel_position,
        "crossover_signal": crossover_signal,
        "inner_bull":       inner_bull,
        "inner_bear":       inner_bear,
        "outer_bull":       outer_bull,
        "inner_top":        inner_top,
        "inner_bottom":     inner_bottom,
        "outer_top":        outer_top,
        "outer_bottom":     outer_bottom,
        "close":            close,
        "ema_values":       ema_values,           # EMA具体数值
        "inner_emas":       INNER_EMAS,           # 内隧道周期列表
        "outer_emas":       OUTER_EMAS,           # 外隧道周期列表
    }


def get_fib_support_resistance(df: pd.DataFrame) -> dict:
    """找出当前价格最近的斐波那契支撑/压力位"""
    from .technical import calc_fibonacci
    fib = calc_fibonacci(df)
    close = df["Close"].iloc[-1]
    levels = sorted(fib["levels"].items(), key=lambda x: x[1])

    support    = None
    resistance = None
    support_lvl = None
    resistance_lvl = None

    for lvl, price in levels:
        if price <= close:
            support = price
            support_lvl = lvl
        elif price > close and resistance is None:
            resistance = price
            resistance_lvl = lvl

    # 计算回撤百分比
    total_range = fib["high"] - fib["low"]
    if total_range > 0 and support:
        support_pct = (support - fib["low"]) / total_range * 100
    else:
        support_pct = None

    if total_range > 0 and resistance:
        resistance_pct = (resistance - fib["low"]) / total_range * 100
    else:
        resistance_pct = None

    return {
        "fib_support":      support,
        "fib_support_lvl":  support_lvl,
        "fib_support_pct":  round(support_pct, 1) if support_pct else None,  # 回撤百分比
        "fib_resistance":   resistance,
        "fib_resistance_lvl": resistance_lvl,
        "fib_resistance_pct": round(resistance_pct, 1) if resistance_pct else None,
        "fib_high":         fib["high"],
        "fib_low":          fib["low"],
        "fib_range":        total_range,  # 总区间
    }


def get_valuation_position(pe, pe_history_pct=None) -> dict:
    """
    估值位置判断
    pe_history_pct: 历史百分位（0~1），若无则用绝对 PE 估算
    """
    if pe is None:
        return {"valuation": "数据不足", "valuation_color": "#888888"}

    # 无历史百分位时用经验值
    if pe_history_pct is not None:
        if pe_history_pct < 0.2:
            label, color = "历史低估区", "#0ECB81"
        elif pe_history_pct < 0.4:
            label, color = "偏低估", "#52B788"
        elif pe_history_pct < 0.6:
            label, color = "历史中位", "#FFD700"
        elif pe_history_pct < 0.8:
            label, color = "偏高估", "#FFA500"
        else:
            label, color = "历史高估区", "#F6465D"
    else:
        if pe < 10:
            label, color = "低PE（可能低估）", "#0ECB81"
        elif pe < 20:
            label, color = "合理估值区间", "#52B788"
        elif pe < 30:
            label, color = "中等估值", "#FFD700"
        elif pe < 50:
            label, color = "偏高估值", "#FFA500"
        else:
            label, color = "高估值", "#F6465D"

    return {"valuation": label, "valuation_color": color}


def get_operation_advice(ema_info: dict, fib_info: dict, rsi_last: float = None) -> dict:
    """
    生成操作建议
    """
    trend    = ema_info.get("trend", "震荡")
    strength = ema_info.get("strength", "")

    # RSI 超买超卖
    rsi_note = ""
    if rsi_last is not None:
        if rsi_last > 75:
            rsi_note = "RSI超买，注意回调风险"
        elif rsi_last < 25:
            rsi_note = "RSI超卖，关注反弹机会"

    if trend == "多头":
        if strength == "强势多头":
            advice = "适合进攻：趋势强劲，可逢回调内隧道附近加仓"
            advice_color = "#0ECB81"
            action = "进攻"
        else:
            advice = "谨慎追多：多头但强度一般，等待内隧道支撑确认"
            advice_color = "#52B788"
            action = "观望偏多"
    elif trend == "空头":
        advice = "防守为主：空头趋势，建议轻仓或离场观望"
        advice_color = "#F6465D"
        action = "防守"
    else:
        advice = "观望为主：震荡格局，等待方向选择，关注隧道突破"
        advice_color = "#FFD700"
        action = "观望"

    if rsi_note:
        advice += f"；{rsi_note}"

    return {
        "advice":       advice,
        "advice_color": advice_color,
        "action":       action,
    }


# ─── 布林带分析 ────────────────────────────────────────────────────────────────

def get_bollinger_analysis(df: pd.DataFrame) -> dict:
    """
    布林带分析：上轨压力、下轨支撑、带宽趋势
    """
    if df.empty or "BB_upper" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    close = last["Close"]
    bb_upper = last["BB_upper"]
    bb_lower = last["BB_lower"]
    bb_middle = last["BB_middle"]
    bb_bandwidth = last["BB_bandwidth"]

    # 带宽趋势（判断波动性）
    bandwidth_prev = prev.get("BB_bandwidth", bb_bandwidth)
    bandwidth_trend = "扩张" if bb_bandwidth > bandwidth_prev else "收缩"

    # 价格在布林带中的位置
    if close > bb_upper:
        position = "突破上轨"
        signal = "超买"
        pressure = close  # 当前价格就是压力
    elif close < bb_lower:
        position = "跌破下轨"
        signal = "超卖"
        support = close  # 当前价格就是支撑
    elif close > bb_middle:
        position = "中轨上方"
        signal = "偏多"
    else:
        position = "中轨下方"
        signal = "偏空"

    # 计算带宽百分位（判断极端情况）
    bandwidth_history = df["BB_bandwidth"].dropna()
    bandwidth_pct = (bb_bandwidth - bandwidth_history.min()) / (bandwidth_history.max() - bandwidth_history.min()) if len(bandwidth_history) > 1 else 0.5

    # 布林带收窄/扩张信号
    squeeze_threshold = 0.15  # 带宽历史百分位低于15%认为极度收窄（A股波动较大，阈值适度放宽）
    expansion_threshold = 0.9  # 带宽历史百分位高于90%认为极度扩张

    if bandwidth_pct < squeeze_threshold:
        squeeze_signal = "极度收窄，警惕突破"
    elif bandwidth_pct < 0.25:
        squeeze_signal = "收窄中"
    elif bandwidth_pct > expansion_threshold:
        squeeze_signal = "极度扩张，警惕反转"
    elif bandwidth_pct > 0.75:
        squeeze_signal = "扩张中"
    else:
        squeeze_signal = "常态波动"

    return {
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_middle": bb_middle,
        "bb_bandwidth": bb_bandwidth,
        "bb_position": position,
        "bb_signal": signal,
        "bb_bandwidth_trend": bandwidth_trend,
        "bb_bandwidth_pct": bandwidth_pct,
        "bb_squeeze_signal": squeeze_signal,
    }


# ─── KDJ分析 ───────────────────────────────────────────────────────────────────

def get_kdj_analysis(df: pd.DataFrame) -> dict:
    """
    KDJ分析：金叉死叉、超买超卖
    """
    if df.empty or "KDJ_K" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    k = last["KDJ_K"]
    d = last["KDJ_D"]
    j = last["KDJ_J"]

    k_prev = prev["KDJ_K"]
    d_prev = prev["KDJ_D"]

    # 金叉死叉判断
    golden_cross = k_prev <= d_prev and k > d  # K线上穿D线
    dead_cross = k_prev >= d_prev and k < d  # K线下穿D线

    # 超买超卖判断
    overbought = k > 80 and d > 80
    oversold = k < 20 and d < 20

    # J值极端情况
    j_extreme_high = j > 100
    j_extreme_low = j < 0

    # 综合信号
    if golden_cross and oversold:
        signal = "金叉超卖区，强烈买入信号"
        strength = 3
    elif golden_cross:
        signal = "金叉，买入信号"
        strength = 2
    elif dead_cross and overbought:
        signal = "死叉超买区，强烈卖出信号"
        strength = -3
    elif dead_cross:
        signal = "死叉，卖出信号"
        strength = -2
    elif overbought:
        signal = "超买区，注意回调"
        strength = -1
    elif oversold:
        signal = "超卖区，关注反弹"
        strength = 1
    else:
        signal = "震荡整理"
        strength = 0

    return {
        "kdj_k": k,
        "kdj_d": d,
        "kdj_j": j,
        "kdj_golden_cross": golden_cross,
        "kdj_dead_cross": dead_cross,
        "kdj_overbought": overbought,
        "kdj_oversold": oversold,
        "kdj_signal": signal,
        "kdj_strength": strength,
        "kdj_j_extreme_high": j_extreme_high,
        "kdj_j_extreme_low": j_extreme_low,
    }


# ─── 交易区间计算 ──────────────────────────────────────────────────────────────

def calculate_trading_zones(df: pd.DataFrame, ema_info: dict, fib_info: dict, bb_info: dict,
                            board: str = "其他", market_cap_yi: float = None) -> dict:
    """
    精确计算交易区间 - 增强版
    - 买入区间: 基于EMA支撑 + 布林下轨 + 斐波那契 + VWAP
    - 卖出区间: 基于EMA压力 + 布林上轨 + 斐波那契 + 波动率
    - 止损位: 基于ATR、关键斐波那契位、历史低点
    - 多目标位: 分阶段止盈
    """
    if df.empty:
        return {}

    last = df.iloc[-1]
    close = last["Close"]
    atr = last.get("ATR", close * 0.02)

    # ========== 支撑位候选（权重排序）==========
    supports = []
    support_weights = {}

    # 1. 斐波那契支撑（最高权重）
    if fib_info.get("fib_support"):
        fib_s = fib_info["fib_support"]
        supports.append(fib_s)
        support_weights[fib_s] = 3.0

    # 2. EMA内隧道下轨
    if ema_info.get("inner_bottom"):
        ema_s = ema_info["inner_bottom"]
        supports.append(ema_s)
        support_weights[ema_s] = 2.5

    # 3. EMA外隧道下轨
    if ema_info.get("outer_bottom"):
        outer_s = ema_info["outer_bottom"]
        supports.append(outer_s)
        support_weights[outer_s] = 2.0

    # 4. 布林带下轨
    if bb_info.get("bb_lower"):
        bb_l = bb_info["bb_lower"]
        supports.append(bb_l)
        support_weights[bb_l] = 2.0

    # 5. VWAP
    if "VWAP" in df.columns:
        vwap = last["VWAP"]
        if vwap < close:
            supports.append(vwap)
            support_weights[vwap] = 1.5

    # 6. 前低点
    recent_lows = df["Low"].tail(20).nsmallest(3)
    for low in recent_lows:
        if low < close * 0.99:
            supports.append(low)
            support_weights[low] = 1.0

    # ========== 压力位候选（权重排序）==========
    resistances = []
    resistance_weights = {}

    # 1. 斐波那契压力
    if fib_info.get("fib_resistance"):
        fib_r = fib_info["fib_resistance"]
        resistances.append(fib_r)
        resistance_weights[fib_r] = 3.0

    # 2. EMA内隧道上轨
    if ema_info.get("inner_top"):
        ema_r = ema_info["inner_top"]
        resistances.append(ema_r)
        resistance_weights[ema_r] = 2.5

    # 3. EMA外隧道上轨
    if ema_info.get("outer_top"):
        outer_r = ema_info["outer_top"]
        resistances.append(outer_r)
        resistance_weights[outer_r] = 2.0

    # 4. 布林带上轨
    if bb_info.get("bb_upper"):
        bb_u = bb_info["bb_upper"]
        resistances.append(bb_u)
        resistance_weights[bb_u] = 2.0

    # 5. VWAP（如果价格在下方）
    if "VWAP" in df.columns:
        vwap = last["VWAP"]
        if vwap > close:
            resistances.append(vwap)
            resistance_weights[vwap] = 1.5

    # 6. 前高点
    recent_highs = df["High"].tail(20).nlargest(3)
    for high in recent_highs:
        if high > close * 1.01:
            resistances.append(high)
            resistance_weights[high] = 1.0

    # ========== 计算加权支撑位 ==========
    if supports:
        # 按距离当前价格排序，取最近的几个
        supports_sorted = sorted(supports, key=lambda x: abs(x - close))
        top_supports = supports_sorted[:4]

        # 计算加权平均支撑位
        weighted_sum = sum(s * support_weights.get(s, 1) for s in top_supports)
        weight_sum = sum(support_weights.get(s, 1) for s in top_supports)
        primary_support = weighted_sum / weight_sum

        # 买入区间：强支撑 ± ATR
        buy_zone_low = min(top_supports) - atr * 0.5
        buy_zone_high = primary_support + atr * 0.3
    else:
        # 无支撑时的默认值
        primary_support = close * 0.95
        buy_zone_low = close * 0.93
        buy_zone_high = close * 0.97

    # ========== 计算加权压力位 ==========
    if resistances:
        # 按距离当前价格排序
        resistances_sorted = sorted(resistances, key=lambda x: abs(x - close))
        top_resistances = resistances_sorted[:4]

        # 计算加权平均压力位
        weighted_sum = sum(r * resistance_weights.get(r, 1) for r in top_resistances)
        weight_sum = sum(resistance_weights.get(r, 1) for r in top_resistances)
        primary_resistance = weighted_sum / weight_sum

        # 卖出区间 - 确保 high > low
        sell_zone_low = close + atr * 0.3
        sell_zone_high = max(primary_resistance - atr * 0.2, sell_zone_low + 0.01)
    else:
        primary_resistance = close * 1.05
        sell_zone_low = close * 1.02
        sell_zone_high = close * 1.05

    # ========== 多层级止盈位 ==========
    if resistances:
        resistances_sorted = sorted(resistances)
        take_profit_1 = resistances_sorted[0] if len(resistances_sorted) > 0 else close * 1.03
        take_profit_2 = resistances_sorted[1] if len(resistances_sorted) > 1 else close * 1.06
        take_profit_3 = resistances_sorted[2] if len(resistances_sorted) > 2 else close * 1.10
    else:
        take_profit_1 = close + 2 * atr
        take_profit_2 = close + 3 * atr
        take_profit_3 = close + 5 * atr

    # ========== 止损位（多重保护）==========
    stop_loss_candidates = []

    # 1. 支撑位下方ATR
    if supports:
        stop_loss_candidates.append(min(supports) - atr)

    # 2. 近期最低点
    recent_low = df["Low"].tail(10).min()
    stop_loss_candidates.append(recent_low - atr * 0.5)

    # 3. 板块自适应止损（替代硬编码百分比）
    ds = dynamic_stop_loss(close, atr, board=board, market_cap_yi=market_cap_yi,
                           direction="long", fallback_pct=0.05)
    if ds["stop_price"] is not None:
        stop_loss_candidates.append(ds["stop_price"])
    else:
        stop_loss_candidates.append(close * 0.95)

    # 取最紧的止损（保护本金）
    stop_loss = max(stop_loss_candidates) if stop_loss_candidates else close * 0.95

    # 使用 dynamic_stop_loss 的硬约束（2%~12%）替代之前的硬编码 8%
    min_stop = close * 0.88   # 最宽 12%
    max_stop = close * 0.98   # 最紧 2%
    stop_loss = min(max_stop, max(min_stop, stop_loss))

    # ========== 计算盈亏比和建议仓位 ==========
    risk = close - stop_loss
    reward_1 = take_profit_1 - close
    reward_2 = take_profit_2 - close

    rr_ratio_1 = reward_1 / risk if risk > 0 else 0
    rr_ratio_2 = reward_2 / risk if risk > 0 else 0

    # 仓位建议（基于风险回报比）
    if rr_ratio_1 >= 3:
        position_size = "20-25%"
    elif rr_ratio_1 >= 2:
        position_size = "15-20%"
    elif rr_ratio_1 >= 1.5:
        position_size = "10-15%"
    else:
        position_size = "<10%"

    # ========== 信心评分 ==========
    confidence_score = 0
    confidence_factors = []

    # 支撑位数量
    if len(supports) >= 3:
        confidence_score += 20
        confidence_factors.append("多重支撑")
    elif len(supports) >= 1:
        confidence_score += 10

    # 压力位清晰度
    if len(resistances) >= 2:
        confidence_score += 15
        confidence_factors.append("压力明确")

    # 盈亏比
    if rr_ratio_1 >= 2:
        confidence_score += 25
        confidence_factors.append("盈亏比优秀")
    elif rr_ratio_1 >= 1.5:
        confidence_score += 15

    # 趋势方向
    if ema_info.get("trend") == "多头":
        confidence_score += 20
        confidence_factors.append("趋势配合")
    elif ema_info.get("trend") == "空头":
        confidence_score -= 10

    # 布林带位置
    if bb_info.get("bb_position") == "中轨上方":
        confidence_score += 10
    elif bb_info.get("bb_position") == "跌破下轨":
        confidence_score += 15  # 超卖反弹机会

    confidence_score = max(0, min(100, confidence_score))

    return {
        # 核心交易区间
        "buy_zone_low": buy_zone_low,
        "buy_zone_high": buy_zone_high,
        "buy_zone_center": (buy_zone_low + buy_zone_high) / 2,
        "sell_zone_low": sell_zone_low,
        "sell_zone_high": sell_zone_high,
        "sell_zone_center": (sell_zone_low + sell_zone_high) / 2,

        # 止损止盈
        "stop_loss": stop_loss,
        "stop_loss_pct": (close - stop_loss) / close,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "take_profit_3": take_profit_3,
        "take_profit_pct_1": (take_profit_1 - close) / close,
        "take_profit_pct_2": (take_profit_2 - close) / close,
        "take_profit_pct_3": (take_profit_3 - close) / close,

        # 盈亏比
        "risk_amount": risk,
        "reward_amount_1": reward_1,
        "reward_amount_2": reward_2,
        "risk_reward_ratio": rr_ratio_1,
        "risk_reward_ratio_2": rr_ratio_2,

        # 支撑压力位详情
        "primary_support": primary_support if supports else None,
        "primary_resistance": primary_resistance if resistances else None,
        "support_count": len(supports),
        "resistance_count": len(resistances),
        "support_levels": sorted(supports)[:5] if supports else [],
        "resistance_levels": sorted(resistances)[:5] if resistances else [],

        # 建议
        "position_size": position_size,
        "confidence_score": confidence_score,
        "confidence_level": "高" if confidence_score >= 70 else ("中" if confidence_score >= 40 else "低"),
        "confidence_factors": confidence_factors,

        # ATR
        "atr": atr,
        "atr_pct": atr / close,
    }


# ─── 趋势强度评分 ──────────────────────────────────────────────────────────────

def calculate_trend_strength(ema_info: dict, macd_info: dict, kdj_info: dict, bb_info: dict,
                              df: pd.DataFrame = None,
                              benchmark_df: pd.DataFrame = None,
                              northbound_score: float = None,
                              mtf_info: dict = None,
                              divergence_info: dict = None) -> dict:
    """
    综合趋势强度评分 (0-100)，基准 45（A 股长期略偏空，避免单一信号轻易推到强势档）

    维度（v3 新增多周期确认）：
        EMA 排列   ±15
        MACD       ±15
        KDJ        ±10
        布林带     ±7
        量能       ±8
        动量       ±8
        北向资金   ±7
        多周期确认 ±8   (v3 新增；mtf_info 提供时启用)

    参数:
        df:              带 Volume / Close 的 DataFrame，启用量能 + 动量评分
        benchmark_df:    沪深 300 等基准；提供时动量按相对强度打分
        northbound_score: -1..+1 标量，由 factor_screener.get_northbound_score 提供
    """
    score = 45  # 基准分（旧版是 50；A 股长期略偏空，下移 5 分避免"中性"虚高）
    signals = []

    # EMA趋势评分 (±15分；旧版 ±25 太重，单一 EMA 排列就能推到 75)
    if ema_info.get("trend") == "多头":
        score += 12
        if ema_info.get("inner_bull"):
            score += 3
            signals.append("EMA多头排列")
        else:
            signals.append("EMA偏多头")
    elif ema_info.get("trend") == "空头":
        score -= 12
        if ema_info.get("inner_bear"):
            score -= 3
            signals.append("EMA空头排列")
        else:
            signals.append("EMA偏空头")
    else:
        signals.append("EMA震荡")

    # MACD评分 (±15分)
    if macd_info.get("macd_bullish"):
        score += 12
        signals.append("MACD金叉")
        # 红柱扩张额外 +3
        if macd_info.get("hist_expanding"):
            score += 3
    elif macd_info.get("macd_bearish"):
        score -= 12
        signals.append("MACD死叉")
        if macd_info.get("hist_expanding"):
            score -= 3

    # KDJ评分 (±10分)
    kdj_strength = kdj_info.get("kdj_strength", 0)
    score += int(kdj_strength * 2.0)  # -6 到 +6 分（旧 ×3 给到 ±9）
    if kdj_info.get("kdj_golden_cross"):
        score += 4
        signals.append("KDJ金叉")
    elif kdj_info.get("kdj_dead_cross"):
        score -= 4
        signals.append("KDJ死叉")

    # 布林带评分 (±7分)
    if bb_info.get("bb_position") == "突破上轨":
        score += 5
        signals.append("突破布林上轨")
    elif bb_info.get("bb_position") == "跌破下轨":
        score -= 5
        signals.append("跌破布林下轨")
    elif bb_info.get("bb_position") == "中轨上方":
        score += 2
    elif bb_info.get("bb_position") == "中轨下方":
        score -= 2

    # ADX/SAR 趋势确认评分 (±5分；v3 新增)
    if df is not None and len(df) >= 2:
        # ADX: >25 表示有趋势，与EMA方向一致则加分
        if "ADX" in df.columns:
            adx_val = float(df["ADX"].iloc[-1])
            if adx_val > 25:
                if ema_info.get("trend") == "多头":
                    score += 3
                    signals.append(f"ADX趋势确认({adx_val:.0f})")
                elif ema_info.get("trend") == "空头":
                    score -= 3
                    signals.append(f"ADX趋势确认({adx_val:.0f})")
        # SAR: 方向与EMA一致则加分
        if "SAR" in df.columns:
            sar_val = float(df["SAR"].iloc[-1])
            close_val = float(df["Close"].iloc[-1])
            sar_bullish = close_val > sar_val
            if sar_bullish and ema_info.get("trend") == "多头":
                score += 2
                signals.append("SAR多头确认")
            elif not sar_bullish and ema_info.get("trend") == "空头":
                score -= 2
                signals.append("SAR空头确认")

    # 量能评分 (±8分；新增)
    if df is not None and "Volume" in df.columns and len(df) >= 20:
        try:
            recent_close = df["Close"].iloc[-5:]
            price_up = recent_close.iloc[-1] > recent_close.iloc[0]
            vol_5 = df["Volume"].iloc[-5:].mean()
            vol_20 = df["Volume"].iloc[-20:].mean()
            if vol_20 > 0:
                vol_ratio = vol_5 / vol_20
                if price_up and vol_ratio > 1.2:
                    score += 6
                    signals.append("放量上涨")
                elif (not price_up) and vol_ratio < 0.8:
                    score += 2
                    signals.append("缩量下跌（企稳）")
                elif price_up and vol_ratio < 0.8:
                    score -= 4
                    signals.append("缩量上涨（背离）")
                elif (not price_up) and vol_ratio > 1.2:
                    score -= 6
                    signals.append("放量下跌")
        except Exception:
            pass

    # 动量评分 (±8分；新增)
    if df is not None and len(df) >= 60:
        try:
            mom_5 = df["Close"].iloc[-1] / df["Close"].iloc[-6] - 1
            mom_60 = df["Close"].iloc[-1] / df["Close"].iloc[-61] - 1
            if benchmark_df is not None and len(benchmark_df) >= 60:
                # 相对强度：跑赢/跑输基准
                bm_5 = benchmark_df["Close"].iloc[-1] / benchmark_df["Close"].iloc[-6] - 1
                bm_60 = benchmark_df["Close"].iloc[-1] / benchmark_df["Close"].iloc[-61] - 1
                rel_5 = mom_5 - bm_5
                rel_60 = mom_60 - bm_60
                if rel_5 > 0.02:
                    score += 5
                    signals.append("5日跑赢基准")
                elif rel_5 < -0.02:
                    score -= 5
                if rel_60 > 0.05:
                    score += 3
                elif rel_60 < -0.05:
                    score -= 3
            else:
                # 无基准：按绝对涨幅
                if mom_5 > 0.03:
                    score += 5
                elif mom_5 < -0.03:
                    score -= 5
                if mom_60 > 0.10:
                    score += 3
                elif mom_60 < -0.10:
                    score -= 3
        except Exception:
            pass

    # 北向资金评分 (±7分；新增)
    if northbound_score is not None:
        nb = max(-1.0, min(1.0, float(northbound_score)))
        delta = int(round(nb * 7))
        if delta != 0:
            score += delta
            if delta > 0:
                signals.append(f"北向净流入+{delta}")
            else:
                signals.append(f"北向净流出{delta}")

    # 多周期趋势确认评分 (±8分；v3 新增)
    if mtf_info is not None:
        mtf_s = mtf_info.get("mtf_score", 0)
        if mtf_s == 2:
            score += 8
            signals.append("多周期共振多头")
        elif mtf_s == 1:
            score += 4
            signals.append("周线多头")
        elif mtf_s == -1:
            score -= 4
            signals.append("周线空头")
        elif mtf_s == -2:
            score -= 8
            signals.append("多周期共振空头")

    # 背离检测评分 (±8分；v3 新增)
    if divergence_info is not None:
        bull_div = divergence_info.get("bullish_regular", [])
        bear_div = divergence_info.get("bearish_regular", [])
        # 只关注最近的背离（end_idx 在最近 10 根 K 线内）
        data_len = len(df) if df is not None else 0
        recent_bull = [d for d in bull_div if data_len - d.get("end_idx", 0) <= 10]
        recent_bear = [d for d in bear_div if data_len - d.get("end_idx", 0) <= 10]
        if recent_bull:
            score += 8
            signals.append("近期底背离（看涨）")
        if recent_bear:
            score -= 8
            signals.append("近期顶背离（看跌）")

    # 确保分数在0-100范围
    score = max(0, min(100, score))

    # 趋势强度等级（v3 门槛保持，新增多周期+背离维度后总分空间更大）
    if score >= 80:
        level = "强势多头"
        level_color = "#0ECB81"
    elif score >= 62:
        level = "偏多"
        level_color = "#52B788"
    elif score >= 38:
        level = "中性震荡"
        level_color = "#FFD700"
    elif score >= 18:
        level = "偏空"
        level_color = "#FFA500"
    else:
        level = "强势空头"
        level_color = "#F6465D"

    return {
        "trend_strength_score": score,
        "trend_strength_level": level,
        "trend_strength_color": level_color,
        "signal_summary": " | ".join(signals) if signals else "无明显信号",
    }


def get_macd_analysis(df: pd.DataFrame) -> dict:
    """MACD分析辅助函数"""
    if df.empty or "MACD" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    macd = last["MACD"]
    signal = last["MACD_signal"]
    hist = last["MACD_hist"]

    macd_prev = prev["MACD"]
    signal_prev = prev["MACD_signal"]

    golden_cross = macd_prev <= signal_prev and macd > signal
    dead_cross = macd_prev >= signal_prev and macd < signal

    return {
        "macd": macd,
        "macd_signal": signal,
        "macd_hist": hist,
        "macd_bullish": golden_cross or (macd > signal and hist > 0),
        "macd_bearish": dead_cross or (macd < signal and hist < 0),
        "macd_golden_cross": golden_cross,
        "macd_dead_cross": dead_cross,
    }


# ─── CCI分析 ───────────────────────────────────────────────────────────────────

def get_cci_analysis(df: pd.DataFrame) -> dict:
    """
    CCI指标分析：超买超卖、背离
    """
    if df.empty or "CCI" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    cci = last["CCI"]
    cci_prev = prev["CCI"]

    # 超买超卖
    overbought = cci > 100
    oversold = cci < -100

    # 穿越信号
    cross_above_100 = cci_prev <= 100 and cci > 100
    cross_below_100 = cci_prev >= 100 and cci < 100
    cross_above_m100 = cci_prev <= -100 and cci > -100
    cross_below_m100 = cci_prev >= -100 and cci < -100

    if cross_above_100:
        signal = "突破+100，进入强势区"
    elif cross_below_100:
        signal = "跌破+100，强势结束"
    elif cross_above_m100:
        signal = "突破-100，弱势结束"
    elif cross_below_m100:
        signal = "跌破-100，进入弱势区"
    elif overbought:
        signal = "超买区(>100)"
    elif oversold:
        signal = "超卖区(<-100)"
    else:
        signal = "常态区(-100~+100)"

    return {
        "cci": cci,
        "cci_overbought": overbought,
        "cci_oversold": oversold,
        "cci_signal": signal,
    }


# ─── WR分析 ────────────────────────────────────────────────────────────────────

def get_wr_analysis(df: pd.DataFrame) -> dict:
    """
    威廉指标(WR)分析：超买超卖
    """
    if df.empty or "WR" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    wr = last["WR"]
    wr_prev = prev["WR"]

    # 超买超卖 (WR是反向指标，-20以上为超买，-80以下为超卖)
    overbought = wr > -20
    oversold = wr < -80

    # 穿越信号
    golden_cross = wr_prev <= -80 and wr > -80  # 上穿-80，买入信号
    dead_cross = wr_prev >= -20 and wr < -20  # 下穿-20，卖出信号

    if golden_cross:
        signal = "上穿-80，买入信号"
    elif dead_cross:
        signal = "下穿-20，卖出信号"
    elif overbought:
        signal = "超买区(>-20)"
    elif oversold:
        signal = "超卖区(<-80)"
    else:
        signal = "常态区"

    return {
        "wr": wr,
        "wr_overbought": overbought,
        "wr_oversold": oversold,
        "wr_golden_cross": golden_cross,
        "wr_dead_cross": dead_cross,
        "wr_signal": signal,
    }


# ─── DMI分析 ───────────────────────────────────────────────────────────────────

def get_dmi_analysis(df: pd.DataFrame) -> dict:
    """
    DMI趋向指标分析：趋势方向、强度
    """
    if df.empty or "ADX" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    di_plus = last["DI_plus"]
    di_minus = last["DI_minus"]
    adx = last["ADX"]

    di_plus_prev = prev["DI_plus"]
    di_minus_prev = prev["DI_minus"]

    # 趋势方向
    bullish = di_plus > di_minus
    bearish = di_minus > di_plus

    # 金叉死叉
    golden_cross = di_plus_prev <= di_minus_prev and di_plus > di_minus
    dead_cross = di_plus_prev >= di_minus_prev and di_plus < di_minus

    # 趋势强度 (ADX > 25 表示趋势明显)
    strong_trend = adx > 25
    weak_trend = adx < 20

    if golden_cross and strong_trend:
        signal = "DI+上穿DI-，ADX>25，强势多头"
    elif golden_cross:
        signal = "DI+上穿DI-，多头信号"
    elif dead_cross and strong_trend:
        signal = "DI-上穿DI+，ADX>25，强势空头"
    elif dead_cross:
        signal = "DI-上穿DI+，空头信号"
    elif strong_trend and bullish:
        signal = f"多头趋势明显 (ADX={adx:.1f})"
    elif strong_trend and bearish:
        signal = f"空头趋势明显 (ADX={adx:.1f})"
    elif weak_trend:
        signal = f"趋势较弱，震荡格局 (ADX={adx:.1f})"
    else:
        signal = f"趋势一般 (ADX={adx:.1f})"

    return {
        "di_plus": di_plus,
        "di_minus": di_minus,
        "adx": adx,
        "dmi_bullish": bullish,
        "dmi_bearish": bearish,
        "dmi_golden_cross": golden_cross,
        "dmi_dead_cross": dead_cross,
        "dmi_strong_trend": strong_trend,
        "dmi_signal": signal,
    }


# ─── 成交量分析 ────────────────────────────────────────────────────────────────

def get_volume_analysis(df: pd.DataFrame) -> dict:
    """
    成交量分析：放量、缩量、量价关系
    """
    if df.empty or "Volume" not in df.columns:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    vol = last["Volume"]
    vol_prev = prev["Volume"]
    close = last["Close"]
    open_price = last["Open"]

    # 成交量MA
    vol_ma5 = last.get("Vol_MA_5", vol)
    vol_ma20 = last.get("Vol_MA_20", vol)

    # 放量缩量
    vol_expanding = vol > vol_ma5 * 1.5  # 放量50%以上
    vol_contracting = vol < vol_ma5 * 0.7  # 缩量30%以上

    # 量价关系
    price_up = close > open_price
    price_down = close < open_price

    if vol_expanding and price_up:
        vol_signal = "放量上涨，多头确认"
        vol_score = 2
    elif vol_expanding and price_down:
        vol_signal = "放量下跌，空头确认"
        vol_score = -2
    elif vol_contracting and price_up:
        vol_signal = "缩量上涨，量价背离"
        vol_score = -1
    elif vol_contracting and price_down:
        vol_signal = "缩量下跌，抛压减轻"
        vol_score = 1
    else:
        vol_signal = "量价常态"
        vol_score = 0

    return {
        "volume": vol,
        "vol_ma5": vol_ma5,
        "vol_ma20": vol_ma20,
        "vol_expanding": vol_expanding,
        "vol_contracting": vol_contracting,
        "vol_signal": vol_signal,
        "vol_score": vol_score,
    }


# ─── 资金流向分析 ──────────────────────────────────────────────────────────────

def get_money_flow_analysis(df: pd.DataFrame) -> dict:
    """
    资金流向分析：基于OBV和MFI
    """
    if df.empty or "OBV" not in df.columns:
        return {}

    last = df.iloc[-1]

    obv = last["OBV"]
    obv_ema = last.get("OBV_EMA", obv)

    # OBV趋势
    obv_bullish = obv > obv_ema

    # 计算MFI (Money Flow Index)
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    money_flow = typical_price * df["Volume"]

    positive_flow = []
    negative_flow = []

    for i in range(1, min(15, len(df))):
        if typical_price.iloc[-i] > typical_price.iloc[-(i+1)]:
            positive_flow.append(money_flow.iloc[-i])
        elif typical_price.iloc[-i] < typical_price.iloc[-(i+1)]:
            negative_flow.append(money_flow.iloc[-i])

    positive_sum = sum(positive_flow) if positive_flow else 1
    negative_sum = sum(negative_flow) if negative_flow else 1

    mfi = 100 - (100 / (1 + positive_sum / negative_sum))

    # MFI信号
    mfi_overbought = mfi > 80
    mfi_oversold = mfi < 20

    if mfi_overbought:
        mfi_signal = "MFI超买(>80)，资金过热"
    elif mfi_oversold:
        mfi_signal = "MFI超卖(<20)，资金冰点"
    else:
        mfi_signal = f"MFI常态 ({mfi:.1f})"

    return {
        "obv": obv,
        "obv_bullish": obv_bullish,
        "mfi": mfi,
        "mfi_overbought": mfi_overbought,
        "mfi_oversold": mfi_oversold,
        "mfi_signal": mfi_signal,
    }


# ─── 多指标共振分析 ────────────────────────────────────────────────────────────

def get_indicator_resonance(df: pd.DataFrame) -> dict:
    """
    多指标共振分析：统计多个指标的多空信号
    """
    signals = {
        "buy": [],
        "sell": [],
        "neutral": []
    }

    # KDJ
    if "KDJ_K" in df.columns:
        last = df.iloc[-1]
        if last["KDJ_K"] > last["KDJ_D"]:
            signals["buy"].append("KDJ金叉")
        else:
            signals["sell"].append("KDJ死叉")

    # MACD
    if "MACD" in df.columns:
        last = df.iloc[-1]
        if last["MACD"] > last["MACD_signal"]:
            signals["buy"].append("MACD金叉")
        else:
            signals["sell"].append("MACD死叉")

    # RSI
    if "RSI" in df.columns:
        rsi = df["RSI"].iloc[-1]
        if rsi < 30:
            signals["buy"].append("RSI超卖")
        elif rsi > 70:
            signals["sell"].append("RSI超买")
        else:
            signals["neutral"].append("RSI常态")

    # CCI
    if "CCI" in df.columns:
        cci = df["CCI"].iloc[-1]
        if cci < -100:
            signals["buy"].append("CCI超卖")
        elif cci > 100:
            signals["sell"].append("CCI超买")

    # WR
    if "WR" in df.columns:
        wr = df["WR"].iloc[-1]
        if wr < -80:
            signals["buy"].append("WR超卖")
        elif wr > -20:
            signals["sell"].append("WR超买")

    # 计算共振强度
    buy_count = len(signals["buy"])
    sell_count = len(signals["sell"])

    if buy_count >= 3:
        resonance = "强烈买入共振"
        resonance_color = "#0ECB81"
    elif buy_count >= 2:
        resonance = "买入共振"
        resonance_color = "#52B788"
    elif sell_count >= 3:
        resonance = "强烈卖出共振"
        resonance_color = "#F6465D"
    elif sell_count >= 2:
        resonance = "卖出共振"
        resonance_color = "#FFA500"
    else:
        resonance = "无明显共振"
        resonance_color = "#FFD700"

    return {
        "resonance": resonance,
        "resonance_color": resonance_color,
        "buy_signals": signals["buy"],
        "sell_signals": signals["sell"],
        "neutral_signals": signals["neutral"],
        "buy_count": buy_count,
        "sell_count": sell_count,
    }


# ─── 小时级短线趋势分析（富途牛牛风格）────────────────────────────────────────

def get_short_term_trend(df: pd.DataFrame) -> dict:
    """
    小时级短线趋势分析 - 富途牛牛风格
    专注于短期交易信号，适合日内/短线交易
    """
    if df.empty or len(df) < 5:
        return {"error": "数据不足"}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last
    close = last["Close"]

    # 计算短期趋势指标
    signals = []
    score = 50  # 基准分

    # 1. 价格相对于短期均线位置
    if "EMA_8" in df.columns:
        ema8 = last["EMA_8"]
        ema8_prev = prev["EMA_8"]
        price_above_ema8 = close > ema8
        ema8_rising = ema8 > ema8_prev

        if price_above_ema8 and ema8_rising:
            signals.append("价格与EMA8同步向上")
            score += 15
        elif price_above_ema8:
            signals.append("价格在EMA8上方")
            score += 5
        elif ema8_rising:
            signals.append("EMA8走平/向上")
            score += 3
        else:
            signals.append("价格跌破EMA8")
            score -= 10

    # 2. 成交量分析（短期放量）
    if "Volume" in df.columns:
        vol = last["Volume"]
        vol_ma = df["Volume"].tail(5).mean()
        if vol > vol_ma * 1.5:
            signals.append("短期放量")
            score += 5
        elif vol < vol_ma * 0.5:
            signals.append("短期缩量")
            score -= 3

    # 3. 布林带窄幅突破（短线关键信号）
    if "BB_upper" in df.columns and "BB_lower" in df.columns:
        bb_upper = last["BB_upper"]
        bb_lower = last["BB_lower"]
        bb_width = (bb_upper - bb_lower) / last.get("BB_middle", close)

        # 计算布林带收窄程度
        bb_width_history = ((df["BB_upper"] - df["BB_lower"]) / df["Close"]).tail(20)
        bb_width_pct = (bb_width - bb_width_history.min()) / (bb_width_history.max() - bb_width_history.min() + 0.0001)

        if bb_width_pct < 0.1:  # 极度收窄
            signals.append("布林带极度收窄，准备突破")
            score += 10
        elif close > bb_upper:
            signals.append("突破布林上轨")
            score += 8
        elif close < bb_lower:
            signals.append("跌破布林下轨")
            score -= 8

    # 4. MACD柱状图变化（短线动能）
    if "MACD_hist" in df.columns:
        hist = last["MACD_hist"]
        hist_prev = prev["MACD_hist"]

        if hist > 0 and hist > hist_prev:
            signals.append("MACD红柱扩大，多头动能增强")
            score += 10
        elif hist > 0 and hist < hist_prev:
            signals.append("MACD红柱缩小，多头动能减弱")
            score -= 3
        elif hist < 0 and hist < hist_prev:
            signals.append("MACD绿柱扩大，空头动能增强")
            score -= 10
        elif hist < 0 and hist > hist_prev:
            signals.append("MACD绿柱缩小，空头动能减弱")
            score += 5

        # MACD金叉死叉（短线关键）
        if "MACD" in df.columns and "MACD_signal" in df.columns:
            macd = last["MACD"]
            signal_line = last["MACD_signal"]
            macd_prev = prev["MACD"]
            signal_prev = prev["MACD_signal"]

            if macd_prev <= signal_prev and macd > signal_line:
                signals.append("MACD金叉")
                score += 15
            elif macd_prev >= signal_prev and macd < signal_line:
                signals.append("MACD死叉")
                score -= 15

    # 5. KDJ短期信号
    if "KDJ_J" in df.columns:
        j = last["KDJ_J"]
        k = last["KDJ_K"]
        d = last["KDJ_D"]

        if j < 0:
            signals.append("KDJ J值<0，超卖")
            score += 10
        elif j > 100:
            signals.append("KDJ J值>100，超买")
            score -= 10

        if k > d:
            signals.append("KDJ金叉状态")
            score += 5
        else:
            signals.append("KDJ死叉状态")
            score -= 5

    # 6. 最近K线形态分析
    if len(df) >= 3:
        last3 = df.tail(3)
        highs = last3["High"].values
        lows = last3["Low"].values
        closes = last3["Close"].values
        opens = last3["Open"].values

        # 连续上涨/下跌
        if all(closes[i] > opens[i] for i in range(3)):
            signals.append("三连阳，强势")
            score += 8
        elif all(closes[i] < opens[i] for i in range(3)):
            signals.append("三连阴，弱势")
            score -= 8

        # 高低点变化
        if highs[-1] > highs[-2] > highs[-3]:
            signals.append("高点上移，趋势向上")
            score += 5
        elif lows[-1] < lows[-2] < lows[-3]:
            signals.append("低点下移，趋势向下")
            score -= 5

    # 7. ATR波动率分析
    if "ATR" in df.columns:
        atr = last["ATR"]
        atr_pct = atr / close

        if atr_pct > 0.03:  # 波动率大于3%
            signals.append(f"高波动率 ({atr_pct*100:.1f}%)，注意风险")
            score -= 3  # 高波动降低确定性
        elif atr_pct < 0.01:
            signals.append(f"低波动率 ({atr_pct*100:.1f}%)，可能即将突破")

    # 分数限制在0-100
    score = max(0, min(100, score))

    # 趋势判断
    if score >= 70:
        trend = "强势多头"
        trend_color = "#0ECB81"
        action = "积极做多"
    elif score >= 55:
        trend = "偏多"
        trend_color = "#52B788"
        action = "偏多操作"
    elif score >= 45:
        trend = "震荡"
        trend_color = "#FFD700"
        action = "观望"
    elif score >= 30:
        trend = "偏空"
        trend_color = "#FFA500"
        action = "偏空操作"
    else:
        trend = "强势空头"
        trend_color = "#F6465D"
        action = "积极做空/离场"

    # 计算短期支撑压力
    recent = df.tail(10)
    short_support = recent["Low"].min()
    short_resistance = recent["High"].max()

    return {
        "short_term_score": score,
        "short_term_trend": trend,
        "short_term_color": trend_color,
        "short_term_action": action,
        "short_term_signals": signals,
        "short_support": short_support,
        "short_resistance": short_resistance,
    }


# ─── 完整分析入口 ──────────────────────────────────────────────────────────────

def run_full_analysis(df: pd.DataFrame, info: dict, timeframe: str = "日线",
                      benchmark_df: pd.DataFrame = None,
                      with_northbound: bool = False) -> dict:
    """
    完整分析入口
    df: 已计算指标的 OHLCV DataFrame
    info: yfinance info dict
    timeframe: 时间框架（日线/周线/月线/小时线）
    benchmark_df: 基准指数（沪深 300 等）OHLCV，用于趋势分的"相对强度"维度
    with_northbound: 是否启用北向资金加成（每次会多 1 次 akshare 调用，~1-2s）
    """
    df = calc_emas(df)

    ema_info = get_ema_alignment(df)
    fib_info = get_fib_support_resistance(df)
    bb_info = get_bollinger_analysis(df)
    kdj_info = get_kdj_analysis(df)
    macd_info = get_macd_analysis(df)

    # 新增分析
    cci_info = get_cci_analysis(df)
    wr_info = get_wr_analysis(df)
    dmi_info = get_dmi_analysis(df)
    vol_info = get_volume_analysis(df)
    mf_info = get_money_flow_analysis(df)
    resonance_info = get_indicator_resonance(df)

    pe = info.get("trailingPE")
    valuation_info = get_valuation_position(pe)

    rsi_last = df["RSI"].iloc[-1] if "RSI" in df.columns else None
    advice_info = get_operation_advice(ema_info, fib_info, rsi_last)

    # 计算交易区间
    zones_info = calculate_trading_zones(df, ema_info, fib_info, bb_info)

    # 趋势强度评分（v2: 增加量能/动量/北向三个维度，权重重新分配）
    nb_score = None
    if with_northbound:
        try:
            ticker = (info or {}).get("symbol") or (info or {}).get("ticker") or ""
            if ticker:
                from .factor_screener import get_northbound_score
                code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
                nb_score = get_northbound_score(code)
        except Exception:
            nb_score = None

    strength_info = calculate_trend_strength(
        ema_info, macd_info, kdj_info, bb_info,
        df=df, benchmark_df=benchmark_df, northbound_score=nb_score,
    )

    result = {
        **ema_info,
        **fib_info,
        **valuation_info,
        **advice_info,
        **bb_info,
        **kdj_info,
        **macd_info,
        **zones_info,
        **strength_info,
        **cci_info,
        **wr_info,
        **dmi_info,
        **vol_info,
        **mf_info,
        **resonance_info,
        "rsi_last": rsi_last,
        "timeframe": timeframe,
    }

    # 如果是小时线，添加短线趋势分析
    if timeframe == "小时线":
        short_term_info = get_short_term_trend(df)
        result.update(short_term_info)

    # 多周期趋势共振（纳入评分体系）
    try:
        if timeframe == "日线" and df is not None and len(df) > 55:
            from .multi_timeframe import analyze_timeframe_trend, build_multi_timeframe_summary
            mtf_analyses = {"日线": analyze_timeframe_trend(df)}
            # 周线/月线通过resample获取（避免额外API调用）
            for tf_name, rule in [("周线", "W"), ("月线", "ME")]:
                try:
                    tf_df = df.resample(rule).agg({
                        "Open": "first", "High": "max", "Low": "min",
                        "Close": "last", "Volume": "sum"
                    }).dropna()
                    if len(tf_df) >= 20:
                        mtf_analyses[tf_name] = analyze_timeframe_trend(tf_df)
                    else:
                        mtf_analyses[tf_name] = {"trend": "数据不足", "strength": 0}
                except Exception:
                    mtf_analyses[tf_name] = {"trend": "获取失败", "strength": 0}
            result["multi_timeframe_summary"] = build_multi_timeframe_summary(mtf_analyses)
    except Exception:
        pass

    # ── 消息面情绪集成（在评分前获取，供 interpret_all_indicators 使用）──
    try:
        from .news_analysis import NewsAnalyzer
        _ticker = info.get("symbol", "") if info else ""
        news_analyzer = NewsAnalyzer(_ticker, info.get("shortName", "") if info else "")
        news_result = news_analyzer.get_comprehensive_news_analysis()
        result["news_sentiment"] = news_result
        # 资金流向单独存储供 interpret_fund_flow 使用
        fund_flow = news_result.get("资金流向", {})
        if fund_flow:
            result["fund_flow"] = fund_flow
    except Exception:
        result["news_sentiment"] = None

    # 指标详细解释与多因子概率评分 (v2)
    try:
        interp_result = interpret_all_indicators(result, df)
        result["indicator_interpretations"] = interp_result.get("indicators", [])
        result["comprehensive_probability"] = interp_result.get("comprehensive", {})
    except Exception:
        result["indicator_interpretations"] = []
        result["comprehensive_probability"] = {}

    # 分周期支撑压力位（日线级别）
    try:
        if timeframe in ("日线", "周线"):
            sr_levels = calc_daily_levels(df)
            result["multi_tf_sr"] = {"daily": sr_levels}
    except Exception:
        pass

    # 复合买卖信号
    try:
        from .signal_generator import generate_composite_signals
        sr_data = result.get("multi_tf_sr", {}).get("daily", {})
        signals = generate_composite_signals(df, result, sr_data)
        result["composite_signals"] = signals
    except Exception:
        result["composite_signals"] = {}

    # ── 买入操作计划 ──────────────────────────────────────────────────────
    try:
        from .buy_action_plan import generate_buy_action_plan
        comp_sig = result.get("composite_signals", {})
        trade_sug = comp_sig.get("trade_suggestion", {})
        sr_for_plan = result.get("multi_tf_sr", {}).get("daily", {})
        _board = "其他"
        _mcap = None
        if info:
            _mcap = info.get("marketCap")
            if _mcap:
                _mcap = _mcap / 1e8  # 转为亿
        action_plan = generate_buy_action_plan(
            df, result, sr_for_plan, comp_sig, trade_sug,
            board=_board, market_cap_yi=_mcap,
        )
        result["buy_action_plan"] = action_plan
    except Exception:
        result["buy_action_plan"] = None

    # ── 新增模块集成 ──────────────────────────────────────────────────────

    # K线形态识别
    try:
        from .candlestick_patterns import detect_patterns
        candle_info = detect_patterns(df)
        result["candlestick_patterns"] = candle_info.get("patterns", [])
        result["candlestick_summary"] = candle_info
    except Exception:
        result["candlestick_patterns"] = []
        result["candlestick_summary"] = {}

    # 背离检测
    try:
        from .divergence_detector import detect_divergences
        div_info = detect_divergences(df)
        result["divergences"] = div_info.get("divergences", [])
        result["divergence_summary"] = div_info
    except Exception:
        result["divergences"] = []
        result["divergence_summary"] = {}

    # 量价深度分析
    try:
        from .volume_price_analysis import run_volume_price_analysis
        shares = info.get("sharesOutstanding") if info else None
        vp_info = run_volume_price_analysis(df, shares_outstanding=shares)
        result["volume_price_analysis"] = vp_info
    except Exception:
        result["volume_price_analysis"] = {}

    # 图表形态识别
    try:
        from .chart_patterns import detect_chart_patterns
        chart_info = detect_chart_patterns(df)
        result["chart_patterns"] = chart_info.get("patterns", [])
        result["chart_pattern_summary"] = chart_info
    except Exception:
        result["chart_patterns"] = []
        result["chart_pattern_summary"] = {}

    # Elliott波浪检测（多周期）
    try:
        from .elliott_wave import detect_elliott_waves, detect_multi_timeframe_waves
        ew_info = detect_elliott_waves(df)
        result["elliott_wave_summary"] = ew_info

        # 多周期综合（仅日线时进行）
        if timeframe == "日线" and len(df) > 55:
            df_weekly = df.resample("W").agg({
                "Open": "first", "High": "max", "Low": "min",
                "Close": "last", "Volume": "sum",
            }).dropna()
            df_60m = None
            try:
                from .intraday_analysis import fetch_intraday_data
                _ticker = info.get("symbol", "") if info else ""
                if _ticker:
                    df_60m = fetch_intraday_data(_ticker, period="1mo", interval="60m")
            except Exception:
                pass
            mtf_ew = detect_multi_timeframe_waves(df, df_weekly, df_60m)
            result["elliott_wave_multi_tf"] = mtf_ew
    except Exception:
        result["elliott_wave_summary"] = {}
        result["elliott_wave_multi_tf"] = {}

    # 风险量化（按市场自动选无风险利率：A 股 2.2% / 美股 4.3% / 港股 4.0%）
    try:
        from .risk_metrics import calc_risk_metrics
        market = (info or {}).get("market") or (info or {}).get("asset_type") or "A股"
        risk_info = calc_risk_metrics(df, market=market)
        result["risk_metrics"] = risk_info
    except Exception:
        result["risk_metrics"] = {}

    # 一目均衡表信号
    try:
        ichimoku = {}
        if "Ichimoku_Tenkan" in df.columns and "Ichimoku_Kijun" in df.columns:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            tenkan = last.get("Ichimoku_Tenkan", np.nan)
            kijun = last.get("Ichimoku_Kijun", np.nan)
            span_a = last.get("Ichimoku_SpanA", np.nan)
            span_b = last.get("Ichimoku_SpanB", np.nan)
            close = last["Close"]

            # TK交叉
            prev_tenkan = prev.get("Ichimoku_Tenkan", np.nan)
            prev_kijun = prev.get("Ichimoku_Kijun", np.nan)
            if not np.isnan(tenkan) and not np.isnan(kijun):
                if tenkan > kijun and prev_tenkan <= prev_kijun:
                    ichimoku["tk_cross"] = "金叉"
                elif tenkan < kijun and prev_tenkan >= prev_kijun:
                    ichimoku["tk_cross"] = "死叉"
                else:
                    ichimoku["tk_cross"] = "多头排列" if tenkan > kijun else "空头排列"

            # 价格 vs 云带
            cloud_top = max(span_a, span_b) if not (np.isnan(span_a) or np.isnan(span_b)) else np.nan
            cloud_bottom = min(span_a, span_b) if not (np.isnan(span_a) or np.isnan(span_b)) else np.nan
            if not np.isnan(cloud_top):
                if close > cloud_top:
                    ichimoku["cloud_position"] = "云上（多头）"
                    ichimoku["cloud_bias"] = "利多"
                elif close < cloud_bottom:
                    ichimoku["cloud_position"] = "云下（空头）"
                    ichimoku["cloud_bias"] = "利空"
                else:
                    ichimoku["cloud_position"] = "云中（震荡）"
                    ichimoku["cloud_bias"] = "中性"

            # 云带颜色
            if not (np.isnan(span_a) or np.isnan(span_b)):
                ichimoku["cloud_color"] = "绿云（多头）" if span_a > span_b else "红云（空头）"

            ichimoku["tenkan"] = round(tenkan, 2) if not np.isnan(tenkan) else None
            ichimoku["kijun"] = round(kijun, 2) if not np.isnan(kijun) else None

            # 云层厚度（波动性补充指标）
            cloud_thick = last.get("Ichimoku_CloudThickness", np.nan)
            cloud_pct = last.get("Ichimoku_CloudPct", np.nan)
            if not np.isnan(cloud_thick):
                ichimoku["cloud_thickness"] = round(float(cloud_thick), 2)
                ichimoku["cloud_thickness_pct"] = round(float(cloud_pct), 2)
                # 云层厚度解读
                abs_pct = abs(float(cloud_pct))
                if abs_pct > 3.0:
                    ichimoku["cloud_vol_signal"] = "厚云（高波动/强趋势）"
                elif abs_pct > 1.0:
                    ichimoku["cloud_vol_signal"] = "正常云层"
                else:
                    ichimoku["cloud_vol_signal"] = "薄云（低波动/趋势减弱）"
        result["ichimoku"] = ichimoku
    except Exception:
        result["ichimoku"] = {}

    # TD Sequential 信号
    try:
        td_info = {}
        if "TD_Setup" in df.columns and "TD_Signal" in df.columns:
            last_setup = int(df["TD_Setup"].iloc[-1])
            last_signal = int(df["TD_Signal"].iloc[-1])
            td_info["current_setup"] = last_setup
            td_info["current_signal"] = last_signal

            # Countdown 进度
            if "TD_Countdown" in df.columns:
                last_cd = int(df["TD_Countdown"].iloc[-1])
                td_info["current_countdown"] = last_cd
            else:
                last_cd = 0
                td_info["current_countdown"] = 0

            # 查找最近的TD信号（Setup 9 = ±1, Countdown 13 = ±2）
            signals = df[df["TD_Signal"] != 0]
            if len(signals) > 0:
                last_sig_row = signals.iloc[-1]
                sig_val = int(last_sig_row["TD_Signal"])
                sig_map = {
                    1: "买入Setup(卖方衰竭9)",
                    -1: "卖出Setup(买方衰竭9)",
                    2: "买入Countdown确认(13)",
                    -2: "卖出Countdown确认(13)",
                }
                td_info["last_signal_type"] = sig_map.get(sig_val, f"信号{sig_val}")
                td_info["last_signal_date"] = str(last_sig_row.name)
                td_info["bars_since_signal"] = len(df) - df.index.get_loc(last_sig_row.name) - 1

            # 当前进度描述
            if last_setup > 0:
                status = f"买Setup {last_setup}/9"
            elif last_setup < 0:
                status = f"卖Setup {abs(last_setup)}/9"
            else:
                status = "无活跃Setup"

            # 附加 Countdown 进度
            if last_cd > 0:
                status += f" | 买Countdown {last_cd}/13"
            elif last_cd < 0:
                status += f" | 卖Countdown {abs(last_cd)}/13"
            td_info["status"] = status
        result["td_sequential"] = td_info
    except Exception:
        result["td_sequential"] = {}

    return result
