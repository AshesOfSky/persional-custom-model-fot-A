"""
info_analysis.py — 信息面分析模块
整合基本面、资金面、市场情绪、宏观环境等多维度信息
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime


class InfoAnalyzer:
    """信息面综合分析器"""

    def __init__(self, ticker: str, info: dict, df: pd.DataFrame, fundamental: dict):
        self.ticker = ticker
        self.info = info
        self.df = df
        self.fundamental = fundamental

    def analyze_fundamental(self) -> Dict:
        """
        基本面信息面分析
        """
        result = {
            "valuation_score": 50,  # 估值评分 0-100
            "profitability_score": 50,
            "health_score": 50,
            "growth_score": 50,
            "comments": [],
        }

        # 估值分析
        pe = self.fundamental.get("PE")
        pb = self.fundamental.get("PB")

        if pe is not None:
            if pe < 10:
                result["valuation_score"] = 80
                result["comments"].append(f"PE({pe:.1f})较低，估值有吸引力")
            elif pe < 20:
                result["valuation_score"] = 60
                result["comments"].append(f"PE({pe:.1f})合理")
            elif pe < 30:
                result["valuation_score"] = 40
                result["comments"].append(f"PE({pe:.1f})偏高")
            else:
                result["valuation_score"] = 20
                result["comments"].append(f"PE({pe:.1f})过高，注意估值风险")

        if pb is not None:
            if pb < 1:
                result["comments"].append(f"PB({pb:.2f})<1，破净状态")
            elif pb > 5:
                result["comments"].append(f"PB({pb:.2f})较高")

        # 盈利能力分析
        roe = self.fundamental.get("ROE")
        net_margin = self.fundamental.get("net_margin")

        if roe is not None:
            if roe > 0.15:
                result["profitability_score"] = 85
                result["comments"].append(f"ROE({roe*100:.1f}%)优秀")
            elif roe > 0.10:
                result["profitability_score"] = 65
                result["comments"].append(f"ROE({roe*100:.1f}%)良好")
            elif roe > 0.05:
                result["profitability_score"] = 45
            else:
                result["profitability_score"] = 25
                result["comments"].append(f"ROE({roe*100:.1f}%)偏低")

        if net_margin is not None:
            if net_margin > 0.20:
                result["comments"].append(f"净利率({net_margin*100:.1f}%)很高")
            elif net_margin < 0.05:
                result["comments"].append(f"净利率({net_margin*100:.1f}%)较低")

        # 成长性分析
        rev_growth = self.fundamental.get("rev_growth")
        if rev_growth is not None:
            if rev_growth > 0.30:
                result["growth_score"] = 85
                result["comments"].append(f"营收增速({rev_growth*100:.1f}%)高增长")
            elif rev_growth > 0.15:
                result["growth_score"] = 65
                result["comments"].append(f"营收增速({rev_growth*100:.1f}%)稳健增长")
            elif rev_growth > 0:
                result["growth_score"] = 45
            else:
                result["growth_score"] = 25
                result["comments"].append(f"营收增速({rev_growth*100:.1f}%)下滑")

        # 财务健康
        debt_ratio = self.fundamental.get("debt_ratio")
        if debt_ratio is not None:
            if debt_ratio < 0.4:
                result["health_score"] = 75
                result["comments"].append("资产负债率低，财务稳健")
            elif debt_ratio > 0.7:
                result["health_score"] = 35
                result["comments"].append("资产负债率高，注意财务风险")
            else:
                result["health_score"] = 55

        # 综合基本面评分
        result["overall_score"] = (
            result["valuation_score"] * 0.25 +
            result["profitability_score"] * 0.30 +
            result["health_score"] * 0.25 +
            result["growth_score"] * 0.20
        )

        # 基本面评级
        if result["overall_score"] >= 70:
            result["rating"] = "优秀"
            result["rating_color"] = "#0ECB81"
        elif result["overall_score"] >= 55:
            result["rating"] = "良好"
            result["rating_color"] = "#52B788"
        elif result["overall_score"] >= 40:
            result["rating"] = "一般"
            result["rating_color"] = "#FFD700"
        else:
            result["rating"] = "较弱"
            result["rating_color"] = "#F6465D"

        return result

    def analyze_capital_flow(self) -> Dict:
        """
        资金面分析
        """
        result = {
            "flow_score": 50,
            "comments": [],
        }

        if self.df.empty or "Volume" not in self.df.columns:
            return result

        last = self.df.iloc[-1]

        # 成交量趋势
        vol = last.get("Volume", 0)
        vol_ma5 = last.get("Vol_MA_5", vol)
        vol_ma20 = last.get("Vol_MA_20", vol)

        if vol_ma20 > 0:
            vol_ratio = vol / vol_ma20
            if vol_ratio > 2.0:
                result["flow_score"] += 20
                result["comments"].append(f"成交量放量({vol_ratio:.1f}倍)，资金活跃")
            elif vol_ratio > 1.5:
                result["flow_score"] += 10
                result["comments"].append(f"成交量温和放大({vol_ratio:.1f}倍)")
            elif vol_ratio < 0.5:
                result["flow_score"] -= 15
                result["comments"].append(f"成交量萎缩({vol_ratio:.1f}倍)，交投清淡")

        # OBV趋势
        if "OBV" in self.df.columns and "OBV_EMA" in self.df.columns:
            obv = last.get("OBV", 0)
            obv_ema = last.get("OBV_EMA", obv)
            if obv > obv_ema:
                result["flow_score"] += 10
                result["comments"].append("OBV上升，资金净流入")
            else:
                result["flow_score"] -= 5
                result["comments"].append("OBV下降，资金净流出")

        # MFI分析
        if "MFI" in self.df.columns or "mfi" in dir(self):
            mfi = last.get("MFI", 50)
            if mfi > 80:
                result["comments"].append("MFI超买，资金过度涌入")
            elif mfi < 20:
                result["comments"].append("MFI超卖，资金极度悲观")

        # 价格与成交量关系
        close = last.get("Close", 0)
        open_price = last.get("Open", close)
        price_change = (close - open_price) / open_price if open_price else 0

        if price_change > 0.03 and vol_ratio > 1.5:
            result["comments"].append("放量上涨，资金做多积极")
        elif price_change < -0.03 and vol_ratio > 1.5:
            result["comments"].append("放量下跌，资金出逃明显")

        # 资金流向评级
        if result["flow_score"] >= 70:
            result["flow_rating"] = "资金流入"
            result["flow_color"] = "#0ECB81"
        elif result["flow_score"] >= 50:
            result["flow_rating"] = "资金平稳"
            result["flow_color"] = "#FFD700"
        else:
            result["flow_rating"] = "资金流出"
            result["flow_color"] = "#F6465D"

        return result

    def analyze_market_sentiment(self, tech_analysis: Dict) -> Dict:
        """
        市场情绪分析（基于技术指标）
        """
        result = {
            "sentiment_score": 50,
            "sentiment": "中性",
            "comments": [],
        }

        # 趋势情绪
        trend = tech_analysis.get("trend", "震荡")
        if trend == "多头":
            result["sentiment_score"] += 20
            result["comments"].append("技术面多头，市场情绪乐观")
        elif trend == "空头":
            result["sentiment_score"] -= 20
            result["comments"].append("技术面空头，市场情绪悲观")
        else:
            result["comments"].append("技术面震荡，市场情绪观望")

        # 共振信号
        resonance = tech_analysis.get("resonance", "")
        if "强烈买入" in resonance:
            result["sentiment_score"] += 15
            result["comments"].append("多指标强烈买入共振")
        elif "买入共振" in resonance:
            result["sentiment_score"] += 10
            result["comments"].append("多指标买入共振")
        elif "强烈卖出" in resonance:
            result["sentiment_score"] -= 15
            result["comments"].append("多指标强烈卖出共振")
        elif "卖出共振" in resonance:
            result["sentiment_score"] -= 10
            result["comments"].append("多指标卖出共振")

        # 波动率情绪
        if "Volatility" in self.df.columns:
            vol = self.df["Volatility"].iloc[-1]
            if vol > 50:
                result["comments"].append(f"波动率较高({vol:.1f}%)，市场情绪不稳定")
            elif vol < 20:
                result["comments"].append(f"波动率较低({vol:.1f}%)，市场情绪平稳")

        # 综合情绪
        if result["sentiment_score"] >= 70:
            result["sentiment"] = "乐观"
            result["sentiment_color"] = "#0ECB81"
        elif result["sentiment_score"] >= 55:
            result["sentiment"] = "偏多"
            result["sentiment_color"] = "#52B788"
        elif result["sentiment_score"] >= 45:
            result["sentiment"] = "中性"
            result["sentiment_color"] = "#FFD700"
        elif result["sentiment_score"] >= 30:
            result["sentiment"] = "偏空"
            result["sentiment_color"] = "#FFA500"
        else:
            result["sentiment"] = "悲观"
            result["sentiment_color"] = "#F6465D"

        return result

    def analyze_company_events(self) -> Dict:
        """
        公司事件分析（基于yfinance数据）
        """
        result = {
            "events": [],
            "risk_factors": [],
        }

        # 从info中提取公司信息
        sector = self.info.get("sector", "")
        industry = self.info.get("industry", "")

        result["sector"] = sector
        result["industry"] = industry

        # 分析行业地位
        market_cap = self.fundamental.get("market_cap")
        if market_cap:
            if market_cap > 1e12:  # 万亿
                result["market_position"] = "行业龙头"
            elif market_cap > 1e11:  # 千亿
                result["market_position"] = "大型蓝筹"
            elif market_cap > 1e10:  # 百亿
                result["market_position"] = "中型成长"
            else:
                result["market_position"] = "小型公司"
        else:
            result["market_position"] = "未知"

        # 分红情况
        dividend_yield = self.fundamental.get("dividend_yield")
        if dividend_yield and dividend_yield > 0:
            if dividend_yield > 0.03:
                result["events"].append(f"高分红股票，股息率{dividend_yield*100:.2f}%")
            else:
                result["events"].append(f"有分红，股息率{dividend_yield*100:.2f}%")

        # 52周位置
        high_52w = self.fundamental.get("52w_high")
        low_52w = self.fundamental.get("52w_low")
        current = self.fundamental.get("current_price")

        if all([high_52w, low_52w, current]) and high_52w > low_52w:
            position = (current - low_52w) / (high_52w - low_52w)
            result["52w_position"] = position
            if position > 0.9:
                result["risk_factors"].append("股价接近52周高点，注意回调风险")
            elif position < 0.1:
                result["events"].append("股价接近52周低点，可能存在反弹机会")

        return result

    def generate_info_report(self, tech_analysis: Dict) -> Dict:
        """
        生成完整的信息面分析报告
        """
        fundamental_analysis = self.analyze_fundamental()
        capital_flow = self.analyze_capital_flow()
        sentiment = self.analyze_market_sentiment(tech_analysis)
        events = self.analyze_company_events()

        # 综合评分
        market_pos = events.get("market_position", "未知")
        position_score = 50 if market_pos in ["行业龙头", "大型蓝筹"] else (45 if market_pos == "中型成长" else 40)
        overall_score = (
            fundamental_analysis["overall_score"] * 0.35 +
            capital_flow["flow_score"] * 0.25 +
            sentiment["sentiment_score"] * 0.25 +
            position_score * 0.15
        )

        # 综合评级
        if overall_score >= 70:
            overall_rating = "看好"
            overall_color = "#0ECB81"
        elif overall_score >= 55:
            overall_rating = "偏积极"
            overall_color = "#52B788"
        elif overall_score >= 40:
            overall_rating = "中性"
            overall_color = "#FFD700"
        else:
            overall_rating = "谨慎"
            overall_color = "#F6465D"

        # 投资建议
        if fundamental_analysis["overall_score"] >= 60 and capital_flow["flow_score"] >= 60:
            investment_advice = "基本面良好且资金流入，可考虑配置"
        elif fundamental_analysis["overall_score"] >= 60 and capital_flow["flow_score"] < 40:
            investment_advice = "基本面良好但资金流出，建议观望"
        elif fundamental_analysis["overall_score"] < 40 and capital_flow["flow_score"] >= 60:
            investment_advice = "资金流入但基本面较弱，谨慎参与"
        else:
            investment_advice = "基本面和资金面均较弱，建议回避"

        return {
            "ticker": self.ticker,
            "analysis_date": datetime.now().strftime("%Y-%m-%d"),
            "fundamental": fundamental_analysis,
            "capital_flow": capital_flow,
            "sentiment": sentiment,
            "events": events,
            "overall_score": round(overall_score, 1),
            "overall_rating": overall_rating,
            "overall_color": overall_color,
            "investment_advice": investment_advice,
        }


def run_info_analysis(ticker: str, info: dict, df: pd.DataFrame, fundamental: dict, tech_analysis: Dict) -> Dict:
    """
    便捷函数：运行完整的信息面分析
    """
    analyzer = InfoAnalyzer(ticker, info, df, fundamental)
    return analyzer.generate_info_report(tech_analysis)
