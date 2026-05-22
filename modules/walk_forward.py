"""
walk_forward.py — Walk-Forward 滚动优化框架

流程:
  1. 将历史数据分成 N 个 (训练窗口, 测试窗口) 对
  2. 在每个训练窗口内, 对策略参数做网格搜索, 以 Sharpe Ratio 最大化为目标
  3. 用最优参数在随后的 OOS 测试窗口运行回测
  4. 汇总所有 OOS 窗口的权益曲线, 输出参数漂移 + 稳定性报告

支持的策略: EMA / Bollinger / KDJ / MACD / RSI (与 backtest.py 对齐)
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from itertools import product
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


# ── 参数搜索空间定义 ────────────────────────────────────────────────────

# 每个策略可优化的参数及其网格
DEFAULT_PARAM_GRIDS: Dict[str, Dict[str, list]] = {
    "ema": {
        "stop_loss_pct": [0.03, 0.05, 0.07, 0.10],
        "take_profit_pct": [0.08, 0.12, 0.15, 0.20],
    },
    "bollinger": {
        "bb_period": [15, 20, 25],
        "bb_std": [1.5, 2.0, 2.5],
        "stop_loss_pct": [0.03, 0.05, 0.07],
    },
    "kdj": {
        "oversold": [15, 20, 25],
        "overbought": [75, 80, 85],
        "stop_loss_pct": [0.03, 0.05, 0.07],
    },
    "macd": {
        "stop_loss_pct": [0.03, 0.05, 0.07],
        "take_profit_pct": [0.08, 0.12, 0.15],
    },
    "rsi": {
        "oversold": [25, 30, 35],
        "overbought": [65, 70, 75],
        "stop_loss_pct": [0.03, 0.05, 0.07],
    },
}


@dataclass
class WindowResult:
    """单个窗口的优化+OOS结果"""
    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    best_params: Dict
    train_sharpe: float
    oos_sharpe: float
    oos_return: float
    oos_max_dd: float
    oos_win_rate: float
    oos_trades: int
    oos_equity: Optional[pd.DataFrame] = None


@dataclass
class WalkForwardResult:
    """Walk-Forward 汇总结果"""
    strategy_type: str
    ticker: str
    n_windows: int
    window_results: List[WindowResult] = field(default_factory=list)
    # 汇总指标
    oos_total_return: float = 0.0
    oos_avg_sharpe: float = 0.0
    oos_avg_win_rate: float = 0.0
    oos_worst_dd: float = 0.0
    param_stability: Dict = field(default_factory=dict)
    combined_equity: Optional[pd.DataFrame] = None

    def summary(self) -> str:
        return (
            f"Walk-Forward [{self.strategy_type}] {self.ticker}\n"
            f"  窗口数: {self.n_windows}\n"
            f"  OOS 综合收益: {self.oos_total_return*100:.2f}%\n"
            f"  OOS 平均 Sharpe: {self.oos_avg_sharpe:.2f}\n"
            f"  OOS 平均胜率: {self.oos_avg_win_rate*100:.1f}%\n"
            f"  OOS 最大回撤: {self.oos_worst_dd*100:.2f}%\n"
        )


# ── 目标函数 ────────────────────────────────────────────────────────────

def _objective_sharpe(result) -> float:
    """以 Sharpe Ratio 为优化目标（越大越好）"""
    sr = result.sharpe_ratio
    if np.isnan(sr) or np.isinf(sr):
        return -999.0
    # 惩罚交易次数太少的参数组合（可能过拟合）
    if result.total_trades < 3:
        return -999.0
    return sr


# ── 网格搜索 ────────────────────────────────────────────────────────────

def _grid_search(
    df_train: pd.DataFrame,
    strategy_type: str,
    param_grid: Dict[str, list],
    ticker: str = "",
    use_atr_stop: bool = True,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
) -> Tuple[Dict, float]:
    """
    在训练集上对参数网格做穷举搜索。

    返回: (最优参数字典, 最优 Sharpe)
    """
    from .backtest import run_backtest

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(product(*values))

    best_score = -999.0
    best_params = {}

    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            result = run_backtest(
                df_train.copy(),
                strategy_type=strategy_type,
                ticker=ticker,
                use_atr_stop=use_atr_stop,
                board=board,
                market_cap_yi=market_cap_yi,
                **params,
            )
            score = _objective_sharpe(result)
            if score > best_score:
                best_score = score
                best_params = params.copy()
        except Exception:
            continue

    return best_params, best_score


# ── 窗口划分 ────────────────────────────────────────────────────────────

def _split_windows(
    df: pd.DataFrame,
    train_bars: int = 252,
    test_bars: int = 63,
    step_bars: Optional[int] = None,
) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    将数据按滚动窗口划分为 (训练集, 测试集) 对。

    参数:
        train_bars: 训练窗口长度（默认 252 ≈ 1年）
        test_bars:  测试窗口长度（默认 63 ≈ 1季度）
        step_bars:  滚动步长（默认 = test_bars，即不重叠）
    """
    if step_bars is None:
        step_bars = test_bars

    n = len(df)
    windows = []
    start = 0

    while start + train_bars + test_bars <= n:
        train_end = start + train_bars
        test_end = train_end + test_bars
        df_train = df.iloc[start:train_end].copy()
        df_test = df.iloc[train_end:test_end].copy()
        windows.append((df_train, df_test))
        start += step_bars

    return windows


# ── Walk-Forward 主入口 ─────────────────────────────────────────────────

def run_walk_forward(
    df: pd.DataFrame,
    strategy_type: str = "ema",
    ticker: str = "",
    param_grid: Optional[Dict[str, list]] = None,
    train_bars: int = 252,
    test_bars: int = 63,
    step_bars: Optional[int] = None,
    use_atr_stop: bool = True,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
    progress_callback=None,
) -> WalkForwardResult:
    """
    运行 Walk-Forward 滚动优化。

    参数:
        df:               完整历史 OHLCV+指标 DataFrame
        strategy_type:    策略类型（ema/bollinger/kdj/macd/rsi）
        param_grid:       参数网格，缺省使用 DEFAULT_PARAM_GRIDS
        train_bars:       训练窗口长度
        test_bars:        OOS 测试窗口长度
        step_bars:        滚动步长
        progress_callback: 进度回调 fn(pct: float, msg: str)

    返回: WalkForwardResult
    """
    from .backtest import run_backtest

    if param_grid is None:
        param_grid = DEFAULT_PARAM_GRIDS.get(strategy_type.lower(), {})

    if not param_grid:
        raise ValueError(f"策略 {strategy_type} 没有可用的参数网格")

    windows = _split_windows(df, train_bars, test_bars, step_bars)
    if not windows:
        raise ValueError(
            f"数据量不足: 需要至少 {train_bars + test_bars} 根K线，"
            f"实际 {len(df)} 根"
        )

    wf_result = WalkForwardResult(
        strategy_type=strategy_type,
        ticker=ticker,
        n_windows=len(windows),
    )

    all_oos_equity = []
    param_history = {k: [] for k in param_grid.keys()}

    for i, (df_train, df_test) in enumerate(windows):
        if progress_callback:
            pct = (i + 1) / len(windows) * 100
            progress_callback(pct, f"窗口 {i+1}/{len(windows)}")

        # 1. 训练窗口内网格搜索
        best_params, train_sharpe = _grid_search(
            df_train, strategy_type, param_grid,
            ticker=ticker, use_atr_stop=use_atr_stop,
            board=board, market_cap_yi=market_cap_yi,
        )

        if not best_params:
            # 所有参数组合都失败，用默认参数
            best_params = {k: v[len(v)//2] for k, v in param_grid.items()}
            train_sharpe = 0.0

        # 记录参数轨迹
        for k in param_history:
            param_history[k].append(best_params.get(k))

        # 2. OOS 测试窗口回测
        try:
            oos_result = run_backtest(
                df_test.copy(),
                strategy_type=strategy_type,
                ticker=ticker,
                use_atr_stop=use_atr_stop,
                board=board,
                market_cap_yi=market_cap_yi,
                **best_params,
            )

            wr = WindowResult(
                window_id=i,
                train_start=str(df_train.index[0].date()),
                train_end=str(df_train.index[-1].date()),
                test_start=str(df_test.index[0].date()),
                test_end=str(df_test.index[-1].date()),
                best_params=best_params,
                train_sharpe=round(train_sharpe, 3),
                oos_sharpe=round(oos_result.sharpe_ratio, 3),
                oos_return=round(oos_result.total_return, 4),
                oos_max_dd=round(oos_result.max_drawdown_pct, 4),
                oos_win_rate=round(oos_result.win_rate, 4),
                oos_trades=oos_result.total_trades,
                oos_equity=oos_result.equity_curve,
            )
            wf_result.window_results.append(wr)

            if not oos_result.equity_curve.empty:
                all_oos_equity.append(oos_result.equity_curve)

        except Exception as e:
            logger.warning(f"窗口 {i} OOS 回测失败: {e}")
            continue

    # ── 汇总统计 ──
    if wf_result.window_results:
        wrs = wf_result.window_results
        wf_result.oos_avg_sharpe = round(
            np.mean([w.oos_sharpe for w in wrs]), 3
        )
        wf_result.oos_avg_win_rate = round(
            np.mean([w.oos_win_rate for w in wrs]), 4
        )
        wf_result.oos_worst_dd = round(
            min(w.oos_max_dd for w in wrs), 4
        )

        # 复合 OOS 收益（连乘）
        compound = 1.0
        for w in wrs:
            compound *= (1 + w.oos_return)
        wf_result.oos_total_return = round(compound - 1, 4)

        # 拼接 OOS 权益曲线
        if all_oos_equity:
            wf_result.combined_equity = pd.concat(all_oos_equity)

    # ── 参数稳定性分析 ──
    stability = {}
    for k, vals in param_history.items():
        vals_clean = [v for v in vals if v is not None]
        if vals_clean:
            stability[k] = {
                "values": vals_clean,
                "mean": round(float(np.mean(vals_clean)), 4),
                "std": round(float(np.std(vals_clean)), 4),
                "cv": round(float(np.std(vals_clean) / np.mean(vals_clean)), 4)
                       if np.mean(vals_clean) != 0 else 0.0,
                "mode": max(set(vals_clean), key=vals_clean.count),
                "stable": float(np.std(vals_clean) / np.mean(vals_clean)) < 0.3
                          if np.mean(vals_clean) != 0 else True,
            }
    wf_result.param_stability = stability

    return wf_result


# ── 便捷函数：快速 WF 报告 ──────────────────────────────────────────────

def quick_walk_forward(
    df: pd.DataFrame,
    strategy_type: str = "ema",
    ticker: str = "",
    progress_callback=None,
) -> Dict:
    """
    快速执行 Walk-Forward 并返回结构化报告字典（供 Streamlit 使用）。
    自动选择合理的窗口长度：
      - 日线数据 >= 500 根：train=252, test=63
      - 日线数据 >= 300 根：train=180, test=40
      - 日线数据 >= 150 根：train=100, test=30
    """
    n = len(df)

    if n >= 500:
        train, test = 252, 63
    elif n >= 300:
        train, test = 180, 40
    elif n >= 150:
        train, test = 100, 30
    else:
        return {
            "error": f"数据量不足: {n} 根K线，Walk-Forward 至少需要 150 根",
            "success": False,
        }

    try:
        result = run_walk_forward(
            df, strategy_type=strategy_type, ticker=ticker,
            train_bars=train, test_bars=test,
            progress_callback=progress_callback,
        )

        # 构造报告
        windows_data = []
        for w in result.window_results:
            windows_data.append({
                "窗口": f"W{w.window_id+1}",
                "训练期": f"{w.train_start} ~ {w.train_end}",
                "测试期": f"{w.test_start} ~ {w.test_end}",
                "最优参数": w.best_params,
                "训练Sharpe": w.train_sharpe,
                "OOS Sharpe": w.oos_sharpe,
                "OOS收益": f"{w.oos_return*100:.2f}%",
                "OOS回撤": f"{w.oos_max_dd*100:.2f}%",
                "OOS胜率": f"{w.oos_win_rate*100:.1f}%",
                "OOS交易数": w.oos_trades,
            })

        return {
            "success": True,
            "summary": result.summary(),
            "strategy": result.strategy_type,
            "ticker": result.ticker,
            "n_windows": result.n_windows,
            "oos_total_return": result.oos_total_return,
            "oos_avg_sharpe": result.oos_avg_sharpe,
            "oos_avg_win_rate": result.oos_avg_win_rate,
            "oos_worst_dd": result.oos_worst_dd,
            "param_stability": result.param_stability,
            "windows": windows_data,
            "combined_equity": result.combined_equity,
            "train_bars": train,
            "test_bars": test,
        }

    except Exception as e:
        return {"error": str(e), "success": False}
