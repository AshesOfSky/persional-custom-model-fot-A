"""
param_sensitivity.py — 参数敏感度分析

对策略关键参数做网格搜索, 输出 2D 热力图数据,
帮助识别参数"稳定高原"(robust plateau) 与悬崖区域。
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from itertools import product
import logging

logger = logging.getLogger(__name__)


# ── 预置参数对 ──────────────────────────────────────────────────────────
# 每组是 (param_x, param_y, x 网格, y 网格)

SENSITIVITY_PAIRS: Dict[str, List[Tuple[str, str, list, list]]] = {
    "ema": [
        ("stop_loss_pct", "take_profit_pct",
         [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10],
         [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25]),
    ],
    "bollinger": [
        ("bb_period", "bb_std",
         [10, 15, 18, 20, 22, 25, 30],
         [1.2, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0]),
        ("bb_std", "stop_loss_pct",
         [1.5, 1.8, 2.0, 2.2, 2.5],
         [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08]),
    ],
    "kdj": [
        ("oversold", "overbought",
         [10, 15, 20, 25, 30, 35],
         [65, 70, 75, 80, 85, 90]),
    ],
    "macd": [
        ("stop_loss_pct", "take_profit_pct",
         [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10],
         [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]),
    ],
    "rsi": [
        ("oversold", "overbought",
         [20, 25, 28, 30, 32, 35, 40],
         [60, 65, 68, 70, 72, 75, 80]),
    ],
}


def run_sensitivity_analysis(
    df: pd.DataFrame,
    strategy_type: str = "ema",
    ticker: str = "",
    pair_index: int = 0,
    custom_pair: Optional[Tuple[str, str, list, list]] = None,
    use_atr_stop: bool = True,
    board: str = "其他",
    market_cap_yi: Optional[float] = None,
    progress_callback=None,
) -> Dict:
    """
    对指定参数对做敏感度分析。

    返回:
        {
            "success": bool,
            "param_x": str,
            "param_y": str,
            "x_values": list,
            "y_values": list,
            "sharpe_matrix": list[list[float]],   # [y][x] 的 Sharpe
            "return_matrix": list[list[float]],   # [y][x] 的总收益
            "trades_matrix": list[list[int]],     # [y][x] 的交易次数
            "best_params": dict,
            "best_sharpe": float,
            "plateau_score": float,  # 高原稳定度 (0-100)
        }
    """
    from .backtest import run_backtest

    if custom_pair:
        px, py, xv, yv = custom_pair
    else:
        pairs = SENSITIVITY_PAIRS.get(strategy_type.lower(), [])
        if not pairs or pair_index >= len(pairs):
            return {"success": False, "error": f"策略 {strategy_type} 无预置参数对"}
        px, py, xv, yv = pairs[pair_index]

    total = len(xv) * len(yv)
    sharpe_mat = np.full((len(yv), len(xv)), np.nan)
    return_mat = np.full((len(yv), len(xv)), np.nan)
    trades_mat = np.zeros((len(yv), len(xv)), dtype=int)

    best_sharpe = -999.0
    best_params = {}
    count = 0

    for yi, y_val in enumerate(yv):
        for xi, x_val in enumerate(xv):
            count += 1
            if progress_callback:
                progress_callback(count / total * 100, f"{px}={x_val}, {py}={y_val}")

            params = {px: x_val, py: y_val}
            try:
                result = run_backtest(
                    df.copy(),
                    strategy_type=strategy_type,
                    ticker=ticker,
                    use_atr_stop=use_atr_stop,
                    board=board,
                    market_cap_yi=market_cap_yi,
                    **params,
                )

                sr = result.sharpe_ratio
                if np.isnan(sr) or np.isinf(sr):
                    sr = 0.0

                sharpe_mat[yi, xi] = sr
                return_mat[yi, xi] = result.total_return
                trades_mat[yi, xi] = result.total_trades

                if result.total_trades >= 3 and sr > best_sharpe:
                    best_sharpe = sr
                    best_params = params.copy()

            except Exception:
                sharpe_mat[yi, xi] = 0.0
                return_mat[yi, xi] = 0.0

    # ── 高原稳定度评分 ──
    # 取最优 Sharpe 90% 以上的区域面积占比
    if best_sharpe > 0:
        threshold = best_sharpe * 0.9
        plateau_cells = np.sum(sharpe_mat >= threshold)
        total_cells = sharpe_mat.size
        plateau_score = round(plateau_cells / total_cells * 100, 1)
    else:
        plateau_score = 0.0

    return {
        "success": True,
        "param_x": px,
        "param_y": py,
        "x_values": xv,
        "y_values": yv,
        "sharpe_matrix": sharpe_mat.tolist(),
        "return_matrix": return_mat.tolist(),
        "trades_matrix": trades_mat.astype(int).tolist(),
        "best_params": best_params,
        "best_sharpe": round(best_sharpe, 3),
        "plateau_score": plateau_score,
    }


def get_available_pairs(strategy_type: str) -> List[Dict]:
    """获取某策略可用的参数对列表（供 UI 下拉选择）"""
    pairs = SENSITIVITY_PAIRS.get(strategy_type.lower(), [])
    result = []
    for i, (px, py, xv, yv) in enumerate(pairs):
        result.append({
            "index": i,
            "label": f"{px} × {py}",
            "param_x": px,
            "param_y": py,
            "grid_size": f"{len(xv)}×{len(yv)}={len(xv)*len(yv)}组合",
        })
    return result
