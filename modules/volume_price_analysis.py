"""
volume_price_analysis.py — 量价深度分析引擎
量价背离、换手率、量比、吸筹/派发阶段、Wyckoff分析
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional


# ─── 量价背离检测 ─────────────────────────────────────────────────────────────

def _detect_volume_price_divergence(df: pd.DataFrame, window: int = 5) -> Dict:
    """
    检测量价背离：价格方向与量能方向不一致
    连续N日确认
    """
    if len(df) < window + 1:
        return {"detected": False, "type": "数据不足", "description": "", "bias_score": 0}

    recent = df.tail(window)
    price_change = recent["Close"].iloc[-1] - recent["Close"].iloc[0]
    price_up = price_change > 0

    vol_ma5 = df["Volume"].rolling(5, min_periods=1).mean()
    vol_ma20 = df["Volume"].rolling(20, min_periods=1).mean()
    current_vol = df["Volume"].iloc[-1]
    avg_vol = vol_ma20.iloc[-1] if not pd.isna(vol_ma20.iloc[-1]) else vol_ma5.iloc[-1]

    # 量能趋势（最近5日成交量 vs 前5日）
    if len(df) >= 10:
        recent_vol_avg = df["Volume"].tail(5).mean()
        prev_vol_avg = df["Volume"].iloc[-10:-5].mean()
        vol_increasing = recent_vol_avg > prev_vol_avg * 1.1
        vol_decreasing = recent_vol_avg < prev_vol_avg * 0.8
    else:
        vol_increasing = current_vol > avg_vol * 1.2
        vol_decreasing = current_vol < avg_vol * 0.7

    vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

    if price_up and vol_decreasing:
        return {
            "detected": True,
            "type": "缩量上涨",
            "description": f"价格上涨但成交量萎缩（量比{vol_ratio:.2f}），上涨动能不足，持续性存疑",
            "bias_score": -25,
            "warning": "上涨缺乏量能支撑",
        }
    elif not price_up and vol_increasing:
        return {
            "detected": True,
            "type": "放量下跌",
            "description": f"价格下跌且成交量放大（量比{vol_ratio:.2f}），卖压沉重，可能加速下行",
            "bias_score": -40,
            "warning": "放量下跌是危险信号",
        }
    elif price_up and vol_increasing:
        return {
            "detected": False,
            "type": "放量上涨",
            "description": f"价格上涨且量能配合放大（量比{vol_ratio:.2f}），量价齐升属健康上涨",
            "bias_score": 30,
            "warning": None,
        }
    elif not price_up and vol_decreasing:
        return {
            "detected": False,
            "type": "缩量下跌",
            "description": f"价格下跌但量能萎缩（量比{vol_ratio:.2f}），卖压减弱，可能即将企稳",
            "bias_score": 10,
            "warning": None,
        }
    else:
        return {
            "detected": False,
            "type": "量价正常",
            "description": "量价关系正常，无明显背离",
            "bias_score": 0,
            "warning": None,
        }


# ─── 换手率分析 ───────────────────────────────────────────────────────────────

def _calc_turnover_rate(df: pd.DataFrame, shares_outstanding: float) -> Dict:
    """
    换手率 = 日成交量 / 流通股本 × 100
    """
    if shares_outstanding is None or shares_outstanding <= 0:
        return {"available": False, "description": "缺少流通股本数据，无法计算换手率"}

    current_vol = df["Volume"].iloc[-1]
    turnover = current_vol / shares_outstanding * 100

    avg_5d = df["Volume"].tail(5).mean() / shares_outstanding * 100
    avg_20d = df["Volume"].tail(20).mean() / shares_outstanding * 100 if len(df) >= 20 else avg_5d

    # 级别判断
    if turnover > 10:
        level = "极高"
        desc = "换手率极高，市场交投极度活跃，短线博弈激烈，注意风险"
        score = -10  # 极高换手率往往是顶部特征
    elif turnover > 7:
        level = "高"
        desc = "换手率偏高，市场活跃度强，可能有重大消息或资金异动"
        score = -5
    elif turnover > 3:
        level = "活跃"
        desc = "换手率适中偏高，交投活跃，有利于价格发现"
        score = 5
    elif turnover > 1:
        level = "正常"
        desc = "换手率正常，市场交投平稳"
        score = 0
    else:
        level = "极低"
        desc = "换手率极低，市场关注度低或流动性不足，注意流动性风险"
        score = -5

    # 换手率趋势
    trend = ""
    if avg_5d > avg_20d * 1.5:
        trend = "近期换手率大幅攀升"
    elif avg_5d > avg_20d * 1.2:
        trend = "近期换手率温和上升"
    elif avg_5d < avg_20d * 0.6:
        trend = "近期换手率大幅萎缩"
    elif avg_5d < avg_20d * 0.8:
        trend = "近期换手率有所萎缩"
    else:
        trend = "近期换手率平稳"

    return {
        "available": True,
        "current": round(turnover, 2),
        "avg_5d": round(avg_5d, 2),
        "avg_20d": round(avg_20d, 2),
        "level": level,
        "trend": trend,
        "description": desc,
        "bias_score": score,
    }


# ─── 量比分析 ────────────────────────────────────────────────────────────────

def _calc_volume_ratio(df: pd.DataFrame) -> Dict:
    """
    量比 = 当前成交量 / 过去5日平均成交量
    """
    if len(df) < 5:
        return {"value": 1.0, "level": "—", "interpretation": "数据不足"}

    current_vol = df["Volume"].iloc[-1]
    avg_vol_5d = df["Volume"].tail(6).iloc[:-1].mean()  # 不含当天

    if avg_vol_5d == 0:
        return {"value": 0, "level": "—", "interpretation": "历史成交量为零"}

    vr = current_vol / avg_vol_5d

    if vr > 5:
        level = "巨量"
        interp = "成交量是近5日均量的5倍以上，可能有重大事件驱动，需极度警惕"
        score = -15
    elif vr > 2.5:
        level = "明显放量"
        interp = "成交量明显放大，关注价格方向确认趋势强度"
        score = 5 if df["Close"].iloc[-1] > df["Close"].iloc[-2] else -10
    elif vr > 1.5:
        level = "温和放量"
        interp = "成交量温和放大，市场活跃度有所提升"
        score = 5
    elif vr > 0.8:
        level = "正常"
        interp = "成交量处于正常水平"
        score = 0
    elif vr > 0.5:
        level = "缩量"
        interp = "成交量萎缩，市场观望情绪浓"
        score = -5
    else:
        level = "极度缩量"
        interp = "成交量极度萎缩，市场冷清，可能即将变盘"
        score = 0

    return {
        "value": round(vr, 2),
        "level": level,
        "interpretation": interp,
        "bias_score": score,
    }


# ─── 吸筹/派发 (Accumulation/Distribution) ──────────────────────────────────

def _calc_ad_phase(df: pd.DataFrame) -> Dict:
    """
    基于 A/D Line (Accumulation/Distribution Line) 判断阶段
    CLV = ((Close - Low) - (High - Close)) / (High - Low)
    AD = cumsum(CLV × Volume)
    """
    if len(df) < 20:
        return {"phase": "数据不足", "ad_trend": "—", "description": "", "bias_score": 0}

    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    volume = df["Volume"]

    hl_range = high - low
    hl_range = hl_range.replace(0, 0.0001)

    clv = ((close - low) - (high - close)) / hl_range
    ad_line = (clv * volume).cumsum()

    # AD线趋势（20日EMA方向）
    ad_ema20 = ad_line.ewm(span=20, adjust=False).mean()
    ad_current = ad_line.iloc[-1]
    ad_ema_val = ad_ema20.iloc[-1]

    ad_slope = ad_ema20.iloc[-1] - ad_ema20.iloc[-5] if len(ad_ema20) >= 5 else 0

    # 价格趋势
    price_slope = close.iloc[-1] - close.iloc[-20] if len(close) >= 20 else close.iloc[-1] - close.iloc[0]

    if ad_slope > 0:
        ad_trend = "上升"
    elif ad_slope < 0:
        ad_trend = "下降"
    else:
        ad_trend = "持平"

    # 阶段判断
    if ad_slope > 0 and price_slope <= 0:
        phase = "吸筹"
        desc = "资金持续流入但价格未上涨，可能处于主力吸筹阶段，关注放量突破信号"
        score = 35
    elif ad_slope > 0 and price_slope > 0:
        phase = "拉升"
        desc = "资金流入且价格上涨，处于拉升阶段，量价配合良好"
        score = 25
    elif ad_slope < 0 and price_slope >= 0:
        phase = "派发"
        desc = "资金持续流出但价格仍在高位，可能处于主力派发阶段，需警惕回调"
        score = -35
    elif ad_slope < 0 and price_slope < 0:
        phase = "下跌"
        desc = "资金流出且价格下跌，处于下跌阶段，建议观望"
        score = -25
    else:
        phase = "盘整"
        desc = "资金流向和价格均无明显趋势，处于盘整阶段"
        score = 0

    return {
        "phase": phase,
        "ad_line": round(ad_current, 2),
        "ad_trend": ad_trend,
        "description": desc,
        "bias_score": score,
    }


# ─── Wyckoff 阶段判断 ────────────────────────────────────────────────────────

def _detect_wyckoff_phase(df: pd.DataFrame) -> Dict:
    """
    简化的 Wyckoff 四阶段判断
    基于价格波动率 + 成交量模式
    """
    if len(df) < 30:
        return {"phase": "数据不足", "phase_en": "", "confidence": 0, "description": "", "bias_score": 0}

    close = df["Close"]
    volume = df["Volume"]

    # 近期价格波动率（20日标准差/均价）
    price_std = close.tail(20).std()
    price_mean = close.tail(20).mean()
    volatility = price_std / price_mean if price_mean > 0 else 0

    # 成交量趋势
    vol_recent = volume.tail(10).mean()
    vol_prev = volume.tail(30).head(20).mean()
    vol_change = vol_recent / vol_prev if vol_prev > 0 else 1

    # 价格趋势
    price_change_20d = (close.iloc[-1] - close.iloc[-20]) / close.iloc[-20] if len(close) >= 20 else 0
    price_change_5d = (close.iloc[-1] - close.iloc[-5]) / close.iloc[-5] if len(close) >= 5 else 0

    # 价格区间（是否在窄幅整理）
    high_20 = df["High"].tail(20).max()
    low_20 = df["Low"].tail(20).min()
    range_pct = (high_20 - low_20) / low_20 if low_20 > 0 else 0

    # Wyckoff阶段判断逻辑
    if range_pct < 0.08 and volatility < 0.02 and vol_change < 0.9:
        phase = "吸筹阶段"
        phase_en = "Accumulation"
        desc = "价格窄幅震荡+缩量，可能处于Wyckoff吸筹阶段，关注向上突破"
        confidence = 65
        score = 25
    elif price_change_20d > 0.05 and vol_change > 1.2:
        phase = "拉升阶段"
        phase_en = "Markup"
        desc = "价格上涨+放量，处于Wyckoff拉升阶段，趋势偏多"
        confidence = 70
        score = 30
    elif range_pct < 0.10 and volatility < 0.025 and vol_change > 1.1 and price_change_20d > 0:
        phase = "派发阶段"
        phase_en = "Distribution"
        desc = "高位窄幅震荡+放量，可能处于Wyckoff派发阶段，需警惕"
        confidence = 60
        score = -25
    elif price_change_20d < -0.05 and vol_change > 1.0:
        phase = "下跌阶段"
        phase_en = "Markdown"
        desc = "价格下跌+量能不减，处于Wyckoff下跌阶段，趋势偏空"
        confidence = 70
        score = -30
    else:
        phase = "过渡阶段"
        phase_en = "Transition"
        desc = "无法明确判断Wyckoff阶段，市场处于过渡期"
        confidence = 30
        score = 0

    return {
        "phase": phase,
        "phase_en": phase_en,
        "confidence": confidence,
        "description": desc,
        "bias_score": score,
    }


# ─── 主函数 ──────────────────────────────────────────────────────────────────

def run_volume_price_analysis(df: pd.DataFrame, shares_outstanding: float = None) -> Dict:
    """
    量价深度分析主函数

    参数:
        df: OHLCV DataFrame
        shares_outstanding: 流通股本（可选，用于换手率计算）

    返回:
        包含量价背离、换手率、量比、AD阶段、Wyckoff阶段的综合分析
    """
    if df is None or df.empty or len(df) < 5:
        return {
            "volume_price_divergence": {"detected": False, "type": "数据不足"},
            "turnover_rate": {"available": False},
            "volume_ratio": {"value": 0, "level": "—"},
            "accumulation_distribution": {"phase": "数据不足"},
            "wyckoff_phase": {"phase": "数据不足"},
            "combined_bias_score": 0,
        }

    vp_div = _detect_volume_price_divergence(df)
    turnover = _calc_turnover_rate(df, shares_outstanding) if shares_outstanding else {"available": False, "description": "未提供流通股本"}
    vol_ratio = _calc_volume_ratio(df)
    ad_phase = _calc_ad_phase(df)
    wyckoff = _detect_wyckoff_phase(df)

    # 综合评分
    scores = [vp_div.get("bias_score", 0), vol_ratio.get("bias_score", 0),
              ad_phase.get("bias_score", 0), wyckoff.get("bias_score", 0)]
    if turnover.get("available"):
        scores.append(turnover.get("bias_score", 0))

    combined = sum(scores) / len(scores) if scores else 0

    return {
        "volume_price_divergence": vp_div,
        "turnover_rate": turnover,
        "volume_ratio": vol_ratio,
        "accumulation_distribution": ad_phase,
        "wyckoff_phase": wyckoff,
        "combined_bias_score": round(combined, 1),
    }
