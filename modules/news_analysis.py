"""
news_analysis.py — 消息面分析模块
整合新闻、公告、研报等多维度信息
"""

import akshare as ak
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
import logging
import re
import requests
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NewsAnalyzer:
    """消息面分析器"""

    # 情感词典
    SENTIMENT_WORDS = {
        "positive": [
            "上涨", "增长", "突破", "利好", "增持", "回购", "分红", "业绩预增",
            "订单", "合作", "中标", "获批", "创新", "领先", "龙头", "优势",
            "扩张", "并购", "重组", "转型", "升级", "优化", "改善", "提升",
            "涨停", "大涨", "强势", "反弹", "企稳", "回暖", "向好", "乐观",
            "超预期", "创历史新高"
        ],
        "negative": [
            "下跌", "下滑", "亏损", "减持", "解禁", "质押", "违约", "诉讼",
            "监管", "处罚", "调查", "退市", "风险", "警示", "违规", "丑闻",
            "裁员", "关闭", "停产", "滞销", "库存", "积压", "贬值", "减值",
            "跌停", "大跌", "暴跌", "弱势", "破位", "下行", "低迷", "悲观",
            "低于预期", "业绩变脸", "财务造假", "资金链", "断裂"
        ]
    }

    def __init__(self, ticker: str, name: str = ""):
        self.ticker = ticker
        self.name = name
        self.code = ticker.split('.')[0] if '.' in ticker else ticker
        self._session = requests.Session()
        self._session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def _is_futures(self) -> bool:
        """检查是否是期货代码"""
        import re
        t = self.ticker.upper()
        # 美股期货格式：GC=F, CL=F 等
        if t.endswith("=F"):
            return True
        # 期货月代码格式：GC00Y, CL25H 等
        if re.match(r'^[A-Z]{2,4}\d{2}[A-Z]$', t):
            return True
        # 国内期货主力连续：AU0, RB0 等
        if re.match(r'^[A-Z]{1,2}\d$', t):
            return True
        return False

    def fetch_news(self, days: int = 7) -> List[Dict]:
        """获取股票相关新闻 - 多数据源备用"""
        # 期货代码不获取新闻
        if self._is_futures():
            logger.info(f"{self.ticker} 是期货代码，跳过新闻获取")
            return []

        news_list = []

        # 尝试东方财富
        try:
            df = ak.stock_news_em(symbol=self.code)
            if not df.empty:
                for _, row in df.head(20).iterrows():
                    news_list.append({
                        "title": str(row.get("标题", "")),
                        "content": str(row.get("内容", "")),
                        "time": str(row.get("发布时间", "")),
                        "source": str(row.get("来源", "东方财富")),
                    })
                return news_list
        except Exception as e:
            logger.warning(f"东方财富新闻获取失败: {e}")

        # 尝试新浪财经
        try:
            news_list = self._fetch_sina_news()
            if news_list:
                return news_list
        except Exception as e:
            logger.warning(f"新浪新闻获取失败: {e}")

        # 使用模拟数据作为最后备选
        if not news_list:
            news_list = self._generate_mock_news()

        return news_list

    def _fetch_sina_news(self) -> List[Dict]:
        """从新浪财经获取新闻"""
        news_list = []
        try:
            # 构建新浪财经新闻URL
            url = f"https://search.sina.com.cn/?q={self.code}&c=news&from=channel&ie=utf-8"
            # 新浪新闻需要解析HTML，简化处理
            return []
        except Exception:
            return []

    def _generate_mock_news(self) -> List[Dict]:
        """生成模拟新闻数据"""
        return [
            {
                "title": f"{self.name}({self.code}) 暂无实时新闻数据",
                "content": "当前无法获取实时新闻，请稍后重试或检查网络连接。",
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "source": "系统提示",
            }
        ]

    def fetch_announcements(self) -> List[Dict]:
        """获取公司公告"""
        # 期货代码不获取公告
        if self._is_futures():
            return []

        announcements = []

        try:
            df = ak.stock_notice_report(symbol=self.code)
            if not df.empty:
                for _, row in df.head(10).iterrows():
                    announcements.append({
                        "title": str(row.get("公告标题", "")),
                        "type": str(row.get("公告类型", "")),
                        "time": str(row.get("公告时间", "")),
                    })
        except Exception as e:
            logger.warning(f"获取公告失败 {self.code}: {e}")
            # 返回空列表

        return announcements

    def fetch_research_reports(self) -> List[Dict]:
        """获取研报信息"""
        # 期货代码不获取研报
        if self._is_futures():
            return []

        reports = []

        try:
            # 获取研报
            df = ak.stock_research_report_em(symbol=self.code)
            if not df.empty:
                for _, row in df.head(10).iterrows():
                    reports.append({
                        "title": str(row.get("报告标题", "")),
                        "org": str(row.get("机构名称", "")),
                        "rating": str(row.get("投资评级", "")),
                        "target_price": str(row.get("目标价格", "")),
                        "time": str(row.get("发布日期", "")),
                    })
        except Exception as e:
            logger.error(f"获取研报失败 {self.code}: {e}")

        return reports

    def analyze_sentiment(self, text: str) -> Dict:
        """分析文本情感"""
        if not text:
            return {"score": 0, "label": "中性"}

        text = text.lower()

        positive_count = sum(1 for word in self.SENTIMENT_WORDS["positive"] if word in text)
        negative_count = sum(1 for word in self.SENTIMENT_WORDS["negative"] if word in text)

        # 计算情感得分
        total = positive_count + negative_count
        if total == 0:
            return {"score": 0, "label": "中性"}

        score = (positive_count - negative_count) / total * 100

        if score > 30:
            label = "积极"
        elif score > 10:
            label = "偏积极"
        elif score < -30:
            label = "消极"
        elif score < -10:
            label = "偏消极"
        else:
            label = "中性"

        return {
            "score": round(score, 1),
            "label": label,
            "positive_count": positive_count,
            "negative_count": negative_count,
        }

    def analyze_news_impact(self, news: Dict) -> Dict:
        """分析单条新闻影响"""
        title_sentiment = self.analyze_sentiment(news.get("title", ""))
        content_sentiment = self.analyze_sentiment(news.get("content", ""))

        # 标题权重更高
        combined_score = title_sentiment["score"] * 0.6 + content_sentiment["score"] * 0.4

        # 判断影响级别
        if abs(combined_score) > 50:
            impact = "重大影响"
        elif abs(combined_score) > 25:
            impact = "较大影响"
        elif abs(combined_score) > 10:
            impact = "一般影响"
        else:
            impact = "轻微影响"

        return {
            "title": news.get("title", ""),
            "time": news.get("time", ""),
            "source": news.get("source", ""),
            "sentiment_score": round(combined_score, 1),
            "sentiment_label": title_sentiment["label"],
            "impact": impact,
        }

    def fetch_fund_flow(self) -> Dict:
        """获取个股资金流向"""
        if self._is_futures():
            return {}
        try:
            df = ak.stock_individual_fund_flow(stock=self.code, market="sh" if self.code.startswith("6") else "sz")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                # 尝试解析常见列名
                net_flow = 0
                for col in df.columns:
                    if "主力净流入" in str(col) and "净额" in str(col):
                        net_flow = pd.to_numeric(latest.get(col, 0), errors="coerce") or 0
                        break

                net_5d = 0
                if len(df) >= 5:
                    for col in df.columns:
                        if "主力净流入" in str(col) and "净额" in str(col):
                            net_5d = pd.to_numeric(df.tail(5)[col], errors="coerce").sum()
                            break

                main_pct = 0
                for col in df.columns:
                    if "主力净流入" in str(col) and "净占比" in str(col):
                        main_pct = pd.to_numeric(latest.get(col, 0), errors="coerce") or 0
                        break

                return {
                    "net_flow_today": net_flow,
                    "net_flow_5d": net_5d,
                    "main_force_net_pct": main_pct,
                }
        except Exception as e:
            logger.warning(f"获取资金流向失败 {self.code}: {e}")
        return {}

    def fetch_comment_score(self) -> Dict:
        """获取千股千评（机构综合评分）"""
        if self._is_futures():
            return {}
        try:
            df = ak.stock_comment_em()
            if df is not None and not df.empty:
                # 筛选当前股票
                row = df[df["代码"].astype(str) == self.code]
                if not row.empty:
                    row = row.iloc[0]
                    return {
                        "综合评分": pd.to_numeric(row.get("综合评分", 0), errors="coerce") or 0,
                        "关注指数": pd.to_numeric(row.get("关注指数", 0), errors="coerce") or 0,
                        "评级": str(row.get("综合评级", "")),
                    }
        except Exception as e:
            logger.warning(f"获取千股千评失败 {self.code}: {e}")
        return {}

    def get_comprehensive_news_analysis(self) -> Dict:
        """获取综合消息面分析"""
        # 获取各类消息
        news_list = self.fetch_news()
        announcements = self.fetch_announcements()
        reports = self.fetch_research_reports()

        # 分析新闻情感
        analyzed_news = []
        sentiment_scores = []

        for news in news_list:
            analysis = self.analyze_news_impact(news)
            analyzed_news.append(analysis)
            sentiment_scores.append(analysis["sentiment_score"])

        # 计算整体情感
        if sentiment_scores:
            avg_sentiment = sum(sentiment_scores) / len(sentiment_scores)
            positive_ratio = sum(1 for s in sentiment_scores if s > 10) / len(sentiment_scores)
            negative_ratio = sum(1 for s in sentiment_scores if s < -10) / len(sentiment_scores)
        else:
            avg_sentiment = 0
            positive_ratio = 0
            negative_ratio = 0

        # 分析公告类型
        announcement_types = {}
        for ann in announcements:
            ann_type = ann.get("type", "其他")
            announcement_types[ann_type] = announcement_types.get(ann_type, 0) + 1

        # 研报评级统计
        rating_stats = {}
        for report in reports:
            rating = report.get("rating", "未知")
            rating_stats[rating] = rating_stats.get(rating, 0) + 1

        # 综合判断
        if avg_sentiment > 30:
            overall_sentiment = "偏多"
            sentiment_color = "#ff4d4f"
        elif avg_sentiment > 10:
            overall_sentiment = "谨慎偏多"
            sentiment_color = "#ff7b72"
        elif avg_sentiment < -30:
            overall_sentiment = "偏空"
            sentiment_color = "#00b578"
        elif avg_sentiment < -10:
            overall_sentiment = "谨慎偏空"
            sentiment_color = "#7ee787"
        else:
            overall_sentiment = "中性"
            sentiment_color = "#8b949e"

        # 获取资金流向和千股千评
        fund_flow = self.fetch_fund_flow()
        comment_score = self.fetch_comment_score()

        # 生成摘要
        key_news = [n for n in analyzed_news[:5] if abs(n["sentiment_score"]) > 20]

        return {
            "股票代码": self.ticker,
            "股票名称": self.name,
            "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "整体情绪": overall_sentiment,
            "情绪颜色": sentiment_color,
            "情绪得分": round(avg_sentiment, 1),
            "积极新闻比例": round(positive_ratio * 100, 1),
            "消极新闻比例": round(negative_ratio * 100, 1),
            "新闻总数": len(news_list),
            "重要新闻": key_news,
            "公告统计": announcement_types,
            "研报评级": rating_stats,
            "最新新闻": analyzed_news[:5] if analyzed_news else [],
            "最新公告": announcements[:3] if announcements else [],
            "最新研报": reports[:3] if reports else [],
            "资金流向": fund_flow,
            "千股千评": comment_score,
        }

    def generate_news_report(self) -> str:
        """生成消息面分析报告"""
        analysis = self.get_comprehensive_news_analysis()

        lines = [
            f"【{analysis['股票名称']}({analysis['股票代码']}) 消息面分析】",
            f"分析时间: {analysis['分析时间']}",
            "",
            f"整体情绪: {analysis['整体情绪']} (得分: {analysis['情绪得分']})",
            f"新闻统计: 共{analysis['新闻总数']}条，积极{analysis['积极新闻比例']}% / 消极{analysis['消极新闻比例']}%",
            "",
            "【重要新闻】",
        ]

        for news in analysis["重要新闻"]:
            emoji = "📈" if news["sentiment_score"] > 0 else "📉"
            lines.append(f"{emoji} {news['title']}")
            lines.append(f"   来源: {news['source']} | 影响: {news['impact']}")

        if analysis["最新公告"]:
            lines.append("\n【最新公告】")
            for ann in analysis["最新公告"]:
                lines.append(f"📋 [{ann.get('type', '公告')}] {ann.get('title', '')}")

        if analysis["最新研报"]:
            lines.append("\n【机构研报】")
            for report in analysis["最新研报"]:
                lines.append(f"📊 {report.get('org', '机构')} | 评级: {report.get('rating', '未知')}")

        return "\n".join(lines)


def run_news_analysis(ticker: str, name: str = "") -> Dict:
    """便捷函数：运行消息面分析"""
    analyzer = NewsAnalyzer(ticker, name)
    return analyzer.get_comprehensive_news_analysis()
