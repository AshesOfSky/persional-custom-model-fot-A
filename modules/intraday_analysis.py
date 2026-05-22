"""
intraday_analysis.py — 超短线+短线趋势分析模块
支持分钟级数据获取和技术分析
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging
from .correlation_analysis import run_correlation_analysis, identify_asset_class

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IntradayAnalyzer:
    """日内趋势分析器"""

    # 时间周期配置
    TIMEFRAMES = {
        "超短线": {
            "period": "5m",  # 5分钟
            "lookback_days": 5,
            "description": "5分钟级别，适合日内交易",
            "ema_fast": 9,
            "ema_slow": 21,
        },
        "短线": {
            "period": "30m",  # 30分钟
            "lookback_days": 15,
            "description": "30分钟级别，适合短期波段",
            "ema_fast": 12,
            "ema_slow": 26,
        },
        "中短线": {
            "period": "60m",  # 60分钟
            "lookback_days": 30,
            "description": "60分钟级别，适合波段操作",
            "ema_fast": 12,
            "ema_slow": 26,
        },
    }

    def __init__(self, df_5m: Optional[pd.DataFrame] = None,
                 df_30m: Optional[pd.DataFrame] = None,
                 df_60m: Optional[pd.DataFrame] = None):
        """
        初始化分析器

        Args:
            df_5m: 5分钟K线数据
            df_30m: 30分钟K线数据
            df_60m: 60分钟K线数据
        """
        self.df_5m = df_5m
        self.df_30m = df_30m
        self.df_60m = df_60m

    def calculate_intraday_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算日内技术指标"""
        if df is None or df.empty:
            return df

        df = df.copy()

        # 确保数据按时间排序
        df = df.sort_index()

        # EMA (日内短周期)
        df['EMA9'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA21'] = df['Close'].ewm(span=21, adjust=False).mean()

        # MACD (日内参数)
        exp1 = df['Close'].ewm(span=12, adjust=False).mean()
        exp2 = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # RSI (短周期)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=9).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=9).mean()
        rs = gain / loss.replace(0, np.nan)
        df['RSI'] = 100 - (100 / (1 + rs))

        # 成交量指标
        df['Vol_MA'] = df['Volume'].rolling(window=20).mean()

        # 波动率 (ATR)
        high_low = df['High'] - df['Low']
        high_close = np.abs(df['High'] - df['Close'].shift())
        low_close = np.abs(df['Low'] - df['Close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['ATR'] = true_range.rolling(window=14).mean()

        # 布林带 (短周期)
        df['BB_Middle'] = df['Close'].rolling(window=20).mean()
        bb_std = df['Close'].rolling(window=20).std()
        df['BB_Upper'] = df['BB_Middle'] + (bb_std * 2)
        df['BB_Lower'] = df['BB_Middle'] - (bb_std * 2)

        # 日内支撑压力位 (基于近期高低点)
        df['Intraday_High'] = df['High'].rolling(window=20).max()
        df['Intraday_Low'] = df['Low'].rolling(window=20).min()
        df['Pivot'] = (df['Intraday_High'] + df['Intraday_Low'] + df['Close'].shift()) / 3

        return df

    def analyze_trend(self, df: pd.DataFrame, timeframe: str) -> Dict:
        """
        分析指定周期的趋势

        Args:
            df: K线数据
            timeframe: 时间周期名称

        Returns:
            趋势分析结果字典
        """
        if df is None or len(df) < 30:
            return {
                "timeframe": timeframe,
                "trend": "数据不足",
                "trend_score": 50,
                "signal": "观望",
            }

        df = self.calculate_intraday_indicators(df)
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last

        # 趋势判断
        price_above_ema9 = last['Close'] > last['EMA9']
        ema9_above_ema21 = last['EMA9'] > last['EMA21']
        macd_bullish = last['MACD'] > last['MACD_Signal']
        rsi_strong = 40 < last['RSI'] < 80

        # 趋势评分 (0-100)
        trend_score = 50
        trend_signals = []

        if price_above_ema9:
            trend_score += 15
            trend_signals.append("价格在EMA9之上")
        else:
            trend_score -= 15
            trend_signals.append("价格在EMA9之下")

        if ema9_above_ema21:
            trend_score += 15
            trend_signals.append("EMA金叉排列")
        else:
            trend_score -= 15
            trend_signals.append("EMA死叉排列")

        if macd_bullish:
            trend_score += 10
            trend_signals.append("MACD看涨")
        else:
            trend_score -= 10
            trend_signals.append("MACD看跌")

        if rsi_strong:
            trend_score += 5

        # 成交量分析
        vol_signal = ""
        if last['Volume'] > last['Vol_MA'] * 1.5:
            if last['Close'] > prev['Close']:
                vol_signal = "放量上涨"
                trend_score += 5
            else:
                vol_signal = "放量下跌"
                trend_score -= 5

        # 布林带位置
        bb_position = ""
        if last['Close'] > last['BB_Upper']:
            bb_position = "突破上轨"
        elif last['Close'] < last['BB_Lower']:
            bb_position = "跌破下轨"
        elif last['Close'] > last['BB_Middle']:
            bb_position = "中轨上方"
        else:
            bb_position = "中轨下方"

        # 综合趋势判断
        if trend_score >= 70:
            trend = "强势多头"
            signal = "做多"
            signal_color = "#ff4d4f"
        elif trend_score >= 55:
            trend = "多头"
            signal = "偏多"
            signal_color = "#ff7b72"
        elif trend_score <= 30:
            trend = "强势空头"
            signal = "做空"
            signal_color = "#00b578"
        elif trend_score <= 45:
            trend = "空头"
            signal = "偏空"
            signal_color = "#7ee787"
        else:
            trend = "震荡"
            signal = "观望"
            signal_color = "#8b949e"

        # 计算支撑压力位
        support = last['BB_Lower'] if not pd.isna(last['BB_Lower']) else last['Intraday_Low']
        resistance = last['BB_Upper'] if not pd.isna(last['BB_Upper']) else last['Intraday_High']

        return {
            "timeframe": timeframe,
            "trend": trend,
            "trend_score": trend_score,
            "signal": signal,
            "signal_color": signal_color,
            "current_price": round(last['Close'], 2),
            "ema9": round(last['EMA9'], 2) if not pd.isna(last['EMA9']) else None,
            "ema21": round(last['EMA21'], 2) if not pd.isna(last['EMA21']) else None,
            "macd": round(last['MACD'], 4) if not pd.isna(last['MACD']) else None,
            "rsi": round(last['RSI'], 1) if not pd.isna(last['RSI']) else None,
            "support": round(support, 2),
            "resistance": round(resistance, 2),
            "bb_position": bb_position,
            "volume_signal": vol_signal,
            "atr": round(last['ATR'], 2) if not pd.isna(last['ATR']) else None,
            "signals": trend_signals,
        }

    def get_comprehensive_analysis(self, ticker: str = None, fetch_data_func=None) -> Dict:
        """获取综合日内分析（含关联资产分析）"""
        analysis_5m = self.analyze_trend(self.df_5m, "超短线(5分钟)") if self.df_5m is not None else None
        analysis_30m = self.analyze_trend(self.df_30m, "短线(30分钟)") if self.df_30m is not None else None
        analysis_60m = self.analyze_trend(self.df_60m, "中短线(60分钟)") if self.df_60m is not None else None

        # 多周期共振分析
        multi_timeframe_signal = self._analyze_multi_timeframe(
            analysis_5m, analysis_30m, analysis_60m
        )

        result = {
            "超短线": analysis_5m,
            "短线": analysis_30m,
            "中短线": analysis_60m,
            "多周期共振": multi_timeframe_signal,
        }

        # 跨资产关联分析
        if ticker:
            try:
                # 用60分钟数据或30分钟数据做关联分析
                primary_df = self.df_60m if self.df_60m is not None else self.df_30m
                corr_result = run_correlation_analysis(
                    ticker, primary_df, fetch_data_func
                )
                result["关联分析"] = corr_result

                # 关联分析信号纳入多周期共振评分
                if corr_result.get("bullish_signals", 0) > corr_result.get("bearish_signals", 0):
                    if multi_timeframe_signal.get("confidence"):
                        multi_timeframe_signal["confidence"] = min(
                            95, multi_timeframe_signal["confidence"] + 5
                        )
                    multi_timeframe_signal["description"] += " (关联资产利多)"
                elif corr_result.get("bearish_signals", 0) > corr_result.get("bullish_signals", 0):
                    if multi_timeframe_signal.get("confidence"):
                        multi_timeframe_signal["confidence"] = max(
                            0, multi_timeframe_signal["confidence"] - 5
                        )
                    multi_timeframe_signal["description"] += " (关联资产利空)"
            except Exception as e:
                logger.warning(f"关联分析失败: {e}")
                result["关联分析"] = {"error": str(e)}

        return result

    def _analyze_multi_timeframe(self, analysis_5m: Dict, analysis_30m: Dict, analysis_60m: Dict) -> Dict:
        """多周期共振分析"""
        signals = []
        scores = []

        for analysis in [analysis_5m, analysis_30m, analysis_60m]:
            if analysis and analysis.get("signal"):
                signals.append(analysis["signal"])
                scores.append(analysis.get("trend_score", 50))

        if not signals:
            return {"signal": "观望", "confidence": 0, "description": "数据不足"}

        # 统计多头/空头信号数量
        bullish_signals = sum(1 for s in signals if s in ["做多", "偏多"])
        bearish_signals = sum(1 for s in signals if s in ["做空", "偏空"])
        neutral_signals = len(signals) - bullish_signals - bearish_signals

        # 计算平均趋势分
        avg_score = sum(scores) / len(scores)

        # 判断共振方向
        if bullish_signals >= 2 and bearish_signals == 0:
            signal = "强烈做多"
            confidence = min(95, 70 + bullish_signals * 10)
            description = f"{bullish_signals}个周期看多共振，建议积极做多"
        elif bullish_signals >= 1 and neutral_signals >= 1:
            signal = "偏多"
            confidence = min(75, 60 + bullish_signals * 8)
            description = "短期偏多，注意30分钟级别确认"
        elif bearish_signals >= 2 and bullish_signals == 0:
            signal = "强烈做空"
            confidence = min(95, 70 + bearish_signals * 10)
            description = f"{bearish_signals}个周期看空共振，建议减仓避险"
        elif bearish_signals >= 1 and neutral_signals >= 1:
            signal = "偏空"
            confidence = min(75, 60 + bearish_signals * 8)
            description = "短期偏空，等待企稳信号"
        else:
            signal = "观望"
            confidence = 50
            description = "各周期信号不一致，建议观望"

        return {
            "signal": signal,
            "confidence": confidence,
            "avg_score": round(avg_score, 1),
            "description": description,
            "timeframe_signals": signals,
        }

    def generate_trading_advice(self) -> str:
        """生成交易建议"""
        analysis = self.get_comprehensive_analysis()
        resonance = analysis.get("多周期共振", {})

        advice = []

        # 超短线建议
        if analysis.get("超短线"):
            s = analysis["超短线"]
            advice.append(f"【超短线】{s['signal']} - {s['trend']} (评分: {s['trend_score']})")
            if s.get('support') and s.get('resistance'):
                advice.append(f"  支撑: {s['support']} 阻力: {s['resistance']}")

        # 短线建议
        if analysis.get("短线"):
            s = analysis["短线"]
            advice.append(f"【短线】{s['signal']} - {s['trend']} (评分: {s['trend_score']})")

        # 共振建议
        advice.append(f"\n【多周期共振】{resonance.get('signal', '观望')}")
        advice.append(f"置信度: {resonance.get('confidence', 0)}%")
        advice.append(f"建议: {resonance.get('description', '')}")

        return "\n".join(advice)


def fetch_intraday_data(ticker: str, period: str = "5d", interval: str = "5m") -> Optional[pd.DataFrame]:
    """
    获取日内数据 (需要通过yfinance或其他源)

    Args:
        ticker: 股票代码
        period: 数据周期
        interval: 数据间隔

    Returns:
        DataFrame or None
    """
    try:
        import yfinance as yf

        # 下载分钟数据
        data = yf.download(
            ticker,
            period=period,
            interval=interval,
            progress=False,
            threads=False
        )

        if data.empty:
            return None

        # 处理多级列名
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        # 标准化列名
        data = data.rename(columns={
            'Open': 'Open',
            'High': 'High',
            'Low': 'Low',
            'Close': 'Close',
            'Volume': 'Volume'
        })

        return data

    except Exception as e:
        logger.error(f"获取日内数据失败 {ticker}: {e}")
        return None


def run_intraday_analysis(ticker: str, fetch_data_func=None) -> Dict:
    """
    运行完整的日内分析（含跨资产关联分析）

    Args:
        ticker: 股票代码
        fetch_data_func: 获取关联资产数据的函数 (可选)

    Returns:
        分析结果字典
    """
    # 获取不同周期的数据
    df_5m = fetch_intraday_data(ticker, period="5d", interval="5m")
    df_30m = fetch_intraday_data(ticker, period="1mo", interval="30m")
    df_60m = fetch_intraday_data(ticker, period="1mo", interval="60m")

    # 创建分析器
    analyzer = IntradayAnalyzer(df_5m, df_30m, df_60m)

    # 获取分析结果（含关联分析）
    return analyzer.get_comprehensive_analysis(ticker=ticker, fetch_data_func=fetch_data_func)
