# 量化策略回测引擎详细设计

> 优先级: Sprint 3 (P1)
> 模块路径: `modules/backtest_engine.py` (升级现有 `backtest.py`)

---

## 1. 功能概述

回测引擎是验证投资策略有效性的核心组件。本次升级将支持多因子模型、事件驱动回测和更全面的风险指标。

### 1.1 核心能力
- 多因子策略回测（价值、动量、质量、低波动）
- 事件驱动回测（财报发布、宏观事件）
- 与现有EMA信号系统联动
- 全面的风险指标（夏普、最大回撤、Calmar等）
- 绩效归因分析

### 1.2 输入输出

**输入:**
- 策略配置（因子权重、信号规则、参数）
- 股票池
- 回测时间范围
- 初始资金、交易成本等

**输出:**
- 回测结果（收益率、风险指标）
- 交易记录
- 权益曲线
- 绩效归因报告

---

## 2. 架构设计

### 2.1 类图

```python
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
        self.portfolio = Portfolio(initial_capital)
        self.trade_history = []
        self.daily_stats = []

    def run(
        self,
        strategy: Strategy,
        stock_pool: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> BacktestResult:
        """
        运行回测

        流程:
        1. 加载历史数据
        2. 按日期循环
        3. 生成交易信号
        4. 执行交易
        5. 更新组合净值
        6. 记录统计
        """
        pass

    def _load_data(
        self,
        stock_pool: List[str],
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """加载历史数据"""
        pass

    def _generate_signals(
        self,
        strategy: Strategy,
        date: datetime,
        data: pd.DataFrame,
    ) -> List[Signal]:
        """生成交易信号"""
        pass

    def _execute_trades(
        self,
        signals: List[Signal],
        date: datetime,
    ) -> List[Trade]:
        """执行交易"""
        pass

    def _update_portfolio(self, date: datetime) -> None:
        """更新组合净值"""
        pass


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


class MultiFactorStrategy(Strategy):
    """多因子策略"""

    def __init__(
        self,
        factors: List[Factor],
        factor_weights: Dict[str, float],
        rebalance_freq: str = "monthly",  # daily/weekly/monthly
        top_n: int = 20,  # 选股数量
    ):
        self.factors = factors
        self.factor_weights = factor_weights
        self.rebalance_freq = rebalance_freq
        self.top_n = top_n

    def generate_signals(self, date, data, portfolio) -> List[Signal]:
        """
        多因子选股流程:
        1. 计算各因子得分
        2. 加权合成总得分
        3. 选出Top N股票
        4. 生成调仓信号
        """
        # 计算因子得分
        factor_scores = {}
        for factor in self.factors:
            score = factor.calculate(date, data)
            factor_scores[factor.name] = score

        # 加权合成
        total_score = pd.Series(0, index=data.index)
        for name, weight in self.factor_weights.items():
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

        return signals


class EMAStrategy(Strategy):
    """EMA趋势策略 (与现有系统联动)"""

    def __init__(
        self,
        fast_period: int = 8,
        slow_period: int = 21,
        use_tunnel: bool = True,  # 使用EMA隧道
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.use_tunnel = use_tunnel

    def generate_signals(self, date, data, portfolio) -> List[Signal]:
        """
        EMA策略:
        - 金叉买入 (快速EMA上穿慢速EMA)
        - 死叉卖出 (快速EMA下穿慢速EMA)
        """
        signals = []

        for ticker in data.index:
            hist_data = data.loc[ticker, 'price_history']

            if self.use_tunnel:
                # 使用现有EMA隧道分析
                ema_data = calculate_ema_tunnel(hist_data)
                signal_type = ema_data['signal']
            else:
                # 简单双均线
                fast_ema = hist_data.ewm(span=self.fast_period).mean().iloc[-1]
                slow_ema = hist_data.ewm(span=self.slow_period).mean().iloc[-1]
                prev_fast = hist_data.ewm(span=self.fast_period).mean().iloc[-2]
                prev_slow = hist_data.ewm(span=self.slow_period).mean().iloc[-2]

                if prev_fast <= prev_slow and fast_ema > slow_ema:
                    signal_type = "golden_cross"
                elif prev_fast >= prev_slow and fast_ema < slow_ema:
                    signal_type = "death_cross"
                else:
                    signal_type = "hold"

            # 生成交易信号
            if signal_type == "golden_cross" and ticker not in portfolio.positions:
                signals.append(Signal(ticker, Action.BUY, "EMA Golden Cross"))
            elif signal_type == "death_cross" and ticker in portfolio.positions:
                signals.append(Signal(ticker, Action.SELL, "EMA Death Cross"))

        return signals


class EventDrivenStrategy(Strategy):
    """事件驱动策略"""

    def __init__(
        self,
        event_type: str,  # earnings / macro / corporate_action
        lookback_days: int = 30,
        holding_days: int = 5,
    ):
        self.event_type = event_type
        self.lookback_days = lookback_days
        self.holding_days = holding_days
        self.event_calendar = EventCalendar()

    def generate_signals(self, date, data, portfolio) -> List[Signal]:
        """
        事件驱动策略:
        - 财报发布前N天买入/卖出
        - 持有N天后平仓
        """
        signals = []

        # 获取即将发生的事件
        upcoming_events = self.event_calendar.get_events(
            date,
            date + timedelta(days=self.lookback_days),
        )

        for event in upcoming_events:
            if event.type == self.event_type:
                # 根据事件类型和历史胜率生成信号
                signal = self._analyze_event(event)
                if signal:
                    signals.append(signal)

        # 检查持仓是否达到持有期
        for ticker, position in portfolio.positions.items():
            if (date - position.entry_date).days >= self.holding_days:
                signals.append(Signal(
                    ticker=ticker,
                    action=Action.SELL,
                    reason=f"Time exit: Held for {self.holding_days} days",
                ))

        return signals


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


class ValueFactor(Factor):
    """价值因子"""

    name = "value"

    def __init__(self, metrics: List[str] = None):
        self.metrics = metrics or ["pe", "pb", "ev_ebitda"]

    def calculate(self, date, data) -> pd.Series:
        """
        价值因子:
        - PE (市盈率) 越低越好
        - PB (市净率) 越低越好
        - EV/EBITDA 越低越好
        """
        scores = pd.Series(0, index=data.index)

        for metric in self.metrics:
            if metric in data.columns:
                # 倒数归一化 (越低越好 -> 越高越好)
                values = 1 / data[metric].replace([np.inf, -np.inf], np.nan)
                scores += self._normalize(values)

        return scores / len(self.metrics)


class MomentumFactor(Factor):
    """动量因子"""

    name = "momentum"

    def __init__(
        self,
        short_window: int = 20,
        long_window: int = 120,
    ):
        self.short_window = short_window
        self.long_window = long_window

    def calculate(self, date, data) -> pd.Series:
        """
        动量因子:
        - 短期动量 (20日收益率)
        - 长期动量 (120日收益率)
        - 加速度 (短期 - 长期)
        """
        short_return = data[f'return_{self.short_window}d']
        long_return = data[f'return_{self.long_window}d']

        # 结合短期和长期动量
        momentum = short_return * 0.7 + long_return * 0.3

        return self._normalize(momentum)


class QualityFactor(Factor):
    """质量因子"""

    name = "quality"

    def calculate(self, date, data) -> pd.Series:
        """
        质量因子:
        - ROE (净资产收益率)
        - ROA (总资产收益率)
        - 盈利稳定性 (ROE标准差的倒数)
        """
        roe_score = self._normalize(data['roe'])
        roa_score = self._normalize(data['roa'])

        # 盈利稳定性
        earnings_stability = 1 / data['roe_std'].replace([np.inf], np.nan)
        stability_score = self._normalize(earnings_stability)

        return (roe_score + roa_score + stability_score) / 3


class LowVolatilityFactor(Factor):
    """低波动因子"""

    name = "low_volatility"

    def __init__(self, window: int = 60):
        self.window = window

    def calculate(self, date, data) -> pd.Series:
        """
        低波动因子:
        - 历史波动率越低越好
        """
        volatility = data[f'volatility_{self.window}d']

        # 倒数 (越低越好 -> 越高越好)
        return self._normalize(1 / volatility)


class Portfolio:
    """投资组合"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: Dict[str, Position] = {}
        self.equity_curve = []

    def buy(self, ticker: str, shares: int, price: float, date: datetime) -> Trade:
        """买入"""
        cost = shares * price * (1 + self.commission_rate)

        if cost > self.cash:
            raise InsufficientFundsError()

        self.cash -= cost

        if ticker in self.positions:
            self.positions[ticker].add(shares, price)
        else:
            self.positions[ticker] = Position(ticker, shares, price, date)

        return Trade(
            date=date,
            ticker=ticker,
            action=Action.BUY,
            shares=shares,
            price=price,
            cost=cost,
        )

    def sell(self, ticker: str, shares: int, price: float, date: datetime) -> Trade:
        """卖出"""
        if ticker not in self.positions:
            raise PositionNotFoundError()

        position = self.positions[ticker]

        if shares > position.shares:
            shares = position.shares

        proceeds = shares * price * (1 - self.commission_rate)
        self.cash += proceeds

        position.reduce(shares)
        if position.shares == 0:
            del self.positions[ticker]

        return Trade(
            date=date,
            ticker=ticker,
            action=Action.SELL,
            shares=shares,
            price=price,
            proceeds=proceeds,
        )

    def update_market_value(self, prices: Dict[str, float], date: datetime) -> None:
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


class BacktestResult:
    """回测结果"""

    trades: List[Trade]
    equity_curve: pd.DataFrame
    daily_returns: pd.Series

    # 收益指标
    total_return: float
    annualized_return: float
    benchmark_return: float
    excess_return: float

    # 风险指标
    volatility: float
    max_drawdown: float
    max_drawdown_duration: int
    var_95: float  # 95% VaR

    # 风险调整收益
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    information_ratio: float

    # 交易统计
    total_trades: int
    win_rate: float
    profit_factor: float
    avg_win: float
    avg_loss: float

    # 归因分析
    attribution: AttributionReport
```

---

## 3. 核心算法

### 3.1 风险指标计算

```python
class RiskAnalyzer:
    """风险分析器"""

    def calculate_all_metrics(
        self,
        returns: pd.Series,
        benchmark_returns: pd.Series = None,
        risk_free_rate: float = 0.03,
    ) -> RiskMetrics:
        """计算所有风险指标"""
        return RiskMetrics(
            volatility=self.calculate_volatility(returns),
            max_drawdown=self.calculate_max_drawdown(returns),
            sharpe_ratio=self.calculate_sharpe(returns, risk_free_rate),
            sortino_ratio=self.calculate_sortino(returns, risk_free_rate),
            calmar_ratio=self.calculate_calmar(returns),
            var_95=self.calculate_var(returns, 0.95),
            information_ratio=self.calculate_information_ratio(
                returns, benchmark_returns
            ) if benchmark_returns is not None else None,
        )

    def calculate_max_drawdown(self, returns: pd.Series) -> Dict:
        """
        计算最大回撤

        公式:
        DD(t) = (Peak(t) - Current(t)) / Peak(t)
        MDD = max(DD(t))
        """
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.expanding().max()
        drawdown = (cumulative - running_max) / running_max

        max_drawdown = drawdown.min()
        max_dd_end = drawdown.idxmin()

        # 找到回撤开始点
        peak_before = cumulative.loc[:max_dd_end].idxmax()

        # 找到恢复点
        recovery_periods = cumulative.loc[max_dd_end:]
        recovery = recovery_periods[recovery_periods >= cumulative.loc[peak_before]]
        max_dd_recovery = recovery.index[0] if len(recovery) > 0 else None

        return {
            'max_drawdown': max_drawdown,
            'start_date': peak_before,
            'end_date': max_dd_end,
            'recovery_date': max_dd_recovery,
            'duration': (max_dd_end - peak_before).days,
        }

    def calculate_sharpe(
        self,
        returns: pd.Series,
        risk_free_rate: float = 0.03,
    ) -> float:
        """
        夏普比率

        Sharpe = (Rp - Rf) / σp
        """
        excess_returns = returns - risk_free_rate / 252  # 日度无风险利率
        return excess_returns.mean() / returns.std() * np.sqrt(252)

    def calculate_sortino(
        self,
        returns: pd.Series,
        risk_free_rate: float = 0.03,
    ) -> float:
        """
        索提诺比率 (只考虑下行波动)

        Sortino = (Rp - Rf) / σd
        """
        excess_returns = returns - risk_free_rate / 252
        downside_returns = returns[returns < 0]
        downside_std = downside_returns.std()

        return excess_returns.mean() / downside_std * np.sqrt(252)

    def calculate_calmar(self, returns: pd.Series) -> float:
        """
        Calmar比率

        Calmar = 年化收益 / |最大回撤|
        """
        annual_return = returns.mean() * 252
        max_dd = abs(self.calculate_max_drawdown(returns)['max_drawdown'])

        return annual_return / max_dd if max_dd > 0 else np.inf

    def calculate_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        """
        Value at Risk (风险价值)

        参数法: VaR = μ - z * σ
        """
        z_score = norm.ppf(1 - confidence)
        return returns.mean() + z_score * returns.std()
```

### 3.2 绩效归因

```python
class AttributionAnalyzer:
    """绩效归因分析"""

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
        1. 资产配置效应 (Allocation)
        2. 证券选择效应 (Selection)
        3. 交互效应 (Interaction)
        """
        # 组合收益
        portfolio_return = (portfolio_weights * portfolio_returns).sum()

        # 基准收益
        benchmark_return = (benchmark_weights * benchmark_returns).sum()

        # 超额收益
        excess_return = portfolio_return - benchmark_return

        # 资产配置效应
        allocation_effect = (
            (portfolio_weights - benchmark_weights) * benchmark_returns
        ).sum()

        # 证券选择效应
        selection_effect = (
            benchmark_weights * (portfolio_returns - benchmark_returns)
        ).sum()

        # 交互效应
        interaction_effect = (
            (portfolio_weights - benchmark_weights) *
            (portfolio_returns - benchmark_returns)
        ).sum()

        return AttributionReport(
            total_excess=excess_return,
            allocation_effect=allocation_effect,
            selection_effect=selection_effect,
            interaction_effect=interaction_effect,
        )
```

---

## 4. 接口定义

### 4.1 Python API

```python
from modules.backtest_engine import (
    BacktestEngine,
    MultiFactorStrategy,
    EMAStrategy,
    ValueFactor,
    MomentumFactor,
)

# 创建多因子策略
strategy = MultiFactorStrategy(
    factors=[
        ValueFactor(metrics=["pe", "pb"]),
        MomentumFactor(short_window=20, long_window=120),
        QualityFactor(),
    ],
    factor_weights={
        "value": 0.4,
        "momentum": 0.4,
        "quality": 0.2,
    },
    rebalance_freq="monthly",
    top_n=20,
)

# 运行回测
engine = BacktestEngine(
    initial_capital=1000000,
    commission_rate=0.0003,
)

result = engine.run(
    strategy=strategy,
    stock_pool=get_hs300_tickers(),
    start_date=datetime(2020, 1, 1),
    end_date=datetime(2024, 12, 31),
)

# 查看结果
print(f"总收益率: {result.total_return:.2%}")
print(f"年化收益率: {result.annualized_return:.2%}")
print(f"夏普比率: {result.sharpe_ratio:.2f}")
print(f"最大回撤: {result.max_drawdown:.2%}")

# 绘制权益曲线
result.plot_equity_curve()

# 导出详细报告
result.export_report("backtest_report.xlsx")
```

### 4.2 Streamlit集成

```python
def render_backtest_page():
    st.header("策略回测")

    # 策略选择
    strategy_type = st.selectbox(
        "策略类型",
        ["多因子", "EMA趋势", "事件驱动"]
    )

    if strategy_type == "多因子":
        col1, col2 = st.columns(2)
        with col1:
            value_weight = st.slider("价值因子权重", 0.0, 1.0, 0.4)
            momentum_weight = st.slider("动量因子权重", 0.0, 1.0, 0.4)
        with col2:
            quality_weight = st.slider("质量因子权重", 0.0, 1.0, 0.2)
            top_n = st.slider("选股数量", 5, 50, 20)

    elif strategy_type == "EMA趋势":
        use_tunnel = st.checkbox("使用EMA隧道", True)
        if not use_tunnel:
            fast_period = st.slider("快速EMA周期", 5, 20, 8)
            slow_period = st.slider("慢速EMA周期", 20, 60, 21)

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
            # 构建策略
            if strategy_type == "多因子":
                strategy = build_multifactor_strategy(
                    value_weight, momentum_weight, quality_weight, top_n
                )
            elif strategy_type == "EMA趋势":
                strategy = EMAStrategy(use_tunnel=use_tunnel)

            # 运行回测
            engine = BacktestEngine(
                initial_capital=initial_capital,
                commission_rate=commission,
            )
            result = engine.run(strategy, stock_pool, start_date, end_date)

        # 展示结果
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("总收益率", f"{result.total_return:.2%}")
        col2.metric("夏普比率", f"{result.sharpe_ratio:.2f}")
        col3.metric("最大回撤", f"{result.max_drawdown:.2%}")
        col4.metric("胜率", f"{result.win_rate:.1%}")

        # 权益曲线
        st.subheader("权益曲线")
        st.line_chart(result.equity_curve.set_index('date')['total_value'])

        # 交易记录
        st.subheader("交易记录")
        st.dataframe(result.trades)
```

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
