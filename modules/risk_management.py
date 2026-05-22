"""
risk_management.py — 仓位与止损管理
ATR-based 动态止损（按板块/市值差异化），统一服务回测引擎和实盘建议。

v3 新增：板块差异化入场阈值、市场环境过滤、账户级回撤熔断参数。
"""

from typing import Optional, Dict
import pandas as pd


# ATR 倍数：板块越投机 → 止损给越多波动空间
BOARD_ATR_MULT = {
    "主板":     2.0,
    "沪深300":  1.8,    # 大盘蓝筹更紧
    "创业板":   2.5,
    "科创板":   2.5,
    "北交所":   3.0,    # 流动性差，止损要宽
    "其他":     2.0,    # 美股/港股等
}

# v3: 板块差异化入场阈值（基于 817 只股票回测 + 实证调校）
# 实证发现：6 个 buy buckets 中同一根 K 线最多触发 2 个，buckets>=3 几乎不命中
# 因此主要用 trend_score 做板块差异化，buckets 统一 >=2
BOARD_TREND_THRESHOLD = {
    "科创板": 55,    # 高波动 + 已有 alpha，门槛最低
    "创业板": 62,    # 中位 -4.35% 厚尾分布，门槛中等
    "主板":   65,    # 蓝筹不友好，必须更强信号
    "北交所": 70,    # 流动性差，最严
    "其他":   60,    # 港股/美股默认
}

BOARD_BUCKET_MIN = {
    "科创板": 2,
    "创业板": 2,
    "主板":   2,
    "北交所": 2,
    "其他":   2,
}

# v3: 账户级回撤熔断默认参数
ACCOUNT_DRAWDOWN_LIMIT = 0.20   # 累计 -20% 暂停
COOLDOWN_BARS = 20              # 暂停 20 个交易日

# v3: 个股连亏冷却
CONSECUTIVE_LOSS_LIMIT = 3      # 连续 3 笔亏损
CONSECUTIVE_LOSS_COOLDOWN_DAYS = 20

# 市值修正系数：小盘股给更多空间
def _cap_adjust(market_cap_yi: Optional[float]) -> float:
    if market_cap_yi is None:
        return 1.0
    if market_cap_yi >= 500:
        return 0.9
    elif market_cap_yi >= 100:
        return 1.0
    elif market_cap_yi >= 30:
        return 1.15
    return 1.3


def dynamic_stop_loss(
    entry_price: float,
    atr: Optional[float],
    board: str = "主板",
    market_cap_yi: Optional[float] = None,
    direction: str = "long",
    fallback_pct: float = 0.05,
) -> dict:
    """
    基于 ATR 的动态止损

    参数:
        entry_price:    入场价
        atr:            最近 ATR；若为 None/0 则回退到 fallback_pct 百分比止损
        board:          板块，由 modules.stock_search.get_board() 提供
        market_cap_yi:  流通市值（亿）；可缺省
        direction:      'long' 或 'short'
        fallback_pct:   ATR 不可用时的兜底百分比

    返回:
        {
          'stop_price':  Optional[float]  # ST 标的返回 None 表示不建议入场
          'stop_pct':    float            # 距入场价的百分比距离
          'atr_mult':    float
          'method':      str              # 描述性字符串
        }

    硬约束：
        - ST 板块：返回 None（不建议入场）
        - 多头止损至少距入场价 2%（防假止损），最多 12%（防风险过大）
        - 空头止损对称：至少 2%，最多 12%
    """
    # ST 风险 → 直接拒绝
    if board == "ST":
        return {
            "stop_price": None,
            "stop_pct": 0.0,
            "atr_mult": 0.0,
            "method": "禁入_ST 风险标的",
        }

    base_mult = BOARD_ATR_MULT.get(board, 2.0)
    cap_adj = _cap_adjust(market_cap_yi)
    eff_mult = base_mult * cap_adj

    # ATR 不可用时回退到固定百分比
    if atr is None or atr <= 0:
        if direction == "long":
            stop = entry_price * (1 - fallback_pct)
        else:
            stop = entry_price * (1 + fallback_pct)
        return {
            "stop_price": round(stop, 4),
            "stop_pct": fallback_pct,
            "atr_mult": 0.0,
            "method": f"回退百分比 {fallback_pct*100:.0f}%（ATR 不可用）",
        }

    atr_distance = atr * eff_mult

    if direction == "long":
        raw_stop = entry_price - atr_distance
        max_stop = entry_price * 0.98   # 止损至少 2% 远（不能太紧）
        min_stop = entry_price * 0.88   # 止损不超 12% 远（不能太宽）
        stop_price = min(max_stop, max(min_stop, raw_stop))
    else:
        raw_stop = entry_price + atr_distance
        min_stop = entry_price * 1.02
        max_stop = entry_price * 1.12
        stop_price = max(min_stop, min(max_stop, raw_stop))

    return {
        "stop_price": round(stop_price, 4),
        "stop_pct": abs(stop_price - entry_price) / entry_price,
        "atr_mult": round(eff_mult, 2),
        "method": f"ATR×{eff_mult:.2f} ({board}" + (f"/市值{market_cap_yi:.0f}亿" if market_cap_yi else "") + ")",
    }


# ─── v3: 市场环境过滤 ─────────────────────────────────────────────────
# 大盘跌破 200 日均线时，所有 A 股策略停止开新仓。
# 这一层在 should_enter_long 之前生效，是系统性风险防御。

def market_filter_pass(idx: int,
                        benchmark_df: Optional[pd.DataFrame],
                        ma_period: int = 200) -> bool:
    """
    沪深 300（或其他基准）是否在长周期均线之上
    返回 True 表示允许开新仓，False 表示市场环境差禁止开仓
    """
    if benchmark_df is None or benchmark_df.empty:
        return True
    if idx < ma_period:
        return True
    if "Close" not in benchmark_df.columns:
        return True

    close = benchmark_df["Close"].iloc[:idx + 1]
    if len(close) < ma_period:
        return True
    ma200 = close.iloc[-ma_period:].mean()
    return float(close.iloc[-1]) > float(ma200)


def enhanced_market_filter(df: pd.DataFrame, idx: int,
                           benchmark_df: Optional[pd.DataFrame] = None) -> Dict:
    """
    增强版市场环境过滤器，返回综合评估结果。

    检查项:
      1. 基准均线过滤（MA200）
      2. 均线斜率方向（MA20/MA60 近5日斜率）
      3. 波动率突增检测（ATR 相对 20日均值是否 > 1.5x）
      4. 综合评分 → allow_full / allow_reduced / block

    返回:
      {
        "allow_entry": bool,        # 是否允许开仓
        "position_scale": float,    # 仓位缩放因子 (0.0 ~ 1.0)
        "regime": str,              # "bull" / "neutral" / "bear" / "volatile"
        "details": dict,            # 各项检查明细
      }
    """
    result = {
        "allow_entry": True,
        "position_scale": 1.0,
        "regime": "neutral",
        "details": {},
    }

    if idx < 60 or len(df) < 60:
        return result

    close = df["Close"].values[:idx + 1]

    # ── 1. 基准均线过滤 ──
    benchmark_ok = market_filter_pass(idx, benchmark_df)
    result["details"]["benchmark_ma200"] = benchmark_ok
    if not benchmark_ok:
        result["position_scale"] *= 0.5
        result["regime"] = "bear"

    # ── 2. 均线斜率方向 ──
    # MA20 斜率：最近5日 MA20 变化
    if len(close) >= 25:
        ma20_now = float(np.mean(close[-20:]))
        ma20_5ago = float(np.mean(close[-25:-5]))
        ma20_slope = (ma20_now - ma20_5ago) / ma20_5ago if ma20_5ago != 0 else 0
    else:
        ma20_slope = 0

    # MA60 斜率
    if len(close) >= 65:
        ma60_now = float(np.mean(close[-60:]))
        ma60_5ago = float(np.mean(close[-65:-5]))
        ma60_slope = (ma60_now - ma60_5ago) / ma60_5ago if ma60_5ago != 0 else 0
    else:
        ma60_slope = 0

    result["details"]["ma20_slope"] = round(ma20_slope, 6)
    result["details"]["ma60_slope"] = round(ma60_slope, 6)

    # 双均线下行 → 减仓
    if ma20_slope < -0.005 and ma60_slope < -0.003:
        result["position_scale"] *= 0.6
        if result["regime"] != "bear":
            result["regime"] = "bear"
    elif ma20_slope > 0.005 and ma60_slope > 0:
        if result["regime"] == "neutral":
            result["regime"] = "bull"

    # ── 3. 波动率突增检测 ──
    if "ATR" in df.columns and idx >= 20:
        atr_vals = df["ATR"].values[:idx + 1]
        atr_now = float(atr_vals[-1])
        atr_mean20 = float(np.mean(atr_vals[-20:]))
        atr_ratio = atr_now / atr_mean20 if atr_mean20 > 0 else 1.0
    else:
        # 手动算 ATR
        high = df["High"].values[:idx + 1]
        low = df["Low"].values[:idx + 1]
        if len(high) >= 21:
            tr = np.maximum(high[-20:] - low[-20:],
                           np.abs(high[-20:] - close[-21:-1]),
                           np.abs(low[-20:] - close[-21:-1]))
            atr_now = float(tr[-1])
            atr_mean20 = float(np.mean(tr))
            atr_ratio = atr_now / atr_mean20 if atr_mean20 > 0 else 1.0
        else:
            atr_ratio = 1.0

    result["details"]["atr_ratio"] = round(atr_ratio, 3)

    if atr_ratio > 2.0:
        # 极端波动 → 禁止开仓
        result["allow_entry"] = False
        result["position_scale"] = 0.0
        result["regime"] = "volatile"
    elif atr_ratio > 1.5:
        # 高波动 → 仓位减半
        result["position_scale"] *= 0.5
        if result["regime"] not in ("bear", "volatile"):
            result["regime"] = "volatile"

    # ── 综合判断 ──
    if result["position_scale"] < 0.2:
        result["allow_entry"] = False

    result["position_scale"] = round(max(0.0, min(1.0, result["position_scale"])), 2)

    return result
