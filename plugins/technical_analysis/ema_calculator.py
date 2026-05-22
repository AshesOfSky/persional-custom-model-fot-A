"""
EMA趋势计算器
基于日线/周线数据计算多周期指数移动平均线
识别多头或空头排列形态
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class TrendDirection(Enum):
    """趋势方向"""
    BULLISH = "多头"
    BEARISH = "空头"
    NEUTRAL = "震荡"


@dataclass
class EMALine:
    """EMA线数据结构"""
    period: int
    values: pd.Series
    current_value: float
    slope: float  # 斜率
    direction: str  # up, down, flat


@dataclass
class EMATrendSignal:
    """EMA趋势信号"""
    trend: TrendDirection
    strength: float  # 0-100
    alignment_score: float  # 均线整齐度
    crossover_signal: Optional[str]  # 金叉/死叉信号
    support_levels: List[float]
    resistance_levels: List[float]
    ema_values: Dict[int, float]  # 各周期EMA当前值


class EMACalculator:
    """
    EMA趋势计算器
    计算多周期EMA并识别趋势形态
    """

    # 默认EMA周期
    DEFAULT_PERIODS = [8, 13, 21, 55, 144, 233]

    # 内外隧道周期
    INNER_PERIODS = [8, 13, 21]
    OUTER_PERIODS = [55, 144, 233]

    def __init__(self, periods: List[int] = None):
        self.periods = periods or self.DEFAULT_PERIODS

    def calculate(self, df: pd.DataFrame, price_col: str = "Close") -> pd.DataFrame:
        """
        计算所有EMA

        Args:
            df: 价格数据DataFrame
            price_col: 价格列名

        Returns:
            DataFrame: 添加了EMA列的DataFrame
        """
        df = df.copy()

        for period in self.periods:
            df[f"EMA_{period}"] = df[price_col].ewm(span=period, adjust=False).mean()

        return df

    def get_ema_line(self, df: pd.DataFrame, period: int) -> EMALine:
        """
        获取单条EMA线的信息

        Args:
            df: 带EMA的数据
            period: EMA周期

        Returns:
            EMALine: EMA线数据
        """
        col_name = f"EMA_{period}"
        if col_name not in df.columns:
            raise ValueError(f"EMA_{period} not calculated")

        ema_values = df[col_name]
        current = ema_values.iloc[-1]

        # 计算斜率（最近5天）
        if len(ema_values) >= 5:
            slope = (ema_values.iloc[-1] - ema_values.iloc[-5]) / 5
        else:
            slope = 0

        # 判断方向
        if slope > current * 0.001:  # 上涨超过0.1%
            direction = "up"
        elif slope < -current * 0.001:
            direction = "down"
        else:
            direction = "flat"

        return EMALine(
            period=period,
            values=ema_values,
            current_value=current,
            slope=slope,
            direction=direction
        )

    def analyze_trend(self, df: pd.DataFrame) -> EMATrendSignal:
        """
        分析EMA趋势

        Args:
            df: 带EMA的数据

        Returns:
            EMATrendSignal: 趋势信号
        """
        # 确保所有EMA已计算
        for period in self.periods:
            if f"EMA_{period}" not in df.columns:
                df = self.calculate(df)
                break

        # 获取当前价格
        close = df["Close"].iloc[-1]

        # 获取各EMA当前值
        ema_values = {p: df[f"EMA_{p}"].iloc[-1] for p in self.periods}

        # 获取内隧道和外隧道
        inner_emas = [ema_values[p] for p in self.INNER_PERIODS if p in ema_values]
        outer_emas = [ema_values[p] for p in self.OUTER_PERIODS if p in ema_values]

        inner_min, inner_max = min(inner_emas), max(inner_emas)
        outer_min, outer_max = min(outer_emas), max(outer_emas)

        # 判断趋势方向
        trend = TrendDirection.NEUTRAL
        trend_strength = 50

        # 价格在外隧道上方 = 强势多头
        if close > outer_max:
            trend = TrendDirection.BULLISH
            trend_strength = 80
        # 价格在外隧道下方 = 强势空头
        elif close < outer_min:
            trend = TrendDirection.BEARISH
            trend_strength = 20
        # 价格在内外隧道之间
        elif inner_min <= close <= inner_max:
            trend = TrendDirection.NEUTRAL
            trend_strength = 50

        # 计算均线整齐度（所有EMA按周期排序后的方差）
        sorted_emas = sorted(ema_values.items(), key=lambda x: x[1])
        alignment_score = 100
        if len(sorted_emas) >= 2:
            expected_order = sorted(self.periods)
            actual_order = [p for p, _ in sorted_emas]
            inversions = sum(1 for i in range(len(expected_order)) if expected_order[i] != actual_order[i])
            alignment_score = max(0, 100 - inversions * 20)

        # 检测金叉/死叉
        crossover_signal = self._detect_crossover(df)

        # 计算支撑和阻力位
        support_levels = []
        resistance_levels = []

        # 隧道作为支撑/阻力
        if trend == TrendDirection.BULLISH:
            support_levels.append(inner_min)
            support_levels.append(outer_min)
        elif trend == TrendDirection.BEARISH:
            resistance_levels.append(inner_max)
            resistance_levels.append(outer_max)

        return EMATrendSignal(
            trend=trend,
            strength=trend_strength,
            alignment_score=alignment_score,
            crossover_signal=crossover_signal,
            support_levels=support_levels,
            resistance_levels=resistance_levels,
            ema_values=ema_values
        )

    def _detect_crossover(self, df: pd.DataFrame) -> Optional[str]:
        """
        检测金叉/死叉信号

        Returns:
            Optional[str]: "golden_cross", "death_cross", or None
        """
        if len(df) < 2:
            return None

        # 使用短期和长期EMA交叉
        short_col = f"EMA_{self.INNER_PERIODS[0]}"  # EMA8
        long_col = f"EMA_{self.OUTER_PERIODS[0]}"   # EMA55

        if short_col not in df.columns or long_col not in df.columns:
            return None

        short_prev = df[short_col].iloc[-2]
        short_curr = df[short_col].iloc[-1]
        long_prev = df[long_col].iloc[-2]
        long_curr = df[long_col].iloc[-1]

        # 金叉：短期从下方穿越长期
        if short_prev <= long_prev and short_curr > long_curr:
            return "golden_cross"

        # 死叉：短期从上方穿越长期
        if short_prev >= long_prev and short_curr < long_curr:
            return "death_cross"

        return None

    def get_tunnel_position(self, df: pd.DataFrame, price: float = None) -> Dict:
        """
        获取价格在隧道中的位置

        Args:
            df: 带EMA的数据
            price: 指定价格（默认用收盘价）

        Returns:
            Dict: 位置信息
        """
        if price is None:
            price = df["Close"].iloc[-1]

        for period in self.periods:
            if f"EMA_{period}" not in df.columns:
                df = self.calculate(df)
                break

        inner_emas = [df[f"EMA_{p}"].iloc[-1] for p in self.INNER_PERIODS]
        outer_emas = [df[f"EMA_{p}"].iloc[-1] for p in self.OUTER_PERIODS]

        inner_min, inner_max = min(inner_emas), max(inner_emas)
        outer_min, outer_max = min(outer_emas), max(outer_emas)

        position = {
            "price": price,
            "inner_tunnel": (inner_min, inner_max),
            "outer_tunnel": (outer_min, outer_max),
            "location": "unknown",
            "distances": {}
        }

        # 判断位置
        if price > outer_max:
            position["location"] = "above_outer"
            position["distances"]["to_outer"] = (price - outer_max) / outer_max * 100
        elif price < outer_min:
            position["location"] = "below_outer"
            position["distances"]["to_outer"] = (outer_min - price) / outer_min * 100
        elif inner_min <= price <= inner_max:
            position["location"] = "inside_inner"
            center = (inner_min + inner_max) / 2
            position["distances"]["to_center"] = (price - center) / center * 100
        else:
            position["location"] = "between_tunnels"
            if price > inner_max:
                position["distances"]["to_inner"] = (price - inner_max) / inner_max * 100
            else:
                position["distances"]["to_inner"] = (inner_min - price) / inner_min * 100

        return position

    def generate_trading_signals(self, df: pd.DataFrame) -> List[Dict]:
        """
        生成交易信号列表

        Returns:
            List[Dict]: 信号列表
        """
        signals = []

        signal = self.analyze_trend(df)

        # 趋势信号
        if signal.trend == TrendDirection.BULLISH:
            signals.append({
                "type": "trend",
                "signal": "buy",
                "strength": signal.strength,
                "reason": "价格位于所有均线上方，趋势多头"
            })
        elif signal.trend == TrendDirection.BEARISH:
            signals.append({
                "type": "trend",
                "signal": "sell",
                "strength": 100 - signal.strength,
                "reason": "价格位于所有均线下方，趋势空头"
            })

        # 交叉信号
        if signal.crossover_signal == "golden_cross":
            signals.append({
                "type": "crossover",
                "signal": "buy",
                "strength": 80,
                "reason": "EMA金叉，短期均线上穿长期均线"
            })
        elif signal.crossover_signal == "death_cross":
            signals.append({
                "type": "crossover",
                "signal": "sell",
                "strength": 80,
                "reason": "EMA死叉，短期均线下穿长期均线"
            })

        return signals


# 便捷函数
def calculate_ema_signals(df: pd.DataFrame) -> EMATrendSignal:
    """便捷函数：计算EMA信号"""
    calculator = EMACalculator()
    return calculator.analyze_trend(df)


def add_ema_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """便捷函数：添加EMA指标"""
    calculator = EMACalculator()
    return calculator.calculate(df)
