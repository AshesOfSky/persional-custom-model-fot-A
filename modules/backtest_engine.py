"""
量化策略回测引擎

功能:
- 多因子策略回测（价值、动量、质量、低波动）
- 事件驱动回测（财报发布、宏观事件）
- EMA趋势策略联动
- 风险指标计算（夏普、最大回撤、Calmar等）
- 绩效归因分析（Brinson模型）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


class Action(Enum):
    """交易动作"""
    BUY = "买入"
    SELL = "卖出"
    HOLD = "持有"


@dataclass
class Signal:
    """交易信号"""
    ticker: str
    action: Action
    reason: str
    weight: float = 1.0
    target_price: Optional[float] = None


@dataclass
class Trade:
    """交易记录"""
    date: datetime
    ticker: str
    action: Action
    shares: int
    price: float
    cost: float = 0.0
    proceeds: float = 0.0


@dataclass
class Position:
    """持仓"""
    ticker: str
    shares: int
    avg_cost: float
    entry_date: datetime
    market_value: float = 0.0
    unrealized_pnl: float = 0.0

    def add(self, shares: int, price: float):
        """加仓"""
        total_cost = self.avg_cost * self.shares + price * shares
        self.shares += shares
        self.avg_cost = total_cost / self.shares if self.shares > 0 else 0

    def reduce(self, shares: int):
        """减仓"""
        self.shares = max(0, self.shares - shares)
        if self.shares == 0:
            self.avg_cost = 0

    def update_price(self, price: float):
        """更新价格"""
        self.market_value = self.shares * price
        self.unrealized_pnl = self.market_value - self.avg_cost * self.shares


@dataclass
class RiskMetrics:
    """风险指标"""
    volatility: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    var_95: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    information_ratio: Optional[float] = None


@dataclass
class AttributionReport:
    """归因报告"""
    total_excess: float = 0.0
    allocation_effect: float = 0.0
    selection_effect: float = 0.0
    interaction_effect: float = 0.0


@dataclass
class BacktestResult:
    """回测结果"""
    trades: List[Trade] = field(default_factory=list)
    equity_curve: Optional[pd.DataFrame] = None
    daily_returns: Optional[pd.Series] = None

    def __post_init__(self):
        if self.equity_curve is None:
            self.equity_curve = pd.DataFrame()
        if self.daily_returns is None:
            self.daily_returns = pd.Series()

    # 收益指标
    total_return: float = 0.0
    annualized_return: float = 0.0
    benchmark_return: float = 0.0
    excess_return: float = 0.0

    # 风险指标
    risk_metrics: RiskMetrics = field(default_factory=RiskMetrics)

    # 交易统计
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0

    # 归因分析
    attribution: AttributionReport = field(default_factory=AttributionReport)

    def plot_equity_curve(self):
        """绘制权益曲线"""
        try:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(12, 6))
            ax.plot(self.equity_curve['date'], self.equity_curve['total_value'])
            ax.set_xlabel('Date')
            ax.set_ylabel('Portfolio Value')
            ax.set_title('Equity Curve')
            return fig
        except ImportError:
            logger.warning("matplotlib not installed")
            return None

    def export_report(self, filepath: str):
        """导出报告"""
        # 简化实现
        pass


class Portfolio:
    """投资组合"""

    def __init__(self, initial_capital: float, commission_rate: float = 0.0003):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate
        self.positions: Dict[str, Position] = {}
        self.equity_curve: List[Dict] = []

    def buy(self, ticker: str, shares: int, price: float, date: datetime) -> Trade:
        """买入"""
        cost = shares * price * (1 + self.commission_rate)

        if cost > self.cash:
            raise ValueError(f"Insufficient funds: need {cost}, have {self.cash}")

        self.cash -= cost

        if ticker in self.positions:
            self.positions[ticker].add(shares, price)
        else:
            self.positions[ticker] = Position(ticker, shares, price, date)

        return Trade(
            date=date, ticker=ticker, action=Action.BUY,
            shares=shares, price=price, cost=cost
        )

    def sell(self, ticker: str, shares: int, price: float, date: datetime) -> Trade:
        """卖出"""
        if ticker not in self.positions:
            raise ValueError(f"Position not found: {ticker}")

        position = self.positions[ticker]

        if shares > position.shares:
            shares = position.shares

        proceeds = shares * price * (1 - self.commission_rate)
        self.cash += proceeds

        position.reduce(shares)
        if position.shares == 0:
            del self.positions[ticker]

        return Trade(
            date=date, ticker=ticker, action=Action.SELL,
            shares=shares, price=price, proceeds=proceeds
        )

    def update_market_value(self, prices: Dict[str, float], date: datetime):
        """更新市值"""
        total_value = self.cash

        for ticker, position in self.positions.items():
            if ticker in prices:
                position.update_price(prices[ticker])
                total_value += position.market_value

        self.equity_curve.append({
            'date': date,
            'total_value': total_value,
            'cash': self.cash,
            'positions_value': total_value - self.cash,
        })

    def get_total_value(self) -> float:
        """获取总资产"""
        return sum(p.market_value for p in self.positions.values()) + self.cash


class RiskAnalyzer:
    """风险分析器"""

    def calculate_all_metrics(
        self,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series] = None,
        risk_free_rate: float = 0.03,
    ) -> RiskMetrics:
        """计算所有风险指标"""
        return RiskMetrics(
            volatility=self.calculate_volatility(returns),
            max_drawdown=self.calculate_max_drawdown(returns)['max_drawdown'],
            sharpe_ratio=self.calculate_sharpe(returns, risk_free_rate),
            sortino_ratio=self.calculate_sortino(returns, risk_free_rate),
            calmar_ratio=self.calculate_calmar(returns),
            var_95=self.calculate_var(returns, 0.95),
            information_ratio=self.calculate_information_ratio(
                returns, benchmark_returns
            ) if benchmark_returns is not None else None,
        )

    def calculate_volatility(self, returns: pd.Series) -> float:
        """计算波动率"""
        return returns.std() * np.sqrt(252)

    def calculate_max_drawdown(self, returns: pd.Series) -> Dict:
        """计算最大回撤"""
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        max_drawdown = drawdown.min()
        max_dd_end = drawdown.idxmin()
        peak_before = cumulative.loc[:max_dd_end].idxmax()

        return {
            'max_drawdown': max_drawdown,
            'start_date': peak_before,
            'end_date': max_dd_end,
            'duration': (max_dd_end - peak_before).days,
        }

    def calculate_sharpe(self, returns: pd.Series, risk_free_rate: float = 0.03) -> float:
        """夏普比率"""
        excess_returns = returns - risk_free_rate / 252
        if returns.std() == 0:
            return 0
        return excess_returns.mean() / returns.std() * np.sqrt(252)

    def calculate_sortino(self, returns: pd.Series, risk_free_rate: float = 0.03) -> float:
        """索提诺比率"""
        excess_returns = returns - risk_free_rate / 252
        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std() if len(downside_returns) > 0 else 0
        if downside_std == 0:
            return 0
        return excess_returns.mean() / downside_std * np.sqrt(252)

    def calculate_calmar(self, returns: pd.Series) -> float:
        """Calmar比率"""
        annual_return = returns.mean() * 252
        max_dd = abs(self.calculate_max_drawdown(returns)['max_drawdown'])
        if max_dd == 0:
            return 0
        return annual_return / max_dd

    def calculate_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        """Value at Risk"""
        from scipy.stats import norm
        z_score = norm.ppf(1 - confidence)
        return returns.mean() + z_score * returns.std()

    def calculate_information_ratio(
        self, returns: pd.Series, benchmark_returns: pd.Series
    ) -> float:
        """信息比率"""
        excess_returns = returns - benchmark_returns
        tracking_error = excess_returns.std() * np.sqrt(252)
        if tracking_error == 0:
            return 0
        return excess_returns.mean() * 252 / tracking_error


class AttributionAnalyzer:
    """绩效归因分析器"""

    def brinson_attribution(
        self,
        portfolio_returns: pd.DataFrame,
        benchmark_returns: pd.DataFrame,
        portfolio_weights: pd.DataFrame,
        benchmark_weights: pd.DataFrame,
    ) -> AttributionReport:
        """
        Brinson归因模型
        将超额收益分解为:
        1. 资产配置效应
        2. 证券选择效应
        3. 交互效应
        """
        # 组合收益
        portfolio_return = (portfolio_weights * portfolio_returns).sum().sum()

        # 基准收益
        benchmark_return = (benchmark_weights * benchmark_returns).sum().sum()

        # 超额收益
        excess_return = portfolio_return - benchmark_return

        # 资产配置效应
        allocation_effect = (
            (portfolio_weights - benchmark_weights) * benchmark_returns
        ).sum().sum()

        # 证券选择效应
        selection_effect = (
            benchmark_weights * (portfolio_returns - benchmark_returns)
        ).sum().sum()

        # 交互效应
        interaction_effect = (
            (portfolio_weights - benchmark_weights) *
            (portfolio_returns - benchmark_returns)
        ).sum().sum()

        return AttributionReport(
            total_excess=excess_return,
            allocation_effect=allocation_effect,
            selection_effect=selection_effect,
            interaction_effect=interaction_effect,
        )


class Strategy(ABC):
    """策略基类"""

    @abstractmethod
    def generate_signals(
        self,
        date: datetime,
        data: pd.DataFrame,
        portfolio: Portfolio,
    ) -> List[Signal]:
        """生成交易信号"""
        pass

    @abstractmethod
    def get_parameters(self) -> Dict[str, Any]:
        """获取策略参数"""
        pass


class Factor(ABC):
    """因子基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def calculate(self, date: datetime, data: pd.DataFrame) -> pd.Series:
        """计算因子值"""
        pass

    def _normalize(self, series: pd.Series) -> pd.Series:
        """标准化（Z-Score）"""
        return (series - series.mean()) / series.std()


class ValueFactor(Factor):
    """价值因子"""

    name = "value"

    def __init__(self, metrics: List[str] = None):
        self.metrics = metrics or ["pe", "pb"]

    def calculate(self, date: datetime, data: pd.DataFrame) -> pd.Series:
        """价值因子: PE/PB越低越好"""
        scores = pd.Series(0, index=data.index)

        for metric in self.metrics:
            if metric in data.columns:
                values = 1 / data[metric].replace([np.inf, -np.inf], np.nan)
                scores += self._normalize(values)

        return scores / len(self.metrics)


class MomentumFactor(Factor):
    """动量因子"""

    name = "momentum"

    def __init__(self, short_window: int = 20, long_window: int = 120):
        self.short_window = short_window
        self.long_window = long_window

    def calculate(self, date: datetime, data: pd.DataFrame) -> pd.Series:
        """动量因子: 短期+长期动量"""
        short_col = f'return_{self.short_window}d'
        long_col = f'return_{self.long_window}d'

        short_return = data.get(short_col, pd.Series(0, index=data.index))
        long_return = data.get(long_col, pd.Series(0, index=data.index))

        momentum = short_return * 0.7 + long_return * 0.3
        return self._normalize(momentum)


class QualityFactor(Factor):
    """质量因子"""

    name = "quality"

    def calculate(self, date: datetime, data: pd.DataFrame) -> pd.Series:
        """质量因子: ROE + ROA + 盈利稳定性"""
        roe_score = self._normalize(data.get('roe', pd.Series(0, index=data.index)))
        roa_score = self._normalize(data.get('roa', pd.Series(0, index=data.index)))

        # 盈利稳定性（ROE标准差的倒数）
        roe_std = data.get('roe_std', pd.Series(1, index=data.index))
        stability = 1 / roe_std.replace([np.inf], np.nan)
        stability_score = self._normalize(stability)

        return (roe_score + roa_score + stability_score) / 3


class LowVolatilityFactor(Factor):
    """低波动因子"""

    name = "low_volatility"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, date: datetime, data: pd.DataFrame) -> pd.Series:
        """低波动因子: 历史波动率越低越好"""
        vol_col = f'volatility_{self.window}d'
        volatility = data.get(vol_col, pd.Series(1, index=data.index))

        # 倒数（越低越好 -> 越高越好）
        return self._normalize(1 / volatility)


class MultiFactorStrategy(Strategy):
    """多因子策略"""

    def __init__(
        self,
        factors: List[Factor],
        factor_weights: Dict[str, float],
        rebalance_freq: str = "monthly",
        top_n: int = 20,
    ):
        self.factors = factors
        self.factor_weights = factor_weights
        self.rebalance_freq = rebalance_freq
        self.top_n = top_n
        self._last_rebalance = None

    def _should_rebalance(self, date: datetime) -> bool:
        """检查是否应该再平衡"""
        if self._last_rebalance is None:
            return True

        if self.rebalance_freq == "daily":
            return True
        elif self.rebalance_freq == "weekly":
            return date.weekday() == 0 and date != self._last_rebalance
        elif self.rebalance_freq == "monthly":
            return date.day == 1 and date != self._last_rebalance

        return False

    def generate_signals(
        self,
        date: datetime,
        data: pd.DataFrame,
        portfolio: Portfolio,
    ) -> List[Signal]:
        """生成交易信号"""
        if not self._should_rebalance(date):
            return []

        # 计算因子得分
        factor_scores = {}
        for factor in self.factors:
            score = factor.calculate(date, data)
            factor_scores[factor.name] = score

        # 加权合成
        total_score = pd.Series(0, index=data.index)
        for name, weight in self.factor_weights.items():
            if name in factor_scores:
                total_score += factor_scores[name] * weight

        # 选出Top N
        selected = total_score.nlargest(self.top_n).index.tolist()

        # 生成调仓信号
        signals = []
        current_positions = set(portfolio.positions.keys())
        target_positions = set(selected)

        # 卖出不在目标中的持仓
        for ticker in current_positions - target_positions:
            signals.append(Signal(
                ticker=ticker,
                action=Action.SELL,
                reason="Rebalance: Not in top selection",
            ))

        # 买入目标中的新股票
        for ticker in target_positions - current_positions:
            signals.append(Signal(
                ticker=ticker,
                action=Action.BUY,
                reason=f"Rebalance: Top {self.top_n} by factor score",
            ))

        self._last_rebalance = date
        return signals

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "factors": [f.name for f in self.factors],
            "factor_weights": self.factor_weights,
            "rebalance_freq": self.rebalance_freq,
            "top_n": self.top_n,
        }


class EMAStrategy(Strategy):
    """EMA趋势策略"""

    def __init__(
        self,
        fast_period: int = 8,
        slow_period: int = 21,
        use_tunnel: bool = True,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.use_tunnel = use_tunnel

    def generate_signals(
        self,
        date: datetime,
        data: pd.DataFrame,
        portfolio: Portfolio,
    ) -> List[Signal]:
        """EMA策略: 金叉买入，死叉卖出"""
        signals = []

        for ticker in data.index:
            if 'close' not in data.columns:
                continue

            # 获取价格历史
            hist_data = data.loc[ticker, 'close'] if ticker in data.index else None
            if hist_data is None or not isinstance(hist_data, (pd.Series, list)):
                continue

            if isinstance(hist_data, pd.Series):
                prices = hist_data.values
            else:
                prices = np.array(hist_data)

            if len(prices) < max(self.fast_period, self.slow_period) + 5:
                continue

            # 计算EMA
            fast_ema = pd.Series(prices).ewm(span=self.fast_period).mean()
            slow_ema = pd.Series(prices).ewm(span=self.slow_period).mean()

            # 检测交叉
            prev_fast = fast_ema.iloc[-2]
            prev_slow = slow_ema.iloc[-2]
            curr_fast = fast_ema.iloc[-1]
            curr_slow = slow_ema.iloc[-1]

            if prev_fast <= prev_slow and curr_fast > curr_slow:
                signal_type = "golden_cross"
            elif prev_fast >= prev_slow and curr_fast < curr_slow:
                signal_type = "death_cross"
            else:
                signal_type = "hold"

            # 生成交易信号
            if signal_type == "golden_cross" and ticker not in portfolio.positions:
                signals.append(Signal(ticker, Action.BUY, "EMA Golden Cross"))
            elif signal_type == "death_cross" and ticker in portfolio.positions:
                signals.append(Signal(ticker, Action.SELL, "EMA Death Cross"))

        return signals

    def get_parameters(self) -> Dict[str, Any]:
        return {
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "use_tunnel": self.use_tunnel,
        }


class BacktestEngine:
    """回测引擎主类"""

    def __init__(
        self,
        initial_capital: float = 1000000,
        commission_rate: float = 0.0003,
        slippage: float = 0.001,
    ):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.portfolio: Optional[Portfolio] = None
        self.trade_history: List[Trade] = []
        self.risk_analyzer = RiskAnalyzer()

    def run(
        self,
        strategy: Strategy,
        stock_pool: List[str],
        start_date: datetime,
        end_date: datetime,
        price_data: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        """运行回测"""
        self.portfolio = Portfolio(self.initial_capital, self.commission_rate)
        self.trade_history = []

        # 生成日期范围
        dates = pd.date_range(start=start_date, end=end_date, freq='B')

        for date in dates:
            # 模拟数据获取
            if price_data is not None:
                date_data = price_data[price_data.index == date]
                if date_data.empty:
                    continue
            else:
                # 生成模拟数据
                date_data = self._generate_mock_data(stock_pool, date)

            # 生成交易信号
            signals = strategy.generate_signals(date, date_data, self.portfolio)

            # 执行交易
            for signal in signals:
                try:
                    trade = self._execute_signal(signal, date, date_data)
                    if trade:
                        self.trade_history.append(trade)
                except Exception as e:
                    logger.warning(f"Trade execution failed: {e}")

            # 更新组合市值
            prices = self._get_prices(date_data)
            self.portfolio.update_market_value(prices, date)

        # 计算结果
        return self._calculate_result()

    def _generate_mock_data(self, stock_pool: List[str], date: datetime) -> pd.DataFrame:
        """生成模拟数据"""
        np.random.seed(42)
        data = {
            'close': np.random.uniform(10, 200, len(stock_pool)),
            'volume': np.random.randint(100000, 10000000, len(stock_pool)),
            'pe': np.random.uniform(5, 50, len(stock_pool)),
            'pb': np.random.uniform(0.5, 10, len(stock_pool)),
            'roe': np.random.uniform(0.05, 0.30, len(stock_pool)),
        }
        return pd.DataFrame(data, index=stock_pool)

    def _execute_signal(
        self,
        signal: Signal,
        date: datetime,
        data: pd.DataFrame,
    ) -> Optional[Trade]:
        """执行交易信号"""
        if signal.ticker not in data.index:
            return None

        price = data.loc[signal.ticker, 'close']
        if pd.isna(price):
            return None

        # 应用滑点
        if signal.action == Action.BUY:
            execution_price = price * (1 + self.slippage)
        else:
            execution_price = price * (1 - self.slippage)

        # 计算交易数量（简化：固定金额）
        trade_value = self.initial_capital * 0.1  # 每笔10%仓位
        shares = int(trade_value / execution_price)

        if shares == 0:
            return None

        try:
            if signal.action == Action.BUY:
                return self.portfolio.buy(signal.ticker, shares, execution_price, date)
            elif signal.action == Action.SELL:
                return self.portfolio.sell(signal.ticker, shares, execution_price, date)
        except ValueError as e:
            logger.warning(f"Trade failed: {e}")
            return None

        return None

    def _get_prices(self, data: pd.DataFrame) -> Dict[str, float]:
        """获取价格字典"""
        if 'close' in data.columns:
            return data['close'].to_dict()
        return {}

    def _calculate_result(self) -> BacktestResult:
        """计算回测结果"""
        # 权益曲线
        equity_df = pd.DataFrame(self.portfolio.equity_curve)
        if equity_df.empty:
            return BacktestResult()

        equity_df.set_index('date', inplace=True)

        # 日收益率
        daily_returns = equity_df['total_value'].pct_change().dropna()

        # 总收益
        total_return = (equity_df['total_value'].iloc[-1] / self.initial_capital) - 1

        # 年化收益
        n_years = len(equity_df) / 252
        annualized_return = (1 + total_return) ** (1 / n_years) - 1 if n_years > 0 else 0

        # 风险指标
        risk_metrics = self.risk_analyzer.calculate_all_metrics(daily_returns)

        # 交易统计
        wins = [t for t in self.trade_history if t.action == Action.SELL and t.proceeds > t.cost]
        losses = [t for t in self.trade_history if t.action == Action.SELL and t.proceeds <= t.cost]

        total_trades = len([t for t in self.trade_history if t.action == Action.SELL])
        win_rate = len(wins) / total_trades if total_trades > 0 else 0

        avg_win = np.mean([t.proceeds - t.cost for t in wins]) if wins else 0
        avg_loss = np.mean([t.cost - t.proceeds for t in losses]) if losses else 0

        profit_factor = sum(t.proceeds for t in wins) / sum(t.cost for t in losses) if losses and sum(t.cost for t in losses) > 0 else 0

        return BacktestResult(
            trades=self.trade_history,
            equity_curve=equity_df.reset_index(),
            daily_returns=daily_returns,
            total_return=total_return,
            annualized_return=annualized_return,
            risk_metrics=risk_metrics,
            total_trades=total_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )


# 便捷函数
def run_multifactor_backtest(
    stock_pool: List[str],
    start_date: datetime,
    end_date: datetime,
    factor_weights: Dict[str, float] = None,
) -> BacktestResult:
    """运行多因子回测"""
    factors = [
        ValueFactor(),
        MomentumFactor(),
        QualityFactor(),
    ]

    weights = factor_weights or {
        "value": 0.4,
        "momentum": 0.4,
        "quality": 0.2,
    }

    strategy = MultiFactorStrategy(
        factors=factors,
        factor_weights=weights,
        rebalance_freq="monthly",
        top_n=20,
    )

    engine = BacktestEngine()
    return engine.run(strategy, stock_pool, start_date, end_date)


# Streamlit集成
def render_backtest_page():
    """Streamlit页面渲染"""
    try:
        import streamlit as st
    except ImportError:
        return

    st.header("策略回测")

    # 策略选择
    strategy_type = st.selectbox("策略类型", ["多因子", "EMA趋势"])

    if strategy_type == "多因子":
        col1, col2 = st.columns(2)
        with col1:
            value_weight = st.slider("价值因子权重", 0.0, 1.0, 0.4)
            momentum_weight = st.slider("动量因子权重", 0.0, 1.0, 0.4)
        with col2:
            quality_weight = st.slider("质量因子权重", 0.0, 1.0, 0.2)
            top_n = st.slider("选股数量", 5, 50, 20)

    # 回测参数
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("开始日期", datetime(2020, 1, 1))
        initial_capital = st.number_input("初始资金", 100000, 10000000, 1000000)
    with col2:
        end_date = st.date_input("结束日期", datetime(2024, 12, 31))
        commission = st.slider("手续费率", 0.0001, 0.005, 0.0003, 0.0001)

    if st.button("运行回测", type="primary"):
        with st.spinner("回测进行中..."):
            # 模拟股票池
            stock_pool = [f"STOCK{i:04d}" for i in range(50)]

            if strategy_type == "多因子":
                factors = [ValueFactor(), MomentumFactor(), QualityFactor()]
                weights = {
                    "value": value_weight,
                    "momentum": momentum_weight,
                    "quality": quality_weight,
                }
                strategy = MultiFactorStrategy(factors, weights, "monthly", top_n)
            else:
                strategy = EMAStrategy()

            engine = BacktestEngine(initial_capital, commission)
            result = engine.run(
                strategy, stock_pool,
                datetime.combine(start_date, datetime.min.time()),
                datetime.combine(end_date, datetime.min.time()),
            )

        # 展示结果
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("总收益率", f"{result.total_return:.2%}")
        col2.metric("夏普比率", f"{result.risk_metrics.sharpe_ratio:.2f}")
        col3.metric("最大回撤", f"{result.risk_metrics.max_drawdown:.2%}")
        col4.metric("胜率", f"{result.win_rate:.1%}")

        # 权益曲线
        if not result.equity_curve.empty:
            st.subheader("权益曲线")
            st.line_chart(result.equity_curve.set_index('date')['total_value'])


if __name__ == "__main__":
    # 测试代码
    stock_pool = [f"STOCK{i:04d}" for i in range(30)]

    result = run_multifactor_backtest(
        stock_pool=stock_pool,
        start_date=datetime(2020, 1, 1),
        end_date=datetime(2024, 12, 31),
    )

    print(f"总收益率: {result.total_return:.2%}")
    print(f"年化收益率: {result.annualized_return:.2%}")
    print(f"夏普比率: {result.risk_metrics.sharpe_ratio:.2f}")
    print(f"最大回撤: {result.risk_metrics.max_drawdown:.2%}")
