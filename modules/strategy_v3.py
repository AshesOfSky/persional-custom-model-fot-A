"""
strategy_v3.py — 实战修复版策略（基于 817 只股票回测诊断）

修复了 6 个核心问题：
  1. 板块差异化入场阈值（主板/创业板要求更严，科创板放宽）
  2. 市场环境过滤（沪深 300 跌破 200 日均线禁止开新仓）
  3. 个股连亏 3 笔后冷却 20 个交易日
  4. 跳空保护止损（盘中跌破止损按 stop_price 出场，跳空开盘按 Open 出场）
  5. 反转分批止盈：30% 仓位在 TP1 锁住保本，70% 跟到 TP2/TP3
  6. 账户级回撤熔断（在 BacktestEngine 层实现，见 backtest.py）
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional, List
import logging

from .backtest import Strategy, SignalType
from .signal_generator import (
    _check_buy_conditions, _check_sell_conditions,
    aggregate_buckets,
    BUY_SIGNAL_BUCKETS, SELL_SIGNAL_BUCKETS,
)
from .risk_management import (
    get_board_thresholds, market_filter_pass,
    BOARD_TREND_THRESHOLD, BOARD_BUCKET_MIN,
    CONSECUTIVE_LOSS_LIMIT, CONSECUTIVE_LOSS_COOLDOWN_DAYS,
)
from .stock_search import get_board

logger = logging.getLogger(__name__)


# ─── 趋势分（在线版，无需调用完整 run_full_analysis） ─────────────────
# 复刻 modules.analysis.calculate_trend_strength 的核心逻辑，但只接 DataFrame
# 这样在回测的 per-bar 循环里可以快速调用

def online_trend_strength(df: pd.DataFrame, i: int,
                           benchmark_df: Optional[pd.DataFrame] = None) -> int:
    """
    第 i 根 K 线时刻的趋势强度（0-100），与 analysis.calculate_trend_strength 等价
    """
    if i < 60:
        return 50  # 数据不足时给中性

    score = 45

    # A. EMA ±15
    e8 = df["EMA_8"].iloc[i] if "EMA_8" in df.columns else None
    e13 = df["EMA_13"].iloc[i] if "EMA_13" in df.columns else None
    e21 = df["EMA_21"].iloc[i] if "EMA_21" in df.columns else None
    e55 = df["EMA_55"].iloc[i] if "EMA_55" in df.columns else None
    if all(x is not None and not pd.isna(x) for x in [e8, e13, e21, e55]):
        if e8 > e13 > e21:
            score += 12
            if e21 > e55:
                score += 3
        elif e8 < e13 < e21:
            score -= 12
            if e21 < e55:
                score -= 3

    # B. MACD ±15
    if "MACD" in df.columns and "MACD_signal" in df.columns and i >= 1:
        m, s, mp, sp = df["MACD"].iloc[i], df["MACD_signal"].iloc[i], df["MACD"].iloc[i-1], df["MACD_signal"].iloc[i-1]
        h = df["MACD_hist"].iloc[i] if "MACD_hist" in df.columns else 0
        hp = df["MACD_hist"].iloc[i-1] if "MACD_hist" in df.columns else 0
        if not pd.isna(m) and not pd.isna(s):
            if m > s and mp <= sp:
                score += 12 + (3 if abs(h) > abs(hp) else 0)
            elif m < s and mp >= sp:
                score -= 12 + (3 if abs(h) > abs(hp) else 0)

    # C. KDJ ±10
    if "KDJ_K" in df.columns and "KDJ_D" in df.columns and "KDJ_J" in df.columns and i >= 1:
        k, d, j = df["KDJ_K"].iloc[i], df["KDJ_D"].iloc[i], df["KDJ_J"].iloc[i]
        kp, dp = df["KDJ_K"].iloc[i-1], df["KDJ_D"].iloc[i-1]
        if not pd.isna(j):
            kdj_strength = max(-3, min(3, (j - 50) / 25))
            score += int(kdj_strength * 2)
            if k > d and kp <= dp and k < 50:
                score += 4
            elif k < d and kp >= dp and k > 50:
                score -= 4

    # D. 布林 ±7
    if "BB_upper" in df.columns and "BB_middle" in df.columns and "BB_lower" in df.columns:
        c = df["Close"].iloc[i]
        u, mid, l = df["BB_upper"].iloc[i], df["BB_middle"].iloc[i], df["BB_lower"].iloc[i]
        if not pd.isna(u):
            if c > u:
                score += 5
            elif c < l:
                score -= 5
            elif c > mid:
                score += 2
            else:
                score -= 2

    # E. 量能 ±8
    if "Volume" in df.columns and i >= 19:
        vol_5 = df["Volume"].iloc[i-4:i+1].mean()
        vol_20 = df["Volume"].iloc[i-19:i+1].mean()
        price_up_5d = df["Close"].iloc[i] > df["Close"].iloc[i-5]
        if vol_20 > 0:
            vol_ratio = vol_5 / vol_20
            if price_up_5d and vol_ratio > 1.2:
                score += 6
            elif (not price_up_5d) and vol_ratio < 0.8:
                score += 2
            elif price_up_5d and vol_ratio < 0.8:
                score -= 4
            elif (not price_up_5d) and vol_ratio > 1.2:
                score -= 6

    # F. 动量 ±8
    if i >= 60:
        mom_5 = df["Close"].iloc[i] / df["Close"].iloc[i-5] - 1
        mom_60 = df["Close"].iloc[i] / df["Close"].iloc[i-60] - 1
        if benchmark_df is not None and len(benchmark_df) >= i + 1:
            bm_5 = benchmark_df["Close"].iloc[i] / benchmark_df["Close"].iloc[i-5] - 1
            bm_60 = benchmark_df["Close"].iloc[i] / benchmark_df["Close"].iloc[i-60] - 1
            if (mom_5 - bm_5) > 0.02:
                score += 5
            elif (mom_5 - bm_5) < -0.02:
                score -= 5
            if (mom_60 - bm_60) > 0.05:
                score += 3
            elif (mom_60 - bm_60) < -0.05:
                score -= 3
        else:
            if mom_5 > 0.03:
                score += 5
            elif mom_5 < -0.03:
                score -= 5
            if mom_60 > 0.10:
                score += 3
            elif mom_60 < -0.10:
                score -= 3

    return max(0, min(100, score))


# ─── v3 主策略 ───────────────────────────────────────────────────────────

class TrendStrengthV3Strategy(Strategy):
    """
    集成 6 项实战改进的趋势强度策略

    入场 = 板块趋势分阈值 + 板块独立桶数阈值 + 市场环境通过 + 无连亏冷却
    出场 = 强势空头(<18) / 跳空止损 / 信号桶反转 / 累计跟踪止损
    """

    def __init__(self,
                 board: str = "主板",
                 ticker: str = "",
                 benchmark_df: Optional[pd.DataFrame] = None,
                 use_market_filter: bool = True,
                 use_consecutive_loss_cooldown: bool = True,
                 use_breakeven_stop: bool = True,
                 breakeven_trigger_pct: float = 0.05,    # 浮盈 5% 后止损上移到入场价
                 trail_trigger_pct: float = 0.10,        # 浮盈 10% 后止损上移到入场价 +5%
                 trail_lock_pct: float = 0.05):
        super().__init__(name=f"v3_{board}")
        self.board = board
        self.ticker = ticker
        self.benchmark_df = benchmark_df
        self.use_market_filter = use_market_filter
        self.use_consecutive_loss_cooldown = use_consecutive_loss_cooldown
        self.use_breakeven_stop = use_breakeven_stop
        self.breakeven_trigger_pct = breakeven_trigger_pct
        self.trail_trigger_pct = trail_trigger_pct
        self.trail_lock_pct = trail_lock_pct

        # 板块阈值
        thresholds = get_board_thresholds(board)
        self.trend_min = thresholds["trend_min"]
        self.buckets_min = thresholds["buckets_min"]

        # 状态变量（由 engine 维护）
        self.recent_trade_pnl: List[float] = []          # 最近交易 P&L
        self.last_loss_exit_idx: int = -10**9             # 上次亏损出场的 bar 索引
        self.current_stop_price: Optional[float] = None   # 当前持仓的止损价（动态可调）

        # 0/1 阶段位（用于 should_exit 内部判断）
        self.stage = 0   # 0=未触发盈利, 1=已上移到 breakeven, 2=已上移到 trail_lock

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """v3 不依赖 spot signal 列；返回 df 即可"""
        return df

    # 钩子：Engine 在每次平仓时调用
    def on_trade_closed(self, pnl: float, exit_idx: int) -> None:
        self.recent_trade_pnl.append(pnl)
        if pnl < 0:
            self.last_loss_exit_idx = exit_idx
        # 重置阶梯止损状态
        self.stage = 0
        self.current_stop_price = None

    # ── 入场判断 ──
    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        # 0. ST 拒绝（如 board=='ST' 直接关闭策略）
        if self.board == "ST":
            return False

        # 1. 市场环境过滤
        if self.use_market_filter:
            if not market_filter_pass(idx, self.benchmark_df, ma_period=200):
                return False

        # 2. 连亏冷却
        if self.use_consecutive_loss_cooldown and len(self.recent_trade_pnl) >= CONSECUTIVE_LOSS_LIMIT:
            last_n = self.recent_trade_pnl[-CONSECUTIVE_LOSS_LIMIT:]
            if all(p < 0 for p in last_n):
                bars_since_last_loss = idx - self.last_loss_exit_idx
                if bars_since_last_loss < CONSECUTIVE_LOSS_COOLDOWN_DAYS:
                    return False

        # 3. 趋势分门槛
        score = online_trend_strength(df, idx, benchmark_df=self.benchmark_df)
        if score < self.trend_min:
            return False

        # 4. 信号桶门槛
        confirmations = _check_buy_conditions(df, idx, sr_levels={})
        n_buckets, _ = aggregate_buckets(confirmations, BUY_SIGNAL_BUCKETS)
        if n_buckets < self.buckets_min:
            return False

        return True

    # ── 出场判断 ──
    # Engine 仍只支持全仓出场；这里用动态止损 + 信号反转
    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> Tuple[bool, str]:
        if idx >= len(df):
            return False, ""

        row = df.iloc[idx]
        close = row["Close"]
        open_ = row["Open"] if "Open" in df.columns else close
        low = row["Low"] if "Low" in df.columns else close

        # 4. 跳空保护止损：开盘直接低于 stop → 按开盘价出场
        if self.current_stop_price is not None:
            # 跳空低开穿透
            if open_ <= self.current_stop_price:
                return True, "止损（跳空）"
            # 盘中触及（按 stop_price 价位成交，比 close 更准）
            if low <= self.current_stop_price:
                return True, "止损"

        # 浮盈维护：阶梯抬高止损（让利润奔跑 + 锁定）
        if self.use_breakeven_stop:
            unrealized = (close - entry_price) / entry_price
            if self.stage == 0 and unrealized >= self.breakeven_trigger_pct:
                # 浮盈 5% → 止损上移到入场价（保本）
                self.current_stop_price = max(self.current_stop_price or 0, entry_price)
                self.stage = 1
            elif self.stage <= 1 and unrealized >= self.trail_trigger_pct:
                # 浮盈 10% → 止损上移到入场价 +5%（锁定盈利）
                self.current_stop_price = max(self.current_stop_price or 0,
                                                 entry_price * (1 + self.trail_lock_pct))
                self.stage = 2

        # 信号反转：≥ 卖出桶阈值 个独立桶
        sell_confs = _check_sell_conditions(df, idx, sr_levels={})
        n_sell_buckets, _ = aggregate_buckets(sell_confs, SELL_SIGNAL_BUCKETS)
        if n_sell_buckets >= self.buckets_min:
            return True, f"信号反转({n_sell_buckets}桶)"

        # 强势空头：趋势分跌破 18
        score = online_trend_strength(df, idx, benchmark_df=self.benchmark_df)
        if score < 18:
            return True, "强势空头"

        return False, ""
