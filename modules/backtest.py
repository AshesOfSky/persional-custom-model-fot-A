"""
backtest.py — 策略回测框架
支持EMA隧道趋势、布林带均值回归、KDJ超买超卖策略
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Callable, Optional
from datetime import datetime
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = 1
    SELL = -1
    HOLD = 0


@dataclass
class Trade:
    """交易记录"""
    entry_date: datetime
    exit_date: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    position: int = 0  # 1 for long, -1 for short
    shares: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    """回测结果"""
    strategy_name: str
    ticker: str
    period: str
    initial_capital: float
    final_capital: float
    total_return: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_profit: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    trades: List[Trade] = field(default_factory=list)
    equity_curve: pd.DataFrame = field(default_factory=pd.DataFrame)
    signals: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> str:
        """生成回测摘要"""
        return f"""
========================================
回测结果: {self.strategy_name}
标的: {self.ticker} ({self.period})
========================================
初始资金: {self.initial_capital:,.2f}
最终资金: {self.final_capital:,.2f}
总收益率: {self.total_return*100:.2f}%
总交易次数: {self.total_trades}
盈利次数: {self.winning_trades}
亏损次数: {self.losing_trades}
胜率: {self.win_rate*100:.2f}%
平均盈利: {self.avg_profit:.2f}
平均亏损: {self.avg_loss:.2f}
盈亏比: {abs(self.avg_profit/self.avg_loss) if self.avg_loss != 0 else 0:.2f}
最大回撤: {self.max_drawdown_pct*100:.2f}%
夏普比率: {self.sharpe_ratio:.2f}
========================================
"""


class Strategy:
    """策略基类"""

    def __init__(self, name: str):
        self.name = name

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成交易信号，子类需实现"""
        raise NotImplementedError

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        """是否入场，子类需实现"""
        raise NotImplementedError

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        """是否出场，返回(是否出场, 出场原因)，子类需实现"""
        raise NotImplementedError


class EMAStrategy(Strategy):
    """EMA隧道趋势跟踪策略"""

    def __init__(self,
                 use_inner_ema: bool = True,
                 use_outer_ema: bool = True,
                 stop_loss_pct: float = 0.05,
                 take_profit_pct: float = 0.15):
        super().__init__("EMA隧道趋势跟踪")
        self.use_inner_ema = use_inner_ema
        self.use_outer_ema = use_outer_ema
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成EMA趋势信号"""
        df = df.copy()

        # 确保EMA已计算
        for col in ['EMA_8', 'EMA_13', 'EMA_21', 'EMA_55', 'EMA_288']:
            if col not in df.columns:
                raise ValueError(f"缺少{col}，请先计算EMA指标")

        # 内隧道多头排列: EMA8 > EMA13 > EMA21
        df['inner_bull'] = (df['EMA_8'] > df['EMA_13']) & (df['EMA_13'] > df['EMA_21'])
        # 外隧道多头排列: 价格 > EMA55
        df['outer_bull'] = df['Close'] > df['EMA_55']

        # 买入信号：内外隧道都多头排列
        df['signal'] = SignalType.HOLD.value
        df.loc[df['inner_bull'] & df['outer_bull'], 'signal'] = SignalType.BUY.value

        # 卖出信号：跌破内隧道或外隧道
        df.loc[~(df['inner_bull'] | df['outer_bull']), 'signal'] = SignalType.SELL.value

        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        """入场条件：多头趋势确立"""
        if idx < 1:
            return False
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        # 价格突破内外隧道，且内隧道多头排列
        return (row['inner_bull'] and row['outer_bull'] and
                not (prev['inner_bull'] and prev['outer_bull']))

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        """出场条件：趋势反转或止盈止损"""
        row = df.iloc[idx]

        # 止盈
        if row['Close'] >= entry_price * (1 + self.take_profit_pct):
            return True, "止盈"

        # 止损
        if row['Close'] <= entry_price * (1 - self.stop_loss_pct):
            return True, "止损"

        # 趋势反转
        if not row['inner_bull'] and not row['outer_bull']:
            return True, "趋势反转"

        return False, ""


class BollingerStrategy(Strategy):
    """布林带均值回归策略"""

    def __init__(self,
                 bb_period: int = 20,
                 bb_std: float = 2.0,
                 stop_loss_pct: float = 0.05):
        super().__init__("布林带均值回归")
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.stop_loss_pct = stop_loss_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成布林带信号"""
        df = df.copy()

        if 'BB_upper' not in df.columns:
            from .technical import calc_bollinger
            df = calc_bollinger(df, self.bb_period, self.bb_std)

        # 买入信号：价格跌破下轨（超卖）
        df['signal'] = SignalType.HOLD.value
        df.loc[df['Close'] < df['BB_lower'], 'signal'] = SignalType.BUY.value

        # 卖出信号：价格突破上轨（超买）
        df.loc[df['Close'] > df['BB_upper'], 'signal'] = SignalType.SELL.value

        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        """入场条件：价格触及下轨"""
        if idx < 1:
            return False
        row = df.iloc[idx]

        # 价格跌破布林带下轨（均值回归机会）
        return row['Close'] < row['BB_lower']

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        """出场条件：回归中轨或止损"""
        row = df.iloc[idx]

        # 价格回归中轨
        if row['Close'] >= row['BB_middle']:
            return True, "回归中轨"

        # 止损
        if row['Close'] <= entry_price * (1 - self.stop_loss_pct):
            return True, "止损"

        return False, ""


class KDJStrategy(Strategy):
    """KDJ超买超卖策略"""

    def __init__(self,
                 oversold: int = 20,
                 overbought: int = 80,
                 stop_loss_pct: float = 0.05):
        super().__init__("KDJ超买超卖")
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """生成KDJ信号"""
        df = df.copy()

        if 'KDJ_K' not in df.columns:
            from .technical import calc_kdj
            df = calc_kdj(df)

        # 买入信号：K、D都低于超卖线
        df['signal'] = SignalType.HOLD.value
        df.loc[(df['KDJ_K'] < self.oversold) & (df['KDJ_D'] < self.oversold), 'signal'] = SignalType.BUY.value

        # 卖出信号：K、D都高于超买线
        df.loc[(df['KDJ_K'] > self.overbought) & (df['KDJ_D'] > self.overbought), 'signal'] = SignalType.SELL.value

        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        """入场条件：KDJ进入超卖区且金叉"""
        if idx < 1:
            return False
        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        # KDJ超卖区金叉
        kdj_oversold = row['KDJ_K'] < self.oversold and row['KDJ_D'] < self.oversold
        golden_cross = prev['KDJ_K'] <= prev['KDJ_D'] and row['KDJ_K'] > row['KDJ_D']

        return kdj_oversold and golden_cross

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        """出场条件：KDJ超买或死叉或止损"""
        if idx < 1:
            return False, ""

        row = df.iloc[idx]
        prev = df.iloc[idx - 1]

        # KDJ超买
        if row['KDJ_K'] > self.overbought and row['KDJ_D'] > self.overbought:
            return True, "KDJ超买"

        # KDJ死叉
        dead_cross = prev['KDJ_K'] >= prev['KDJ_D'] and row['KDJ_K'] < row['KDJ_D']
        if dead_cross:
            return True, "KDJ死叉"

        # 止损
        if row['Close'] <= entry_price * (1 - self.stop_loss_pct):
            return True, "止损"

        return False, ""


class BacktestEngine:
    """回测引擎"""

    def __init__(self,
                 initial_capital: float = 100000.0,
                 commission_rate: float = 0.0016,  # 含佣金+印花税0.05%+过户费
                 slippage: float = 0.001,
                 use_atr_stop: bool = True,
                 board: str = "其他",
                 market_cap_yi: Optional[float] = None,
                 # ── v3 新增 ──
                 use_drawdown_breaker: bool = False,
                 drawdown_limit: float = 0.20,
                 cooldown_bars: int = 20,
                 use_gap_protection: bool = False,
                 # ── v4 Kelly/ATR 动态仓位 ──
                 use_dynamic_sizing: bool = True,
                 kelly_fraction: float = 0.5,
                 max_position_pct: float = 0.95,
                 min_position_pct: float = 0.10,
                 atr_risk_pct: float = 0.02,
                 kelly_min_trades: int = 10,
                 # ── v4 移动止盈止损 ──
                 use_trailing_stop: bool = True,
                 trailing_tighten: bool = True):
        """
        参数:
            use_atr_stop: True 时按 ATR 动态计算止损（覆盖策略的 stop_loss_pct），
                          False 保留原始固定百分比止损。
            board:        板块（来自 modules.stock_search.get_board）
            market_cap_yi: 流通市值（亿）
            use_drawdown_breaker (v3): True 时账户累计回撤超限暂停开新仓
            drawdown_limit (v3):       回撤阈值，默认 20%
            cooldown_bars (v3):        熔断后冷却的交易日数
            use_gap_protection (v3):   True 时跳空开盘穿透 stop_price → 按 Open 出场
            use_dynamic_sizing (v4):   True 时启用 Kelly/ATR 动态仓位，False 回退固定 95%
            kelly_fraction (v4):       Kelly 倍率，0.5 = half-Kelly（推荐）
            max_position_pct (v4):     仓位上限占总资金比例
            min_position_pct (v4):     仓位下限占总资金比例
            atr_risk_pct (v4):         单笔最大 ATR 风险占总资金比例
            kelly_min_trades (v4):     Kelly 公式启用所需的最低历史交易笔数
            use_trailing_stop (v4):    True 时持仓中按 ATR 移动止损
            trailing_tighten (v4):     True 时随盈利增加阶梯收紧止损
        """
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.use_atr_stop = use_atr_stop
        self.board = board
        self.market_cap_yi = market_cap_yi

        # 滑点差异化：流动性差的板块/小盘股给更高滑点
        if slippage == 0.001:  # 仅在用户未自定义时自动调整
            _BOARD_SLIPPAGE = {"北交所": 0.005, "科创板": 0.003, "创业板": 0.002, "主板": 0.001, "其他": 0.001}
            base_slip = _BOARD_SLIPPAGE.get(board, 0.001)
            if market_cap_yi is not None and market_cap_yi < 30:
                base_slip = max(base_slip, 0.003)  # 小盘股至少 0.3%
            elif market_cap_yi is not None and market_cap_yi < 100:
                base_slip = max(base_slip, 0.002)
            self.slippage = base_slip
        else:
            self.slippage = slippage
        self.use_drawdown_breaker = use_drawdown_breaker
        self.drawdown_limit = drawdown_limit
        self.cooldown_bars = cooldown_bars
        self.use_gap_protection = use_gap_protection
        # v4: Kelly/ATR 动态仓位
        self.use_dynamic_sizing = use_dynamic_sizing
        self.kelly_fraction = kelly_fraction
        self.max_position_pct = max_position_pct
        self.min_position_pct = min_position_pct
        self.atr_risk_pct = atr_risk_pct
        self.kelly_min_trades = kelly_min_trades
        # v4: 移动止盈止损
        self.use_trailing_stop = use_trailing_stop
        self.trailing_tighten = trailing_tighten

    def _atr_stop_pct(self, entry_price: float, atr: Optional[float],
                     fallback: float) -> float:
        """计算单笔交易的 ATR-based 止损百分比"""
        if not self.use_atr_stop or atr is None or atr <= 0:
            return fallback
        from .risk_management import dynamic_stop_loss
        result = dynamic_stop_loss(
            entry_price, atr, board=self.board,
            market_cap_yi=self.market_cap_yi, direction="long",
            fallback_pct=fallback,
        )
        if result["stop_price"] is None:  # ST 标的
            return fallback
        return result["stop_pct"]

    # ── v4: Kelly / ATR 动态仓位计算 ──────────────────────
    def _calc_position_size(
        self,
        capital: float,
        entry_price: float,
        atr: Optional[float],
        stop_pct: float,
        closed_trades: List[Trade],
    ) -> float:
        """
        计算本笔交易的仓位金额（非股数）。

        逻辑：
        1. **ATR-risk sizing**: 根据 ATR 止损距离限制单笔风险 ≤ atr_risk_pct * capital
        2. **Kelly sizing**: 当历史交易 ≥ kelly_min_trades 时用 half-Kelly 上限
        3. 取两者较小值，再 clip 到 [min_position_pct, max_position_pct] * capital
        4. 未启用 dynamic_sizing 时退回固定 max_position_pct
        """
        if not self.use_dynamic_sizing:
            return capital * self.max_position_pct

        max_alloc = capital * self.max_position_pct
        min_alloc = capital * self.min_position_pct

        # ── ATR-risk sizing ──
        # 每笔最多亏损 atr_risk_pct * capital
        risk_dollar = capital * self.atr_risk_pct
        if stop_pct > 0:
            atr_alloc = risk_dollar / stop_pct
        else:
            atr_alloc = max_alloc
        atr_alloc = min(atr_alloc, max_alloc)

        # ── Kelly sizing ──
        kelly_alloc = max_alloc  # 默认不限
        n = len(closed_trades)
        if n >= self.kelly_min_trades:
            wins = [t for t in closed_trades if t.pnl > 0]
            losses = [t for t in closed_trades if t.pnl <= 0]
            p = len(wins) / n  # 胜率
            if losses and wins:
                avg_win = np.mean([t.pnl_pct for t in wins])
                avg_loss = abs(np.mean([t.pnl_pct for t in losses]))
                if avg_loss > 0:
                    b = avg_win / avg_loss  # 赔率
                    kelly_f = (p * b - (1 - p)) / b
                    # 应用 kelly_fraction（half-Kelly）并 cap 到 [0, max_position_pct]
                    kelly_f = max(0.0, kelly_f) * self.kelly_fraction
                    kelly_f = min(kelly_f, self.max_position_pct)
                    kelly_alloc = capital * kelly_f

        # ── 取两者较小值并 clip ──
        alloc = min(atr_alloc, kelly_alloc)
        alloc = max(alloc, min_alloc)
        alloc = min(alloc, max_alloc)
        return alloc

    def run(self, df: pd.DataFrame, strategy: Strategy, ticker: str = "", period: str = "") -> BacktestResult:
        """运行回测"""
        logger.info(f"开始回测: {strategy.name} on {ticker}")

        # 生成信号
        df = strategy.generate_signals(df.copy())

        # 确保 ATR 列存在（动态止损需要）
        if self.use_atr_stop and "ATR" not in df.columns:
            try:
                from .technical import calc_atr
                df = calc_atr(df)
            except Exception:
                logger.warning("ATR 计算失败，回退到固定百分比止损")

        # 初始化
        capital = self.initial_capital
        position = 0  # 当前持仓方向：0空仓，1多仓
        entry_price = 0.0
        trades: List[Trade] = []
        equity_curve = []
        original_stop_pct = getattr(strategy, "stop_loss_pct", 0.05)

        # v3: 账户级回撤熔断状态
        peak_capital = self.initial_capital
        breaker_cooldown_remaining = 0    # 还需冷却的 bar 数
        breaker_triggered_count = 0       # 熔断触发次数（统计用）

        current_trade: Optional[Trade] = None
        trailing_tracker = None  # v4: 移动止损追踪器

        for i in range(len(df)):
            date = df.index[i]
            row = df.iloc[i]
            price = row['Close']

            # 更新权益曲线
            if position == 0:
                market_value = capital
            else:
                market_value = capital + (price - entry_price) * (current_trade.shares if current_trade else 0)

            equity_curve.append({
                'date': date,
                'equity': market_value,
                'price': price,
                'signal': row.get('signal', 0)
            })

            # v3: 更新峰值并检测回撤熔断
            if self.use_drawdown_breaker:
                if market_value > peak_capital:
                    peak_capital = market_value
                current_dd = (peak_capital - market_value) / peak_capital if peak_capital > 0 else 0
                if breaker_cooldown_remaining > 0:
                    breaker_cooldown_remaining -= 1
                if current_dd > self.drawdown_limit and breaker_cooldown_remaining == 0:
                    breaker_cooldown_remaining = self.cooldown_bars
                    breaker_triggered_count += 1
                    # 熔断触发时如有持仓，立即清仓
                    if position != 0 and current_trade:
                        exit_price_breaker = price * (1 - self.slippage)
                        current_trade.exit_date = date
                        current_trade.exit_price = exit_price_breaker
                        current_trade.exit_reason = "回撤熔断"
                        gross_pnl = (exit_price_breaker - entry_price) * current_trade.shares
                        commission = current_trade.shares * exit_price_breaker * self.commission_rate
                        net_pnl = gross_pnl - commission
                        current_trade.pnl = net_pnl
                        current_trade.pnl_pct = (exit_price_breaker - entry_price) / entry_price
                        capital += net_pnl
                        trades.append(current_trade)
                        # v3 strategy hook
                        if hasattr(strategy, "on_trade_closed"):
                            try:
                                strategy.on_trade_closed(net_pnl, i)
                            except Exception:
                                pass
                        position = 0
                        entry_price = 0.0
                        current_trade = None
                        trailing_tracker = None  # v4
                        if self.use_atr_stop and hasattr(strategy, "stop_loss_pct"):
                            strategy.stop_loss_pct = original_stop_pct
                    continue

            if position == 0:
                # v3: 熔断冷却期内禁止开新仓
                if self.use_drawdown_breaker and breaker_cooldown_remaining > 0:
                    continue
                # 空仓，检查是否入场
                if strategy.should_enter(df, i):
                    # 买入
                    position = 1
                    entry_price = price * (1 + self.slippage)  # 考虑滑点

                    # 按入场时刻的 ATR 重置策略止损（仅对当前持仓有效，平仓后还原）
                    atr_now = df["ATR"].iloc[i] if "ATR" in df.columns else None
                    if self.use_atr_stop and hasattr(strategy, "stop_loss_pct"):
                        new_stop = self._atr_stop_pct(entry_price, atr_now, original_stop_pct)
                        strategy.stop_loss_pct = new_stop
                    else:
                        new_stop = original_stop_pct

                    # v4: Kelly/ATR 动态仓位
                    alloc = self._calc_position_size(
                        capital, entry_price, atr_now, new_stop, trades
                    )
                    shares = alloc / entry_price

                    # v3: 把入场止损价同步给策略（用于跳空保护和阶梯抬高）
                    if hasattr(strategy, "current_stop_price"):
                        strategy.current_stop_price = entry_price * (1 - new_stop)

                    # v4: 初始化移动止损追踪器
                    trailing_tracker = None
                    if self.use_trailing_stop and self.use_atr_stop:
                        from .risk_management import TrailingStopTracker, BOARD_ATR_MULT, _cap_adjust
                        base_mult = BOARD_ATR_MULT.get(self.board, 2.0) * _cap_adjust(self.market_cap_yi)
                        initial_stop_price = entry_price * (1 - new_stop)
                        trailing_tracker = TrailingStopTracker(
                            entry_price=entry_price,
                            initial_stop=initial_stop_price,
                            base_mult=base_mult,
                            direction="long",
                            enable_tighten=self.trailing_tighten,
                        )

                    current_trade = Trade(
                        entry_date=date,
                        entry_price=entry_price,
                        position=1,
                        shares=shares
                    )

                    # 扣除手续费
                    commission = shares * entry_price * self.commission_rate
                    capital -= commission

            else:
                # v4: 更新移动止损
                if trailing_tracker is not None:
                    hi = float(row["High"]) if "High" in df.columns else price
                    lo = float(row["Low"]) if "Low" in df.columns else price
                    atr_i = float(df["ATR"].iloc[i]) if "ATR" in df.columns else None
                    trailing_tracker.update(hi, lo, atr_i)
                    # 移动止损触发判定
                    if trailing_tracker.is_stopped(price):
                        should_exit, exit_reason = True, "移动止损"
                    else:
                        should_exit, exit_reason = strategy.should_exit(df, i, entry_price, position)
                else:
                    # 持仓，检查是否出场
                    should_exit, exit_reason = strategy.should_exit(df, i, entry_price, position)

                if should_exit:
                    # v3: 跳空保护出场价（按 Open 而非 Close，反映真实成交）
                    if self.use_gap_protection and "Open" in df.columns and "止损" in exit_reason:
                        gap_price = float(row["Open"])
                        # 跳空时选 Open（更悲观），盘中触及时选当前 Close
                        if "跳空" in exit_reason:
                            exit_price = gap_price * (1 - self.slippage)
                        else:
                            exit_price = price * (1 - self.slippage)
                    else:
                        exit_price = price * (1 - self.slippage)  # 考虑滑点

                    current_trade.exit_date = date
                    current_trade.exit_price = exit_price
                    current_trade.exit_reason = exit_reason

                    # 计算盈亏
                    gross_pnl = (exit_price - entry_price) * current_trade.shares
                    commission = current_trade.shares * exit_price * self.commission_rate
                    net_pnl = gross_pnl - commission

                    current_trade.pnl = net_pnl
                    current_trade.pnl_pct = (exit_price - entry_price) / entry_price

                    capital += net_pnl
                    trades.append(current_trade)

                    # v3: 通知策略本次交易已平仓（用于连亏冷却统计）
                    if hasattr(strategy, "on_trade_closed"):
                        try:
                            strategy.on_trade_closed(net_pnl, i)
                        except Exception:
                            pass

                    position = 0
                    entry_price = 0.0
                    current_trade = None
                    trailing_tracker = None  # v4: 清理移动止损

                    # 还原策略原始止损百分比，下一笔重新按入场时刻 ATR 计算
                    if self.use_atr_stop and hasattr(strategy, "stop_loss_pct"):
                        strategy.stop_loss_pct = original_stop_pct

        # 如果最后还有持仓，强制平仓
        if position != 0 and current_trade:
            last_price = df['Close'].iloc[-1]
            current_trade.exit_date = df.index[-1]
            current_trade.exit_price = last_price
            current_trade.exit_reason = "回测结束"

            gross_pnl = (last_price - entry_price) * current_trade.shares
            commission = current_trade.shares * last_price * self.commission_rate
            net_pnl = gross_pnl - commission

            current_trade.pnl = net_pnl
            current_trade.pnl_pct = (last_price - entry_price) / entry_price

            capital += net_pnl
            trades.append(current_trade)

        # 计算回测指标
        return self._calculate_metrics(
            strategy, ticker, period, trades, equity_curve, df
        )

    def _calculate_metrics(self, strategy: Strategy, ticker: str, period: str,
                          trades: List[Trade], equity_curve: List[Dict],
                          df: pd.DataFrame) -> BacktestResult:
        """计算回测指标"""

        equity_df = pd.DataFrame(equity_curve).set_index('date')

        # 基本统计
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.pnl > 0)
        losing_trades = total_trades - winning_trades
        win_rate = winning_trades / total_trades if total_trades > 0 else 0

        # 盈亏统计
        profits = [t.pnl for t in trades if t.pnl > 0]
        losses = [t.pnl for t in trades if t.pnl < 0]

        avg_profit = np.mean(profits) if profits else 0
        avg_loss = np.mean(losses) if losses else 0

        # 盈亏比
        profit_factor = abs(sum(profits) / sum(losses)) if losses and sum(losses) != 0 else float('inf')

        # 最大回撤
        equity_series = equity_df['equity']
        cummax = equity_series.cummax()
        drawdown = equity_series - cummax
        max_drawdown = drawdown.min()
        max_drawdown_pct = max_drawdown / cummax[drawdown.idxmin()] if max_drawdown != 0 else 0

        # 夏普比率（简化计算，假设无风险利率为0）
        returns = equity_series.pct_change().dropna()
        sharpe_ratio = np.sqrt(252) * returns.mean() / returns.std() if len(returns) > 1 and returns.std() != 0 else 0

        # 总收益率
        final_capital = equity_series.iloc[-1]
        total_return = (final_capital - self.initial_capital) / self.initial_capital

        return BacktestResult(
            strategy_name=strategy.name,
            ticker=ticker,
            period=period,
            initial_capital=self.initial_capital,
            final_capital=final_capital,
            total_return=total_return,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            avg_profit=avg_profit,
            avg_loss=avg_loss,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            max_drawdown_pct=max_drawdown_pct,
            sharpe_ratio=sharpe_ratio,
            trades=trades,
            equity_curve=equity_df,
            signals=df
        )


# 便捷函数
class MACDCrossStrategy(Strategy):
    """MACD金叉死叉策略"""

    def __init__(self, stop_loss_pct: float = 0.05, take_profit_pct: float = 0.12,
                 macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9):
        super().__init__("MACD金叉死叉")
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        # 用自定义参数重算 MACD（覆盖已有列）
        from .technical import calc_macd
        df = calc_macd(df, fast=self.macd_fast, slow=self.macd_slow, signal=self.macd_signal)

        df['signal'] = SignalType.HOLD.value
        # 金叉：MACD上穿信号线
        macd_cross_up = (df['MACD'] > df['MACD_signal']) & (df['MACD'].shift(1) <= df['MACD_signal'].shift(1))
        macd_cross_down = (df['MACD'] < df['MACD_signal']) & (df['MACD'].shift(1) >= df['MACD_signal'].shift(1))
        df.loc[macd_cross_up, 'signal'] = SignalType.BUY.value
        df.loc[macd_cross_down, 'signal'] = SignalType.SELL.value
        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        if idx < 1 or 'MACD' not in df.columns:
            return False
        return (df['MACD'].iloc[idx] > df['MACD_signal'].iloc[idx] and
                df['MACD'].iloc[idx-1] <= df['MACD_signal'].iloc[idx-1])

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        current = df['Close'].iloc[idx]
        pnl_pct = (current - entry_price) / entry_price if position == 1 else (entry_price - current) / entry_price
        if pnl_pct <= -self.stop_loss_pct:
            return True, "止损"
        if pnl_pct >= self.take_profit_pct:
            return True, "止盈"
        if 'MACD' in df.columns and idx >= 1:
            if (df['MACD'].iloc[idx] < df['MACD_signal'].iloc[idx] and
                    df['MACD'].iloc[idx-1] >= df['MACD_signal'].iloc[idx-1]):
                return True, "MACD死叉"
        return False, ""


class RSIReversalStrategy(Strategy):
    """RSI反转策略"""

    def __init__(self, oversold: float = 30, overbought: float = 70, stop_loss_pct: float = 0.05):
        super().__init__("RSI反转")
        self.oversold = oversold
        self.overbought = overbought
        self.stop_loss_pct = stop_loss_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if 'RSI' not in df.columns:
            from .technical import calc_rsi
            df = calc_rsi(df)
        df['signal'] = SignalType.HOLD.value
        # 买入：RSI从超卖区上穿30
        rsi_cross_up = (df['RSI'] > self.oversold) & (df['RSI'].shift(1) <= self.oversold)
        df.loc[rsi_cross_up, 'signal'] = SignalType.BUY.value
        # 卖出：RSI进入超买区
        df.loc[df['RSI'] > self.overbought, 'signal'] = SignalType.SELL.value
        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        if idx < 1 or 'RSI' not in df.columns:
            return False
        return df['RSI'].iloc[idx] > self.oversold and df['RSI'].iloc[idx-1] <= self.oversold

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        current = df['Close'].iloc[idx]
        pnl_pct = (current - entry_price) / entry_price
        if pnl_pct <= -self.stop_loss_pct:
            return True, "止损"
        if 'RSI' in df.columns and df['RSI'].iloc[idx] > self.overbought:
            return True, "RSI超买"
        return False, ""


class DivergenceStrategy(Strategy):
    """背离交易策略"""

    def __init__(self, stop_loss_pct: float = 0.05, take_profit_pct: float = 0.12):
        super().__init__("背离交易")
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df['signal'] = SignalType.HOLD.value

        try:
            from .divergence_detector import detect_divergences
            div_result = detect_divergences(df, lookback=len(df))
            divs = div_result.get("divergences", [])
            for d in divs:
                end_idx = d.get("end_idx", 0)
                if 0 <= end_idx < len(df):
                    if d["type"] in ("底背离", "隐藏底背离"):
                        df.iloc[end_idx, df.columns.get_loc('signal')] = SignalType.BUY.value
                    elif d["type"] in ("顶背离", "隐藏顶背离"):
                        df.iloc[end_idx, df.columns.get_loc('signal')] = SignalType.SELL.value
        except Exception:
            pass
        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        return df['signal'].iloc[idx] == SignalType.BUY.value if 'signal' in df.columns else False

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        current = df['Close'].iloc[idx]
        pnl_pct = (current - entry_price) / entry_price
        if pnl_pct <= -self.stop_loss_pct:
            return True, "止损"
        if pnl_pct >= self.take_profit_pct:
            return True, "止盈"
        if 'signal' in df.columns and df['signal'].iloc[idx] == SignalType.SELL.value:
            return True, "背离反转"
        return False, ""


class IchimokuStrategy(Strategy):
    """一目均衡表趋势策略：价格突破云带+TK金叉入场"""

    def __init__(self, stop_loss_pct: float = 0.05, take_profit_pct: float = 0.15,
                 **kwargs):
        super().__init__(name="一目均衡表策略", stop_loss_pct=stop_loss_pct,
                         take_profit_pct=take_profit_pct)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"]
        high = df["High"]
        low = df["Low"]

        # 计算Ichimoku
        tenkan = (high.rolling(9, min_periods=1).max() + low.rolling(9, min_periods=1).min()) / 2
        kijun = (high.rolling(26, min_periods=1).max() + low.rolling(26, min_periods=1).min()) / 2
        span_a = ((tenkan + kijun) / 2).shift(26)
        span_b_raw = (high.rolling(52, min_periods=1).max() + low.rolling(52, min_periods=1).min()) / 2
        span_b = span_b_raw.shift(26)

        cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
        cloud_bottom = pd.concat([span_a, span_b], axis=1).min(axis=1)

        signal = pd.Series(0, index=df.index)
        for i in range(27, len(df)):
            above_cloud = close.iloc[i] > cloud_top.iloc[i]
            tk_bull = tenkan.iloc[i] > kijun.iloc[i]
            prev_below = close.iloc[i - 1] <= cloud_top.iloc[i - 1]

            below_cloud = close.iloc[i] < cloud_bottom.iloc[i]
            tk_bear = tenkan.iloc[i] < kijun.iloc[i]

            if above_cloud and tk_bull and prev_below:
                signal.iloc[i] = SignalType.BUY.value
            elif below_cloud and tk_bear:
                signal.iloc[i] = SignalType.SELL.value

        df["signal"] = signal
        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        return df['signal'].iloc[idx] == SignalType.BUY.value if 'signal' in df.columns else False

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        current = df['Close'].iloc[idx]
        pnl_pct = (current - entry_price) / entry_price
        if pnl_pct <= -self.stop_loss_pct:
            return True, "止损"
        if pnl_pct >= self.take_profit_pct:
            return True, "止盈"
        if 'signal' in df.columns and df['signal'].iloc[idx] == SignalType.SELL.value:
            return True, "云带跌破"
        return False, ""


class TDSequentialStrategy(Strategy):
    """TD序列反转策略：TD9完成时反向入场"""

    def __init__(self, stop_loss_pct: float = 0.04, take_profit_pct: float = 0.10,
                 **kwargs):
        super().__init__(name="TD序列策略", stop_loss_pct=stop_loss_pct,
                         take_profit_pct=take_profit_pct)

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["Close"].values
        n = len(df)
        signal = np.zeros(n, dtype=int)

        buy_count = 0
        sell_count = 0
        for i in range(4, n):
            if close[i] < close[i - 4]:
                buy_count += 1
                sell_count = 0
            elif close[i] > close[i - 4]:
                sell_count += 1
                buy_count = 0
            else:
                buy_count = 0
                sell_count = 0

            if buy_count == 9:
                signal[i] = SignalType.BUY.value  # 卖方衰竭→买入
                buy_count = 0
            elif sell_count == 9:
                signal[i] = SignalType.SELL.value  # 买方衰竭→卖出
                sell_count = 0

        df["signal"] = signal
        return df

    def should_enter(self, df: pd.DataFrame, idx: int) -> bool:
        return df['signal'].iloc[idx] == SignalType.BUY.value if 'signal' in df.columns else False

    def should_exit(self, df: pd.DataFrame, idx: int, entry_price: float, position: int) -> tuple:
        current = df['Close'].iloc[idx]
        pnl_pct = (current - entry_price) / entry_price
        if pnl_pct <= -self.stop_loss_pct:
            return True, "止损"
        if pnl_pct >= self.take_profit_pct:
            return True, "止盈"
        if 'signal' in df.columns and df['signal'].iloc[idx] == SignalType.SELL.value:
            return True, "TD卖出信号"
        return False, ""


def run_backtest(df: pd.DataFrame,
                 strategy_type: str = "ema",
                 ticker: str = "",
                 period: str = "",
                 initial_capital: float = 100000.0,
                 use_atr_stop: bool = True,
                 board: Optional[str] = None,
                 market_cap_yi: Optional[float] = None,
                 **strategy_params) -> BacktestResult:
    """
    便捷回测函数

    Args:
        df: 包含OHLCV和指标数据的DataFrame
        strategy_type: "ema", "bollinger", "kdj"
        ticker: 标的代码
        period: 时间周期
        initial_capital: 初始资金
        use_atr_stop: True 时按 ATR 动态计算止损（推荐），False 沿用固定百分比
        board: 板块（主板/创业板/科创板/北交所/ST/其他）；缺省时按 ticker 推断
        market_cap_yi: 流通市值（亿）
        **strategy_params: 策略参数
    """
    # 创建策略
    if strategy_type.lower() == "ema":
        strategy = EMAStrategy(**strategy_params)
    elif strategy_type.lower() == "bollinger":
        strategy = BollingerStrategy(**strategy_params)
    elif strategy_type.lower() == "kdj":
        strategy = KDJStrategy(**strategy_params)
    elif strategy_type.lower() == "macd":
        strategy = MACDCrossStrategy(**strategy_params)
    elif strategy_type.lower() == "rsi":
        strategy = RSIReversalStrategy(**strategy_params)
    elif strategy_type.lower() == "divergence":
        strategy = DivergenceStrategy(**strategy_params)
    elif strategy_type.lower() == "ichimoku":
        strategy = IchimokuStrategy(**strategy_params)
    elif strategy_type.lower() == "td_sequential":
        strategy = TDSequentialStrategy(**strategy_params)
    else:
        raise ValueError(f"未知策略类型: {strategy_type}")

    # 板块自动推断
    if board is None and ticker:
        try:
            from .stock_search import get_board
            board = get_board(ticker)
        except Exception:
            board = "其他"

    # 运行回测
    engine = BacktestEngine(
        initial_capital=initial_capital,
        use_atr_stop=use_atr_stop,
        board=board or "其他",
        market_cap_yi=market_cap_yi,
    )
    return engine.run(df, strategy, ticker, period)


def compare_strategies(df: pd.DataFrame, ticker: str = "", period: str = "",
                       use_atr_stop: bool = True,
                       market_cap_yi: Optional[float] = None) -> pd.DataFrame:
    """对比多个策略的表现"""
    strategies = [
        ("EMA趋势", EMAStrategy()),
        ("布林带回归", BollingerStrategy()),
        ("KDJ超卖", KDJStrategy()),
        ("MACD金叉", MACDCrossStrategy()),
        ("RSI反转", RSIReversalStrategy()),
        ("背离交易", DivergenceStrategy()),
        ("一目均衡表", IchimokuStrategy()),
        ("TD序列", TDSequentialStrategy()),
    ]

    board = "其他"
    if ticker:
        try:
            from .stock_search import get_board
            board = get_board(ticker)
        except Exception:
            pass

    results = []
    engine = BacktestEngine(use_atr_stop=use_atr_stop, board=board, market_cap_yi=market_cap_yi)

    for name, strategy in strategies:
        result = engine.run(df.copy(), strategy, ticker, period)
        results.append({
            '策略': name,
            '总收益率': f"{result.total_return*100:.2f}%",
            '交易次数': result.total_trades,
            '胜率': f"{result.win_rate*100:.2f}%",
            '最大回撤': f"{result.max_drawdown_pct*100:.2f}%",
            '夏普比率': f"{result.sharpe_ratio:.2f}",
            '盈亏比': f"{result.profit_factor:.2f}",
        })

    return pd.DataFrame(results)
