"""
risk_metrics.py — 风险量化指标引擎
VaR, CVaR, Beta, Alpha, Sortino, Calmar, 波动率锥, Kelly公式, 最大回撤分析
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


# ─── 无风险利率 ──────────────────────────────────────────────────────────────
# 按市场设置默认值，2025-2026 区间。需要更精确时传 live=True 拉实时国债收益率。

DEFAULT_RISK_FREE_RATES = {
    "A股":   0.022,    # 中国 10Y 国债 ~2.2% (2025-2026)
    "港股":  0.040,    # 港币利率跟美元
    "美股":  0.043,    # 美国 10Y Treasury ~4.3%
    "国内期货": 0.022,
    "美股期货": 0.043,
    "外汇":   0.030,
    "其他":   0.030,
}


def get_risk_free_rate(market: str = "A股", live: bool = False) -> float:
    """
    获取无风险利率（年化）
    market: 市场分类（来自 data_router.classify_ticker 返回值）
    live=True 时尝试拉实时国债收益率，失败回退到默认值
    """
    if live:
        try:
            if market in ("美股", "美股期货"):
                import yfinance as yf
                tnx = yf.Ticker("^TNX").history(period="5d")["Close"].iloc[-1]
                return float(tnx) / 100
        except Exception as e:
            logger.debug(f"实时无风险利率拉取失败，回退默认值: {e}")
    return DEFAULT_RISK_FREE_RATES.get(market, 0.030)


# ─── VaR & CVaR ──────────────────────────────────────────────────────────────

def _calc_var(returns: pd.Series, confidence: float = 0.95) -> float:
    """历史模拟法 VaR"""
    if returns.empty:
        return 0
    return float(np.percentile(returns.dropna(), (1 - confidence) * 100))


def _calc_cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """条件VaR (Expected Shortfall)"""
    if returns.empty:
        return 0
    var = _calc_var(returns, confidence)
    tail = returns[returns <= var]
    return float(tail.mean()) if len(tail) > 0 else var


# ─── Beta & Alpha ────────────────────────────────────────────────────────────

def _calc_beta(stock_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    """Beta = Cov(Rs, Rb) / Var(Rb)"""
    aligned = pd.DataFrame({"stock": stock_returns, "bench": benchmark_returns}).dropna()
    if len(aligned) < 20:
        return None
    cov = aligned["stock"].cov(aligned["bench"])
    var_b = aligned["bench"].var()
    return round(cov / var_b, 4) if var_b > 0 else None


def _calc_alpha(stock_returns: pd.Series, benchmark_returns: pd.Series,
                risk_free_rate: float = 0.03) -> float:
    """Jensen's Alpha (年化)"""
    beta = _calc_beta(stock_returns, benchmark_returns)
    if beta is None:
        return None

    aligned = pd.DataFrame({"stock": stock_returns, "bench": benchmark_returns}).dropna()
    n = len(aligned)

    stock_annual = (1 + aligned["stock"].mean()) ** 252 - 1
    bench_annual = (1 + aligned["bench"].mean()) ** 252 - 1

    alpha = stock_annual - (risk_free_rate + beta * (bench_annual - risk_free_rate))
    return round(alpha * 100, 2)  # 百分比


# ─── Sortino & Calmar ────────────────────────────────────────────────────────

def _calc_sortino(returns: pd.Series, risk_free_rate: float = 0.03) -> float:
    """Sortino Ratio: 只考虑下行偏差"""
    if returns.empty or len(returns) < 20:
        return 0
    daily_rf = risk_free_rate / 252
    excess = returns - daily_rf
    downside = returns[returns < 0]
    downside_std = downside.std()
    if downside_std == 0:
        return 0
    return round(float(np.sqrt(252) * excess.mean() / downside_std), 4)


def _calc_calmar(returns: pd.Series, max_dd: float) -> float:
    """Calmar Ratio: 年化收益 / 最大回撤"""
    if max_dd == 0 or len(returns) < 20:
        return 0
    annual_return = (1 + returns.mean()) ** 252 - 1
    return round(annual_return / abs(max_dd), 4)


# ─── 最大回撤分析 ────────────────────────────────────────────────────────────

def _calc_max_drawdown(df: pd.DataFrame) -> Dict:
    """最大回撤 + 持续天数 + 恢复天数"""
    close = df["Close"]
    cum_max = close.cummax()
    drawdown = (close - cum_max) / cum_max

    max_dd = drawdown.min()
    max_dd_idx = drawdown.idxmin()

    # 找到峰值和谷底
    peak_idx = cum_max[:max_dd_idx].idxmax() if hasattr(cum_max.index, 'get_loc') else None
    trough_idx = max_dd_idx

    # 恢复日期
    recovery_idx = None
    if max_dd_idx is not None:
        try:
            post_trough = close[close.index > max_dd_idx]
            peak_val = cum_max[max_dd_idx]
            recovered = post_trough[post_trough >= peak_val]
            if len(recovered) > 0:
                recovery_idx = recovered.index[0]
        except Exception:
            pass

    # 计算天数
    duration = None
    recovery_days = None
    try:
        if peak_idx is not None and trough_idx is not None:
            if hasattr(peak_idx, 'date'):
                duration = (trough_idx - peak_idx).days
                if recovery_idx is not None:
                    recovery_days = (recovery_idx - trough_idx).days
            else:
                peak_pos = df.index.get_loc(peak_idx) if peak_idx in df.index else None
                trough_pos = df.index.get_loc(trough_idx) if trough_idx in df.index else None
                if peak_pos is not None and trough_pos is not None:
                    duration = trough_pos - peak_pos
    except Exception:
        pass

    return {
        "value": round(float(max_dd) * 100, 2),  # 百分比
        "start_date": str(peak_idx) if peak_idx is not None else "—",
        "end_date": str(trough_idx) if trough_idx is not None else "—",
        "recovery_date": str(recovery_idx) if recovery_idx is not None else "未恢复",
        "duration_days": duration,
        "recovery_days": recovery_days,
    }


# ─── 波动率锥 ────────────────────────────────────────────────────────────────

def _calc_volatility_cone(df: pd.DataFrame) -> Dict:
    """
    波动率锥：各周期滚动年化波动率的分布
    用于判断当前波动率在历史中的位置
    """
    if len(df) < 60:
        return {"available": False, "description": "数据不足以计算波动率锥"}

    returns = df["Close"].pct_change().dropna()

    periods = {
        "30d": 30,
        "60d": 60,
        "90d": 90,
    }
    if len(returns) >= 180:
        periods["180d"] = 180
    if len(returns) >= 360:
        periods["360d"] = 360

    # 当前20日年化波动率
    current_vol = returns.tail(20).std() * np.sqrt(252) * 100

    cone_data = {}
    for name, period in periods.items():
        if len(returns) < period + 20:
            continue
        rolling_vol = returns.rolling(period).std() * np.sqrt(252) * 100
        rolling_vol = rolling_vol.dropna()
        if len(rolling_vol) < 5:
            continue

        cone_data[name] = {
            "min": round(float(rolling_vol.min()), 2),
            "q25": round(float(rolling_vol.quantile(0.25)), 2),
            "median": round(float(rolling_vol.median()), 2),
            "q75": round(float(rolling_vol.quantile(0.75)), 2),
            "max": round(float(rolling_vol.max()), 2),
        }

    # 当前波动率在历史中的百分位
    if len(returns) >= 60:
        all_vol_20d = returns.rolling(20).std() * np.sqrt(252) * 100
        all_vol_20d = all_vol_20d.dropna()
        percentile = float((all_vol_20d < current_vol).sum() / len(all_vol_20d) * 100)
    else:
        percentile = 50

    # 解释
    if percentile > 80:
        interp = f"当前波动率{current_vol:.1f}%处于历史{percentile:.0f}%分位（偏高），市场波动较大，注意控制仓位"
        level = "偏高"
    elif percentile > 60:
        interp = f"当前波动率{current_vol:.1f}%处于历史{percentile:.0f}%分位（中高），波动正常偏大"
        level = "中高"
    elif percentile > 40:
        interp = f"当前波动率{current_vol:.1f}%处于历史{percentile:.0f}%分位（正常），波动适中"
        level = "正常"
    elif percentile > 20:
        interp = f"当前波动率{current_vol:.1f}%处于历史{percentile:.0f}%分位（偏低），可能即将放大"
        level = "偏低"
    else:
        interp = f"当前波动率{current_vol:.1f}%处于历史{percentile:.0f}%分位（极低），市场平静，可能酝酿大行情"
        level = "极低"

    return {
        "available": True,
        "current_vol": round(current_vol, 2),
        "percentile": round(percentile, 1),
        "level": level,
        "cone_data": cone_data,
        "interpretation": interp,
    }


# ─── Kelly 公式 ──────────────────────────────────────────────────────────────

def _calc_kelly(returns: pd.Series) -> Dict:
    """
    Kelly Criterion: f* = (p * avg_win - q * avg_loss) / avg_win
    p = win_rate, q = 1 - p
    """
    if len(returns) < 20:
        return {"optimal_fraction": 0, "half_kelly": 0, "interpretation": "数据不足"}

    wins = returns[returns > 0]
    losses = returns[returns < 0]

    if len(wins) == 0 or len(losses) == 0:
        return {"optimal_fraction": 0, "half_kelly": 0, "interpretation": "无法计算（无盈亏对比）"}

    win_rate = len(wins) / len(returns)
    avg_win = wins.mean()
    avg_loss = abs(losses.mean())

    if avg_win == 0:
        return {"optimal_fraction": 0, "half_kelly": 0, "interpretation": "平均盈利为零"}

    # Kelly: f* = p/b_loss - q/b_win  (简化为) f* = (p*avg_win - q*avg_loss) / avg_win
    kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
    kelly = max(0, min(kelly, 1))  # 限制在0-1之间
    half_kelly = kelly / 2

    if kelly <= 0:
        interp = "Kelly公式建议不投入资金（期望收益为负），应规避该标的"
    elif half_kelly < 0.05:
        interp = f"建议极轻仓（Half Kelly {half_kelly*100:.1f}%），风险收益比较差"
    elif half_kelly < 0.15:
        interp = f"建议轻仓（Half Kelly {half_kelly*100:.1f}%），风险收益比一般"
    elif half_kelly < 0.30:
        interp = f"可适度配置（Half Kelly {half_kelly*100:.1f}%），风险收益比尚可"
    else:
        interp = f"可积极配置（Half Kelly {half_kelly*100:.1f}%），但建议不超过Half Kelly"

    return {
        "optimal_fraction": round(kelly * 100, 2),     # 百分比
        "half_kelly": round(half_kelly * 100, 2),       # 百分比
        "win_rate": round(win_rate * 100, 2),
        "avg_win": round(avg_win * 100, 4),
        "avg_loss": round(avg_loss * 100, 4),
        "interpretation": interp,
    }


# ─── 风险等级 ────────────────────────────────────────────────────────────────

def _assess_risk_level(var_95: float, beta: float, vol_pct: float, max_dd: float) -> Dict:
    """综合风险等级评估"""
    score = 0

    # VaR 评分
    if abs(var_95) > 5:
        score += 30
    elif abs(var_95) > 3:
        score += 20
    elif abs(var_95) > 1.5:
        score += 10

    # Beta 评分
    if beta is not None:
        if abs(beta) > 1.5:
            score += 25
        elif abs(beta) > 1.0:
            score += 15
        elif abs(beta) > 0.5:
            score += 5

    # 波动率评分
    if vol_pct > 50:
        score += 25
    elif vol_pct > 30:
        score += 15
    elif vol_pct > 15:
        score += 5

    # 最大回撤评分
    if abs(max_dd) > 30:
        score += 20
    elif abs(max_dd) > 15:
        score += 10
    elif abs(max_dd) > 5:
        score += 5

    if score >= 60:
        return {"level": "极高", "color": "#F6465D", "score": score}
    elif score >= 40:
        return {"level": "高", "color": "#FF8C00", "score": score}
    elif score >= 20:
        return {"level": "中", "color": "#FFD700", "score": score}
    else:
        return {"level": "低", "color": "#0ECB81", "score": score}


# ─── 主函数 ──────────────────────────────────────────────────────────────────

def calc_risk_metrics(df: pd.DataFrame, benchmark_df: pd.DataFrame = None,
                      risk_free_rate: float = None,
                      market: str = "A股") -> Dict:
    """
    计算全套风险量化指标

    参数:
        df: 股票OHLCV DataFrame
        benchmark_df: 基准指数OHLCV DataFrame（可选）
        risk_free_rate: 无风险利率（年化）；None 时按 market 自动选取（A 股 2.2% / 美股 4.3% / 港股 4.0%）
        market: 市场分类，用于无风险利率自适应

    返回:
        包含VaR/CVaR/Beta/Alpha/Sortino/Calmar/波动率锥/Kelly/最大回撤的完整风险报告
    """
    if risk_free_rate is None:
        risk_free_rate = get_risk_free_rate(market)

    if df is None or df.empty or len(df) < 20:
        return {
            "available": False,
            "error": "数据不足以计算风险指标",
            "risk_level": "—",
        }

    returns = df["Close"].pct_change().dropna()

    # VaR / CVaR
    var_95 = _calc_var(returns, 0.95)
    var_99 = _calc_var(returns, 0.99)
    cvar_95 = _calc_cvar(returns, 0.95)
    var_95_amount = round(abs(var_95) * 10000, 2)  # 每万元投资的最大亏损

    # Beta / Alpha
    beta = None
    alpha = None
    if benchmark_df is not None and not benchmark_df.empty and len(benchmark_df) >= 20:
        bench_returns = benchmark_df["Close"].pct_change().dropna()
        beta = _calc_beta(returns, bench_returns)
        alpha = _calc_alpha(returns, bench_returns, risk_free_rate)

    # Sortino / Calmar
    max_dd_info = _calc_max_drawdown(df)
    sortino = _calc_sortino(returns, risk_free_rate)
    calmar = _calc_calmar(returns, abs(max_dd_info["value"]) / 100)

    # 波动率锥
    vol_cone = _calc_volatility_cone(df)

    # Kelly
    kelly = _calc_kelly(returns)

    # Sharpe（复用）
    daily_rf = risk_free_rate / 252
    sharpe = float(np.sqrt(252) * (returns.mean() - daily_rf) / returns.std()) if returns.std() > 0 else 0

    # 风险等级
    current_vol = vol_cone.get("current_vol", 0) if vol_cone.get("available") else 0
    risk_level_info = _assess_risk_level(var_95 * 100, beta, current_vol, max_dd_info["value"])

    return {
        "available": True,
        "var_95": round(var_95 * 100, 2),           # 百分比
        "var_99": round(var_99 * 100, 2),
        "var_95_amount": var_95_amount,
        "cvar_95": round(cvar_95 * 100, 2),
        "beta": beta,
        "alpha": alpha,
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "volatility_cone": vol_cone,
        "kelly_criterion": kelly,
        "max_drawdown": max_dd_info,
        "risk_level": risk_level_info["level"],
        "risk_color": risk_level_info["color"],
        "risk_score": risk_level_info["score"],
    }


# ─── 蒙特卡洛模拟 ─────────────────────────────────────────────────────────────

def run_monte_carlo(df, n_simulations: int = 1000, n_days: int = 60,
                    confidence_levels=None):
    """
    蒙特卡洛模拟：基于几何布朗运动(GBM)预测未来价格分布

    Args:
        df: OHLCV DataFrame
        n_simulations: 模拟次数
        n_days: 预测天数
        confidence_levels: 置信度列表

    Returns:
        {
            "current_price": float,
            "median_price": float,
            "mean_price": float,
            "confidence_bands": {5: price, 25: price, ...},
            "prob_profit": float,
            "prob_loss_10pct": float,
            "paths_sample": [[prices], ...],  # 5条示例路径
            "targets": [{days, p5, p25, p50, p75, p95}, ...],
        }
    """
    if confidence_levels is None:
        confidence_levels = [5, 25, 50, 75, 95]

    if df is None or df.empty or len(df) < 30:
        return {}

    try:
        close = df["Close"].dropna()
        current_price = float(close.iloc[-1])

        # 日收益率统计
        log_returns = np.log(close / close.shift(1)).dropna()
        mu = float(log_returns.mean())
        sigma = float(log_returns.std())

        if sigma == 0 or np.isnan(sigma):
            return {}

        dt = 1  # 日频
        np.random.seed(42)

        # 生成随机路径
        # S(t+1) = S(t) * exp((mu - sigma^2/2)*dt + sigma*sqrt(dt)*Z)
        random_shocks = np.random.normal(0, 1, (n_simulations, n_days))
        drift = (mu - 0.5 * sigma ** 2) * dt
        diffusion = sigma * np.sqrt(dt) * random_shocks

        # 累积对数收益
        cum_log_returns = np.cumsum(drift + diffusion, axis=1)

        # 价格矩阵
        price_paths = current_price * np.exp(cum_log_returns)

        # 终点价格分布
        final_prices = price_paths[:, -1]
        median_price = float(np.median(final_prices))
        mean_price = float(np.mean(final_prices))

        # 置信度区间
        confidence_bands = {}
        for cl in confidence_levels:
            confidence_bands[cl] = round(float(np.percentile(final_prices, cl)), 2)

        # 盈利/亏损概率
        prob_profit = float(np.sum(final_prices > current_price) / n_simulations * 100)
        prob_loss_10 = float(np.sum(final_prices < current_price * 0.9) / n_simulations * 100)
        prob_gain_10 = float(np.sum(final_prices > current_price * 1.1) / n_simulations * 100)

        # 不同时间点的预测 (30天、60天)
        targets = []
        for check_day in [min(30, n_days), n_days]:
            if check_day <= 0:
                continue
            idx = check_day - 1
            if idx < price_paths.shape[1]:
                day_prices = price_paths[:, idx]
                targets.append({
                    "days": check_day,
                    "p5": round(float(np.percentile(day_prices, 5)), 2),
                    "p25": round(float(np.percentile(day_prices, 25)), 2),
                    "p50": round(float(np.percentile(day_prices, 50)), 2),
                    "p75": round(float(np.percentile(day_prices, 75)), 2),
                    "p95": round(float(np.percentile(day_prices, 95)), 2),
                })

        # 采样5条路径用于绘图
        sample_indices = np.linspace(0, n_simulations - 1, 5, dtype=int)
        paths_sample = [[current_price] + price_paths[i].tolist() for i in sample_indices]

        # 各百分位路径（用于扇形图）
        percentile_paths = {}
        for pct in [5, 25, 50, 75, 95]:
            path = [current_price]
            for d in range(n_days):
                path.append(float(np.percentile(price_paths[:, d], pct)))
            percentile_paths[pct] = path

        return {
            "current_price": round(current_price, 2),
            "n_simulations": n_simulations,
            "n_days": n_days,
            "median_price": round(median_price, 2),
            "mean_price": round(mean_price, 2),
            "confidence_bands": confidence_bands,
            "prob_profit": round(prob_profit, 1),
            "prob_loss_10pct": round(prob_loss_10, 1),
            "prob_gain_10pct": round(prob_gain_10, 1),
            "targets": targets,
            "paths_sample": paths_sample,
            "percentile_paths": percentile_paths,
            "mu_daily": round(mu * 100, 4),
            "sigma_daily": round(sigma * 100, 4),
        }
    except Exception as e:
        logger.warning(f"Monte Carlo simulation error: {e}")
        return {}
