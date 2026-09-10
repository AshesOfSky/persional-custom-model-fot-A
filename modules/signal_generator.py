"""
signal_generator.py — 复合买卖信号生成器
结合多指标确认的买卖点判定 + 信号强度评级 + 精确交易建议
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from .technical import adaptive_rsi_thresholds
from .signal_confidence import (
    online_trend_strength,
    score_signal_evidence,
    signal_evidence_level,
)


# ── 前高前低检测 ──────────────────────────────────────────────────────

def detect_swing_highs_lows(df: pd.DataFrame, window: int = 10) -> Dict:
    """
    检测前高前低（摆动高低点）
    window: 左右各看window根K线确认极值
    """
    if df is None or df.empty or len(df) < window * 2 + 1:
        return {"swing_highs": [], "swing_lows": []}

    highs = []
    lows = []
    high_arr = df["High"].values
    low_arr = df["Low"].values

    for i in range(window, len(df) - window):
        confirmed_index = i + window
        pivot_date = str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i])
        confirmed_date = (
            str(df.index[confirmed_index].date())
            if hasattr(df.index[confirmed_index], "date")
            else str(df.index[confirmed_index])
        )
        # Swing high: 当前High是左右window内最高
        if high_arr[i] == max(high_arr[i - window:i + window + 1]):
            highs.append({
                "index": i,
                "date": pivot_date,
                "price": float(high_arr[i]),
                # 枢轴要到右侧 window 根K线走完后才可确认。保留 pivot date
                # 兼容既有展示，同时显式给出可用于回测/as-of 的确认时点。
                "confirmed_index": confirmed_index,
                "confirmed_date": confirmed_date,
            })
        # Swing low: 当前Low是左右window内最低
        if low_arr[i] == min(low_arr[i - window:i + window + 1]):
            lows.append({
                "index": i,
                "date": pivot_date,
                "price": float(low_arr[i]),
                "confirmed_index": confirmed_index,
                "confirmed_date": confirmed_date,
            })

    return {"swing_highs": highs, "swing_lows": lows}


# ── 单根K线条件检测 ──────────────────────────────────────────────────

# ── 信号桶：把高度相关的信号合并到同一维度，避免一个事实被计三票 ────────────
# 每个桶最多算 1 票，置信度公式按"独立桶数"打分而非原始信号条数。

BUY_SIGNAL_BUCKETS = {
    "trend":          ["EMA金叉(8×21)"],
    "momentum":       ["MACD金叉"],
    "oscillator":     ["RSI超卖反弹", "KDJ低位金叉"],
    "mean_revert":    ["布林下轨反弹", "接近支撑"],
    "volume":         ["放量(>1.5xMA5)"],
    "pattern":        ["TD买入信号"],
    "divergence":     ["MACD底背离"],        # v3: 背离作为独立确认桶
    "trend_strength": ["ADX趋势强+多周期看多"],  # v4: 趋势强度确认桶
    "smart_money":    ["OBV底背离累积"],         # v4: 智能资金流桶
}

SELL_SIGNAL_BUCKETS = {
    "trend":          ["EMA死叉(8×21)"],
    "momentum":       ["MACD死叉"],
    "oscillator":     ["RSI超买回落", "KDJ高位死叉"],
    "mean_revert":    ["布林上轨回落", "接近压力"],
    "volume":         ["缩量上涨(量价背离)"],
    "pattern":        ["TD卖出信号"],
    "divergence":     ["MACD顶背离"],        # v3: 背离作为独立确认桶
    "trend_strength": ["ADX趋势弱+多周期看空"],  # v4: 趋势强度确认桶
    "smart_money":    ["OBV顶背离派发"],         # v4: 智能资金流桶
}


def aggregate_buckets(confirmations: List[str], buckets: Dict[str, List[str]]) -> tuple:
    """返回 (独立桶数, 命中的桶名列表)"""
    hit = []
    for bucket_name, signals in buckets.items():
        if any(any(sig in conf for sig in signals) for conf in confirmations):
            hit.append(bucket_name)
    return len(hit), hit


def _volume_ma5_column(df: pd.DataFrame) -> str | None:
    if "Vol_MA_5" in df.columns:
        return "Vol_MA_5"
    if "Vol_MA5" in df.columns:
        return "Vol_MA5"
    return None


def _as_of_multi_timeframe_bias(df: pd.DataFrame, i: int) -> str | None:
    """Approximate weekly/monthly alignment using only data known at bar ``i``."""

    required = ("EMA_21", "EMA_55", "EMA_144")
    if i < 144 or any(column not in df.columns for column in required):
        return None
    ema21 = df["EMA_21"].iloc[i]
    ema55 = df["EMA_55"].iloc[i]
    ema144 = df["EMA_144"].iloc[i]
    if any(pd.isna(value) for value in (ema21, ema55, ema144)):
        return None
    if ema21 > ema55 > ema144:
        return "bullish"
    if ema21 < ema55 < ema144:
        return "bearish"
    return "mixed"


def _check_buy_conditions(df: pd.DataFrame, i: int, sr_levels: Dict,
                          rsi_thresholds: Dict = None) -> List[str]:
    """检测第i根K线的买入确认条件（rsi_thresholds: 自适应RSI超买超卖阈值）"""
    confirmations = []
    if i < 1 or i >= len(df):
        return confirmations

    c = df.iloc[i]
    p = df.iloc[i - 1]

    # 1. EMA内隧道金叉 (EMA8上穿EMA21)
    if "EMA_8" in df.columns and "EMA_21" in df.columns:
        if df["EMA_8"].iloc[i] > df["EMA_21"].iloc[i] and df["EMA_8"].iloc[i - 1] <= df["EMA_21"].iloc[i - 1]:
            confirmations.append("EMA金叉(8×21)")

    # 2. MACD（金叉与柱转正合并为一个事实，金叉为主、柱转正为补）
    macd_bullish = False
    if "MACD" in df.columns and "MACD_signal" in df.columns:
        if df["MACD"].iloc[i] > df["MACD_signal"].iloc[i] and df["MACD"].iloc[i - 1] <= df["MACD_signal"].iloc[i - 1]:
            macd_bullish = True
    if not macd_bullish and "MACD_hist" in df.columns:
        if df["MACD_hist"].iloc[i] > 0 and df["MACD_hist"].iloc[i - 1] <= 0:
            macd_bullish = True
    if macd_bullish:
        confirmations.append("MACD金叉")

    # 3. RSI从超卖反弹（使用自适应阈值）
    if "RSI" in df.columns:
        os_line = (rsi_thresholds or {}).get("oversold", 30.0)
        if df["RSI"].iloc[i] > os_line and df["RSI"].iloc[i - 1] <= os_line:
            confirmations.append("RSI超卖反弹")

    # 4. KDJ金叉（K上穿D且低位）
    if "KDJ_K" in df.columns and "KDJ_D" in df.columns:
        if (df["KDJ_K"].iloc[i] > df["KDJ_D"].iloc[i] and
                df["KDJ_K"].iloc[i - 1] <= df["KDJ_D"].iloc[i - 1] and
                df["KDJ_K"].iloc[i] < 50):
            confirmations.append("KDJ低位金叉")

    # 5. 价格触及布林下轨反弹
    if "BB_lower" in df.columns:
        if p["Low"] <= df["BB_lower"].iloc[i - 1] and c["Close"] > df["BB_lower"].iloc[i]:
            confirmations.append("布林下轨反弹")

    # 6. 价格接近支撑位
    if sr_levels:
        supports = sr_levels.get("all_supports", [])
        for s in supports[:3]:
            sp = s.get("price", 0)
            if sp > 0 and abs(c["Close"] - sp) / sp < 0.01:
                confirmations.append(f"接近支撑{s.get('method','')}")
                break

    # 7. 成交量放大
    volume_ma5 = _volume_ma5_column(df)
    if "Volume" in df.columns and volume_ma5 is not None:
        if df[volume_ma5].iloc[i] > 0 and c["Volume"] > 1.5 * df[volume_ma5].iloc[i]:
            confirmations.append("放量(>1.5xMA5)")

    # 8. TD序列买入信号
    if "TD_Signal" in df.columns and df["TD_Signal"].iloc[i] in (1, 2):
        confirmations.append("TD买入信号")
    elif "TD_Setup" in df.columns and df["TD_Setup"].iloc[i] == 9:
        confirmations.append("TD买入信号")

    # 9. MACD底背离（价格创新低但MACD柱未创新低）
    if "MACD_hist" in df.columns and i >= 20:
        price_low_20 = df["Low"].iloc[i-20:i].min()
        hist_low_20 = df["MACD_hist"].iloc[i-20:i].min()
        if (c["Low"] <= price_low_20 * 1.005 and
            df["MACD_hist"].iloc[i] > hist_low_20 * 0.7 and
            df["MACD_hist"].iloc[i] < 0):
            confirmations.append("MACD底背离")

    # 10. v4 趋势强度确认桶：ADX强趋势 + 多周期看多
    _trend_str_buy = False
    if "ADX" in df.columns:
        adx_val = float(df["ADX"].iloc[i])
        if adx_val > 25:  # ADX > 25 表示趋势成立
            mtf_bull = _as_of_multi_timeframe_bias(df, i) == "bullish"
            # SAR 看多（价格在 SAR 之上）
            sar_bull = True
            if "SAR" in df.columns and pd.notna(df["SAR"].iloc[i]):
                sar_bull = float(c["Close"]) > float(df["SAR"].iloc[i])
            if mtf_bull and sar_bull:
                _trend_str_buy = True
    if _trend_str_buy:
        confirmations.append("ADX趋势强+多周期看多")

    # 11. v4 OBV smart money: bullish divergence + accumulation regime
    if "OBV_Divergence" in df.columns and "OBV_Regime" in df.columns:
        obv_div = int(df["OBV_Divergence"].iloc[i]) if pd.notna(df["OBV_Divergence"].iloc[i]) else 0
        obv_regime = str(df["OBV_Regime"].iloc[i])
        if obv_div == 1 or obv_regime == "accumulation":
            confirmations.append("OBV底背离累积")

    return confirmations


def _check_sell_conditions(df: pd.DataFrame, i: int, sr_levels: Dict,
                           rsi_thresholds: Dict = None) -> List[str]:
    """检测第i根K线的卖出确认条件（rsi_thresholds: 自适应RSI超买超卖阈值）"""
    confirmations = []
    if i < 1 or i >= len(df):
        return confirmations

    c = df.iloc[i]
    p = df.iloc[i - 1]

    # 1. EMA死叉
    if "EMA_8" in df.columns and "EMA_21" in df.columns:
        if df["EMA_8"].iloc[i] < df["EMA_21"].iloc[i] and df["EMA_8"].iloc[i - 1] >= df["EMA_21"].iloc[i - 1]:
            confirmations.append("EMA死叉(8×21)")

    # 2. MACD（死叉与柱转负合并为一个事实，死叉为主、柱转负为补）
    macd_bearish = False
    if "MACD" in df.columns and "MACD_signal" in df.columns:
        if df["MACD"].iloc[i] < df["MACD_signal"].iloc[i] and df["MACD"].iloc[i - 1] >= df["MACD_signal"].iloc[i - 1]:
            macd_bearish = True
    if not macd_bearish and "MACD_hist" in df.columns:
        if df["MACD_hist"].iloc[i] < 0 and df["MACD_hist"].iloc[i - 1] >= 0:
            macd_bearish = True
    if macd_bearish:
        confirmations.append("MACD死叉")

    # 3. RSI超买回落（使用自适应阈值）
    if "RSI" in df.columns:
        ob_line = (rsi_thresholds or {}).get("overbought", 70.0)
        if df["RSI"].iloc[i] < ob_line and df["RSI"].iloc[i - 1] >= ob_line:
            confirmations.append("RSI超买回落")

    # 4. KDJ高位死叉
    if "KDJ_K" in df.columns and "KDJ_D" in df.columns:
        if (df["KDJ_K"].iloc[i] < df["KDJ_D"].iloc[i] and
                df["KDJ_K"].iloc[i - 1] >= df["KDJ_D"].iloc[i - 1] and
                df["KDJ_K"].iloc[i] > 50):
            confirmations.append("KDJ高位死叉")

    # 5. 价格触及布林上轨回落
    if "BB_upper" in df.columns:
        if p["High"] >= df["BB_upper"].iloc[i - 1] and c["Close"] < df["BB_upper"].iloc[i]:
            confirmations.append("布林上轨回落")

    # 6. 接近压力位
    if sr_levels:
        resistances = sr_levels.get("all_resistances", [])
        for r in resistances[:3]:
            rp = r.get("price", 0)
            if rp > 0 and abs(c["Close"] - rp) / rp < 0.01:
                confirmations.append(f"接近压力{r.get('method','')}")
                break

    # 7. 缩量上涨（量价背离）
    volume_ma5 = _volume_ma5_column(df)
    if "Volume" in df.columns and volume_ma5 is not None:
        if c["Close"] > p["Close"] and df[volume_ma5].iloc[i] > 0 and c["Volume"] < 0.7 * df[volume_ma5].iloc[i]:
            confirmations.append("缩量上涨(量价背离)")

    # 8. TD序列卖出信号（Setup 9 或 Countdown 13）
    if "TD_Setup" in df.columns:
        td_val = df["TD_Setup"].iloc[i]
        if td_val == -9:
            confirmations.append("TD卖出信号")
    if "TD_Signal" in df.columns:
        td_sig = df["TD_Signal"].iloc[i]
        if td_sig == -1:
            confirmations.append("TD卖出信号")
        elif td_sig == -2:
            confirmations.append("TD卖出信号")  # Countdown 13 确认

    # 9. MACD顶背离（价格创新高但MACD柱未创新高）
    if "MACD_hist" in df.columns and i >= 20:
        price_high_20 = df["High"].iloc[i-20:i].max()
        hist_high_20 = df["MACD_hist"].iloc[i-20:i].max()
        if (c["High"] >= price_high_20 * 0.995 and
            df["MACD_hist"].iloc[i] < hist_high_20 * 0.7 and
            df["MACD_hist"].iloc[i] > 0):
            confirmations.append("MACD顶背离")

    # 10. v4 趋势强度确认桶：ADX 趋势衰减 + 多周期看空
    _trend_str_sell = False
    if "ADX" in df.columns and i >= 5:
        adx_val = float(df["ADX"].iloc[i])
        adx_prev5 = float(df["ADX"].iloc[i - 5])
        # ADX 从高位回落（趋势衰竭）或 ADX 低 + 多周期空头
        adx_declining = adx_val < adx_prev5 * 0.85 and adx_prev5 > 25
        adx_weak_bear = adx_val < 20
        if adx_declining or adx_weak_bear:
            mtf_bear = _as_of_multi_timeframe_bias(df, i) == "bearish"
            sar_bear = True
            if "SAR" in df.columns and pd.notna(df["SAR"].iloc[i]):
                sar_bear = float(c["Close"]) < float(df["SAR"].iloc[i])
            if mtf_bear and sar_bear:
                _trend_str_sell = True
    if _trend_str_sell:
        confirmations.append("ADX趋势弱+多周期看空")

    # 11. v4 OBV smart money: bearish divergence + distribution regime
    if "OBV_Divergence" in df.columns and "OBV_Regime" in df.columns:
        obv_div = int(df["OBV_Divergence"].iloc[i]) if pd.notna(df["OBV_Divergence"].iloc[i]) else 0
        obv_regime = str(df["OBV_Regime"].iloc[i])
        if obv_div == -1 or obv_regime == "distribution":
            confirmations.append("OBV顶背离派发")

    return confirmations


# ── 复合信号生成 ──────────────────────────────────────────────────────

def generate_composite_signals(df: pd.DataFrame, analysis: Dict,
                                sr_levels: Dict = None) -> Dict:
    """
    复合买卖信号生成器
    扫描最近N根K线，标记买卖信号点及其确认强度
    """
    if df is None or df.empty or len(df) < 20:
        return {"buy_signals": [], "sell_signals": [],
                "current_signal": "观望", "signal_strength": "弱",
                "trade_suggestion": {}}

    if sr_levels is None:
        sr_levels = {}

    buy_signals = []
    sell_signals = []

    # 计算自适应RSI阈值（基于近期波动率）
    rsi_th = adaptive_rsi_thresholds(df)

    # 扫描最近60根K线（或全部数据取较小值）
    scan_start = max(1, len(df) - 60)

    for i in range(scan_start, len(df)):
        # 买入条件：独立桶负责去重和触发，逐 K 趋势分保留连续信息。
        buy_confs = _check_buy_conditions(df, i, sr_levels, rsi_thresholds=rsi_th)
        n_buckets, hit = aggregate_buckets(buy_confs, BUY_SIGNAL_BUCKETS)
        if n_buckets >= 2:
            strength = "强" if n_buckets >= 3 else "中"
            confidence = score_signal_evidence(
                independent_bucket_count=n_buckets,
                trend_strength=online_trend_strength(df, i),
                direction="buy",
                trend_evidence_available=i >= 60,
            )
            buy_signals.append({
                "bar_index": i,
                "date": str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i]),
                "price": float(df["Low"].iloc[i]),
                "close": float(df["Close"].iloc[i]),
                "type": "buy",
                "strength": strength,
                "confidence_level": signal_evidence_level(confidence.score),
                "confirmations": buy_confs,
                "buckets": hit,
                "confidence": confidence.score,
                "confidence_components": {
                    "version": confidence.version,
                    "semantics": confidence.semantics,
                    "independent_bucket_count": confidence.independent_bucket_count,
                    "bucket_score": confidence.bucket_score,
                    "directional_trend_score": confidence.directional_trend_score,
                    "trend_evidence_status": confidence.trend_evidence_status,
                },
            })

        # 卖出条件
        sell_confs = _check_sell_conditions(df, i, sr_levels, rsi_thresholds=rsi_th)
        n_buckets_s, hit_s = aggregate_buckets(sell_confs, SELL_SIGNAL_BUCKETS)
        if n_buckets_s >= 2:
            strength = "强" if n_buckets_s >= 3 else "中"
            confidence = score_signal_evidence(
                independent_bucket_count=n_buckets_s,
                trend_strength=online_trend_strength(df, i),
                direction="sell",
                trend_evidence_available=i >= 60,
            )
            sell_signals.append({
                "bar_index": i,
                "date": str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i]),
                "price": float(df["High"].iloc[i]),
                "close": float(df["Close"].iloc[i]),
                "type": "sell",
                "strength": strength,
                "confidence_level": signal_evidence_level(confidence.score),
                "confirmations": sell_confs,
                "buckets": hit_s,
                "confidence": confidence.score,
                "confidence_components": {
                    "version": confidence.version,
                    "semantics": confidence.semantics,
                    "independent_bucket_count": confidence.independent_bucket_count,
                    "bucket_score": confidence.bucket_score,
                    "directional_trend_score": confidence.directional_trend_score,
                    "trend_evidence_status": confidence.trend_evidence_status,
                },
            })

    # 当前K线信号判断
    current_signal = "观望"
    signal_strength = "弱"

    if buy_signals and (not sell_signals or buy_signals[-1]["bar_index"] > sell_signals[-1]["bar_index"]):
        last_buy = buy_signals[-1]
        if last_buy["bar_index"] >= len(df) - 3:  # 最近3根内有买入信号
            current_signal = "买入"
            signal_strength = last_buy["strength"]
    elif sell_signals and (not buy_signals or sell_signals[-1]["bar_index"] > buy_signals[-1]["bar_index"]):
        last_sell = sell_signals[-1]
        if last_sell["bar_index"] >= len(df) - 3:
            current_signal = "卖出"
            signal_strength = last_sell["strength"]

    # 交易建议
    trade_suggestion = calc_trade_suggestion(
        float(df["Close"].iloc[-1]), sr_levels, current_signal, df
    )

    return {
        "buy_signals": buy_signals[-10:],   # 最近10个买入信号
        "sell_signals": sell_signals[-10:],  # 最近10个卖出信号
        "current_signal": current_signal,
        "signal_strength": signal_strength,
        "trade_suggestion": trade_suggestion,
    }


# ── 精确交易建议 ──────────────────────────────────────────────────────

def calc_trade_suggestion(current_price: float, sr_levels: Dict,
                          signal_type: str, df: pd.DataFrame) -> Dict:
    """
    基于支撑压力位生成入场/止损/止盈建议
    """
    if not current_price or current_price <= 0:
        return {}

    supports = sr_levels.get("all_supports", [])
    resistances = sr_levels.get("all_resistances", [])

    # ATR用于止损距离参考
    atr = 0
    if df is not None and len(df) >= 14:
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift(1)).abs(),
            (df["Low"] - df["Close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = float(tr.tail(14).mean())

    if signal_type == "买入":
        # 入场区间：当前价-0.5% 到 当前价
        entry_low = current_price * 0.995
        entry_high = current_price

        # 止损：最近支撑位下方，或ATR的1.5倍
        if supports:
            nearest_support = supports[0]["price"]
            stop_loss = nearest_support * 0.995  # 支撑位下方0.5%
        elif atr > 0:
            stop_loss = current_price - atr * 1.5
        else:
            stop_loss = current_price * 0.95  # 默认5%

        # 止盈1：最近压力位
        tp1 = resistances[0]["price"] if resistances else current_price * 1.05
        # 止盈2：第二压力位或Fib扩展
        tp2 = resistances[1]["price"] if len(resistances) > 1 else current_price * 1.10

    elif signal_type == "卖出":
        entry_low = current_price
        entry_high = current_price * 1.005

        # 止损：最近压力位上方
        if resistances:
            nearest_resistance = resistances[0]["price"]
            stop_loss = nearest_resistance * 1.005
        elif atr > 0:
            stop_loss = current_price + atr * 1.5
        else:
            stop_loss = current_price * 1.05

        # 止盈（做空）
        tp1 = supports[0]["price"] if supports else current_price * 0.95
        tp2 = supports[1]["price"] if len(supports) > 1 else current_price * 0.90

    else:  # 观望
        return {
            "action": "观望",
            "reason": "无明确买卖信号，建议等待",
        }

    # 风险收益比
    risk = abs(current_price - stop_loss)
    reward = abs(tp1 - current_price)
    rr_ratio = reward / risk if risk > 0 else 0

    return {
        "action": signal_type,
        "entry_zone": [round(entry_low, 4), round(entry_high, 4)],
        "stop_loss": round(stop_loss, 4),
        "take_profit_1": round(tp1, 4),
        "take_profit_2": round(tp2, 4),
        "risk_reward_ratio": round(rr_ratio, 2),
        "atr": round(atr, 4) if atr > 0 else None,
        "risk_pct": round(risk / current_price * 100, 2) if current_price > 0 else 0,
    }


# ── 位置分类 ──────────────────────────────────────────────────────────

def classify_position(df: pd.DataFrame, sr_levels: Dict,
                      signals: Dict = None) -> Dict:
    """
    分类当前股票所处位置

    Returns: {
        "position_type": str,   # 低吸点/突破点/破位点/支撑反弹/无明确信号
        "confidence": int,      # 0-100
        "reasons": list,        # 判定原因
        "action": str,          # 建议操作
    }
    """
    if df is None or df.empty or len(df) < 20:
        return {"position_type": "无明确信号", "confidence": 0,
                "reasons": ["数据不足"], "action": "观望"}

    c = df.iloc[-1]
    p = df.iloc[-2] if len(df) >= 2 else c
    close = float(c["Close"])

    supports = sr_levels.get("all_supports", []) if sr_levels else []
    resistances = sr_levels.get("all_resistances", []) if sr_levels else []

    # 获取指标值
    rsi = float(df["RSI"].iloc[-1]) if "RSI" in df.columns else 50
    rsi_prev = float(df["RSI"].iloc[-2]) if "RSI" in df.columns and len(df) >= 2 else rsi

    vol = float(c.get("Volume", 0))
    volume_ma5 = _volume_ma5_column(df)
    vol_ma5 = float(df[volume_ma5].iloc[-1]) if volume_ma5 is not None else vol
    vol_ratio = vol / vol_ma5 if vol_ma5 > 0 else 1.0

    ema8 = float(df["EMA_8"].iloc[-1]) if "EMA_8" in df.columns else close
    ema21 = float(df["EMA_21"].iloc[-1]) if "EMA_21" in df.columns else close

    macd_hist = float(df["MACD_hist"].iloc[-1]) if "MACD_hist" in df.columns else 0
    macd_hist_prev = float(df["MACD_hist"].iloc[-2]) if "MACD_hist" in df.columns and len(df) >= 2 else 0

    # ── 低吸点检测 ──
    dip_reasons = []
    if supports:
        nearest_sup = supports[0]["price"]
        dist_pct = (close - nearest_sup) / nearest_sup * 100 if nearest_sup > 0 else 999
        if dist_pct < 2:
            dip_reasons.append(f"价格距支撑位{dist_pct:.1f}%")
    if rsi < 35:
        dip_reasons.append(f"RSI超卖({rsi:.0f})")
    if vol_ratio < 0.8:
        dip_reasons.append(f"缩量({vol_ratio:.1f}x)")
    if abs(macd_hist) < abs(macd_hist_prev) and macd_hist < 0:
        dip_reasons.append("MACD柱收窄")

    # ── 突破点检测 ──
    break_reasons = []
    if resistances:
        nearest_res = resistances[0]["price"]
        if close > nearest_res and float(p["Close"]) <= nearest_res:
            break_reasons.append(f"突破压力位{nearest_res:.2f}")
    if vol_ratio > 1.5:
        break_reasons.append(f"放量({vol_ratio:.1f}x)")
    if ema8 > ema21:
        break_reasons.append("EMA多头排列")
    if macd_hist > 0 and abs(macd_hist) > abs(macd_hist_prev):
        break_reasons.append("MACD柱放大")

    # ── 破位点检测 ──
    breakdown_reasons = []
    if supports:
        nearest_sup = supports[0]["price"]
        if close < nearest_sup and float(p["Close"]) >= nearest_sup:
            breakdown_reasons.append(f"跌破支撑位{nearest_sup:.2f}")
    if vol_ratio > 1.2:
        breakdown_reasons.append(f"放量下跌({vol_ratio:.1f}x)")
    if ema8 < ema21:
        breakdown_reasons.append("EMA空头排列")
    if rsi < 40 and rsi < rsi_prev:
        breakdown_reasons.append(f"RSI下行({rsi:.0f})")

    # ── 支撑反弹检测 ──
    bounce_reasons = []
    if supports:
        nearest_sup = supports[0]["price"]
        dist_pct = abs(close - nearest_sup) / nearest_sup * 100 if nearest_sup > 0 else 999
        if dist_pct < 3 and close > float(p["Close"]):
            bounce_reasons.append(f"支撑位附近反弹")
    if len(df) >= 3 and "Volume" in df.columns and vol_ratio > 1.0 and vol > float(df["Volume"].iloc[-3]):
        bounce_reasons.append("量能从低位回升")
    if 30 <= rsi <= 45 and rsi > rsi_prev:
        bounce_reasons.append(f"RSI从低位上行({rsi:.0f})")

    # ── 优先级判定 ──
    candidates = [
        ("低吸点", dip_reasons, "逢低分批建仓"),
        ("突破点", break_reasons, "突破确认后追多"),
        ("破位点", breakdown_reasons, "止损或回避"),
        ("支撑反弹", bounce_reasons, "轻仓试多"),
    ]

    best = None
    for pos_type, reasons, action in candidates:
        if len(reasons) >= 2:
            conf = min(95, 30 + len(reasons) * 20)
            if best is None or conf > best["confidence"]:
                best = {
                    "position_type": pos_type,
                    "confidence": conf,
                    "reasons": reasons,
                    "action": action,
                }

    if best:
        return best

    return {
        "position_type": "无明确信号",
        "confidence": 0,
        "reasons": ["不满足任何明确分类条件"],
        "action": "观望",
    }
