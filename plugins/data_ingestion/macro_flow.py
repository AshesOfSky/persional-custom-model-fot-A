"""
宏观与资金流向插件
获取无风险利率、外资流入、行业景气度等宏观指标
"""

import akshare as ak
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class MacroIndicator:
    """宏观指标数据结构"""
    name: str
    value: float
    unit: str
    date: datetime
    change_pct: Optional[float] = None
    trend: str = "stable"  # up, down, stable


@dataclass
class CapitalFlow:
    """资金流向数据结构"""
    date: datetime
    northbound_net: float  # 北向资金净流入
    southbound_net: float  # 南向资金净流入
    main_force_net: float  # 主力资金净流入
    retail_net: float  # 散户资金净流入


class MacroFlowPlugin:
    """
    宏观与资金流向插件
    提供市场流动性和宏观环境数据
    """

    def __init__(self):
        self._cache = {}
        self._cache_ttl = 600  # 10分钟缓存

    def get_risk_free_rate(self, market: str = "CN") -> MacroIndicator:
        """
        获取无风险利率

        Args:
            market: CN (中国) / US (美国)

        Returns:
            MacroIndicator: 利率指标
        """
        try:
            if market == "CN":
                # 中国10年期国债收益率
                df = ak.bond_zh_us_rate()
                if not df.empty:
                    latest = df.iloc[-1]
                    rate = latest.get('中国国债收益率10年', latest.get('中国', 0))
                    prev_rate = df.iloc[-2].get('中国国债收益率10年', rate) if len(df) > 1 else rate

                    change_pct = ((rate - prev_rate) / prev_rate * 100) if prev_rate else 0
                    trend = "up" if change_pct > 0.1 else ("down" if change_pct < -0.1 else "stable")

                    return MacroIndicator(
                        name="中国10年期国债收益率",
                        value=rate,
                        unit="%",
                        date=pd.to_datetime(latest.get('日期', datetime.now())),
                        change_pct=change_pct,
                        trend=trend
                    )

            elif market == "US":
                # 美国10年期国债收益率 (通过akshare)
                df = ak.bond_zh_us_rate()
                if not df.empty:
                    latest = df.iloc[-1]
                    rate = latest.get('美国国债收益率10年', latest.get('美国', 0))
                    prev_rate = df.iloc[-2].get('美国国债收益率10年', rate) if len(df) > 1 else rate

                    change_pct = ((rate - prev_rate) / prev_rate * 100) if prev_rate else 0
                    trend = "up" if change_pct > 0.1 else ("down" if change_pct < -0.1 else "stable")

                    return MacroIndicator(
                        name="美国10年期国债收益率",
                        value=rate,
                        unit="%",
                        date=pd.to_datetime(latest.get('日期', datetime.now())),
                        change_pct=change_pct,
                        trend=trend
                    )

        except Exception as e:
            logger.error(f"Failed to get risk-free rate for {market}: {e}")

        # 返回默认值
        default_rate = 2.5 if market == "CN" else 4.0
        return MacroIndicator(
            name=f"{'中国' if market == 'CN' else '美国'}10年期国债收益率",
            value=default_rate,
            unit="%",
            date=datetime.now(),
        )

    def get_northbound_flow(self, days: int = 30) -> pd.DataFrame:
        """
        获取北向资金流向

        Args:
            days: 获取天数

        Returns:
            DataFrame: 资金流向数据
        """
        try:
            # 使用akshare获取北向资金
            df = ak.stock_hsgt_hist_em(symbol="沪股通")

            if df is not None and not df.empty:
                df = df.rename(columns={
                    "日期": "date",
                    "当日资金流入": "inflow",
                    "当日成交净买额": "net_buy",
                })
                df['date'] = pd.to_datetime(df['date'])
                df = df.sort_values('date', ascending=False).head(days)
                return df

        except Exception as e:
            logger.error(f"Failed to get northbound flow: {e}")

        return pd.DataFrame()

    def get_sector_capital_flow(self) -> pd.DataFrame:
        """
        获取行业资金流向

        Returns:
            DataFrame: 行业资金流向数据
        """
        try:
            # 获取行业资金净流入排行
            df = ak.stock_sector_fund_flow_rank(indicator="今日")

            if df is not None and not df.empty:
                return df

        except Exception as e:
            logger.error(f"Failed to get sector capital flow: {e}")

        return pd.DataFrame()

    def get_industry_sentiment(self) -> Dict[str, MacroIndicator]:
        """
        获取行业景气度指标

        Returns:
            Dict[str, MacroIndicator]: 各行业景气度
        """
        try:
            # 获取行业涨跌幅作为景气度代理
            df = ak.stock_sector_detail(symbol="行业板块")

            indicators = {}
            if df is not None and not df.empty:
                for _, row in df.head(10).iterrows():
                    name = row.get('名称', '')
                    change = row.get('涨跌幅', 0)

                    indicators[name] = MacroIndicator(
                        name=f"{name}板块",
                        value=change,
                        unit="%",
                        date=datetime.now(),
                        trend="up" if change > 0 else "down"
                    )

            return indicators

        except Exception as e:
            logger.error(f"Failed to get industry sentiment: {e}")
            return {}

    def analyze_market_liquidity(self) -> Dict:
        """
        分析市场流动性状况

        Returns:
            Dict: 流动性评分和指标
        """
        analysis = {
            "score": 50,
            "status": "中性",
            "indicators": {},
        }

        try:
            # 北向资金近期流向
            northbound = self.get_northbound_flow(days=5)
            if not northbound.empty:
                recent_net = northbound['net_buy'].sum() if 'net_buy' in northbound.columns else 0
                analysis["indicators"]["northbound_5d"] = recent_net

                if recent_net > 100:  # 100亿以上流入
                    analysis["score"] += 15
                elif recent_net < -100:  # 100亿以上流出
                    analysis["score"] -= 15

            # 无风险利率
            cn_rate = self.get_risk_free_rate("CN")
            us_rate = self.get_risk_free_rate("US")

            analysis["indicators"]["cn_10y_rate"] = cn_rate.value
            analysis["indicators"]["us_10y_rate"] = us_rate.value

            # 利率趋势
            if cn_rate.trend == "down":  # 降息周期，流动性改善
                analysis["score"] += 10
            elif cn_rate.trend == "up":  # 加息周期，流动性收紧
                analysis["score"] -= 10

            # 评分区间
            if analysis["score"] >= 65:
                analysis["status"] = "流动性充裕"
            elif analysis["score"] >= 45:
                analysis["status"] = "流动性中性"
            else:
                analysis["status"] = "流动性偏紧"

        except Exception as e:
            logger.error(f"Failed to analyze market liquidity: {e}")

        return analysis

    def get_comprehensive_macro_view(self) -> Dict:
        """
        获取综合宏观视角

        Returns:
            Dict: 包含利率、汇率、资金流向的综合视图
        """
        return {
            "risk_free_rates": {
                "CN": self.get_risk_free_rate("CN"),
                "US": self.get_risk_free_rate("US"),
            },
            "liquidity": self.analyze_market_liquidity(),
            "sector_flow": self.get_sector_capital_flow(),
            "industry_sentiment": self.get_industry_sentiment(),
            "update_time": datetime.now().isoformat(),
        }


# 便捷函数
def get_market_liquidity_summary() -> Dict:
    """便捷函数：获取市场流动性摘要"""
    plugin = MacroFlowPlugin()
    return plugin.analyze_market_liquidity()


def get_current_rates() -> Dict[str, float]:
    """便捷函数：获取当前利率"""
    plugin = MacroFlowPlugin()
    cn_rate = plugin.get_risk_free_rate("CN")
    us_rate = plugin.get_risk_free_rate("US")
    return {
        "CN_10Y": cn_rate.value,
        "US_10Y": us_rate.value,
        "spread": us_rate.value - cn_rate.value,
    }
