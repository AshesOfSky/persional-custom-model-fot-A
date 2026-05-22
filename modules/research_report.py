"""
研报生成引擎

自动整合技术面、基本面、估值和舆情数据，生成专业投资研究报告。
支持 Markdown/Word/推文 多种输出格式。
"""

import asyncio
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Any, Callable
import json

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


class ReportSection(Enum):
    """研报章节"""
    EXECUTIVE_SUMMARY = "investment_summary"
    INVESTMENT_THESIS = "investment_thesis"
    BUSINESS_OVERVIEW = "business_overview"
    FINANCIAL_ANALYSIS = "financial_analysis"
    VALUATION = "valuation"
    RISKS = "risks"
    CATALYSTS = "catalysts"


DEFAULT_SECTIONS = [
    ReportSection.EXECUTIVE_SUMMARY,
    ReportSection.INVESTMENT_THESIS,
    ReportSection.VALUATION,
    ReportSection.FINANCIAL_ANALYSIS,
    ReportSection.RISKS,
]


@dataclass
class ResearchReport:
    """研报数据类"""
    ticker: str
    company_name: str
    generated_at: datetime
    rating: str
    target_price: float
    current_price: float
    upside: float
    content: str
    metadata: Dict
    format_type: str = "markdown"

    def save(self, filepath: str) -> None:
        """保存报告"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.content)

    def to_social_post(self, style: str = 'professional') -> str:
        """转换为社媒推文"""
        try:
            from modules.social_content import SocialContentGenerator

            generator = SocialContentGenerator()

            content = {
                'ticker': self.ticker,
                'company_name': self.company_name,
                'rating': self.rating,
                'target_price': self.target_price,
                'current_price': self.current_price,
                'upside': self.upside,
                'key_points': self._extract_key_points(),
            }

            return generator.generate(style=style, content=content)
        except Exception as e:
            logger.error(f"生成推文失败: {e}")
            return f"推文生成失败: {str(e)}"

    def _extract_key_points(self) -> List[str]:
        """提取关键要点"""
        points = []
        if self.upside > 0.2:
            points.append(f"上涨空间达{self.upside:.1%}")
        if self.rating in ["买入", "增持"]:
            points.append(f"投资评级: {self.rating}")
        return points


@dataclass
class CatalystEvent:
    """催化剂事件"""
    date: datetime
    event_type: str
    title: str
    description: str
    impact: str
    sentiment: str = "neutral"


class CatalystCalendar:
    """催化剂日历"""

    def __init__(self, data_source: Optional[Any] = None):
        self.data_source = data_source
        self.events: List[CatalystEvent] = []

    async def get_upcoming_events(
        self,
        ticker: str,
        days_ahead: int = 90,
    ) -> List[CatalystEvent]:
        """获取未来催化剂事件"""
        events = []

        # 财报发布日期
        earnings_dates = await self._get_earnings_dates(ticker)
        events.extend(earnings_dates)

        # 股东大会
        agm_dates = await self._get_agm_dates(ticker)
        events.extend(agm_dates)

        # 行业会议/产品发布
        industry_events = await self._get_industry_events(ticker)
        events.extend(industry_events)

        # 监管节点
        regulatory_events = await self._get_regulatory_events(ticker)
        events.extend(regulatory_events)

        # 筛选未来N天内的事件
        cutoff = datetime.now() + timedelta(days=days_ahead)
        return [e for e in events if e.date <= cutoff]

    async def _get_earnings_dates(self, ticker: str) -> List[CatalystEvent]:
        """获取财报日期"""
        # 模拟数据 - 实际应从数据源获取
        events = []
        base_date = datetime.now()

        # 假设每季度发布财报
        for i in range(1, 4):
            events.append(CatalystEvent(
                date=base_date + timedelta(days=i*90),
                event_type="earnings",
                title=f"Q{(i%4)+1}财报发布",
                description=f"季度业绩公告",
                impact="high",
                sentiment="neutral",
            ))
        return events

    async def _get_agm_dates(self, ticker: str) -> List[CatalystEvent]:
        """获取股东大会日期"""
        return [CatalystEvent(
            date=datetime.now() + timedelta(days=60),
            event_type="agm",
            title="年度股东大会",
            description="审议年度财务报告及分红方案",
            impact="medium",
            sentiment="neutral",
        )]

    async def _get_industry_events(self, ticker: str) -> List[CatalystEvent]:
        """获取行业事件"""
        return []  # 实际应查询行业数据库

    async def _get_regulatory_events(self, ticker: str) -> List[CatalystEvent]:
        """获取监管节点"""
        return []  # 实际应查询监管公告


class ValuationSummary:
    """估值摘要"""

    def __init__(
        self,
        dcf_value: Optional[float] = None,
        comps_value: Optional[float] = None,
        historical_multiple: Optional[float] = None,
    ):
        self.dcf_value = dcf_value
        self.comps_value = comps_value
        self.historical_multiple = historical_multiple

    def weighted_target(
        self,
        weights: Dict[str, float] = None,
    ) -> float:
        """
        加权计算目标价

        默认权重:
        - DCF: 50%
        - Comps: 40%
        - 历史倍数: 10%
        """
        weights = weights or {'dcf': 0.5, 'comps': 0.4, 'historical': 0.1}

        values = []
        total_weight = 0

        if self.dcf_value:
            values.append(self.dcf_value * weights['dcf'])
            total_weight += weights['dcf']
        if self.comps_value:
            values.append(self.comps_value * weights['comps'])
            total_weight += weights['comps']
        if self.historical_multiple:
            values.append(self.historical_multiple * weights['historical'])
            total_weight += weights['historical']

        if not values:
            logger.warning("没有可用的估值数据，目标价返回0")
            return 0

        return sum(values) / total_weight if total_weight > 0 else 0

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'dcf_value': self.dcf_value,
            'comps_value': self.comps_value,
            'historical_multiple': self.historical_multiple,
            'weighted_target': self.weighted_target(),
        }


class MarkdownFormatter:
    """Markdown格式化器"""

    def format(self, content: Dict, data: Dict) -> str:
        """格式化为Markdown"""
        sections = []
        metadata = data.get('metadata', {})

        # 标题
        company_name = metadata.get('name', data.get('ticker', ''))
        sections.append(f"# {company_name} ({data.get('ticker', '')}) 研究报告")
        sections.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

        rating = content.get('rating', 'N/A')
        target = content.get('target_price', 0)
        current = content.get('current_price', 0)
        upside = content.get('upside', 0)

        sections.append(f"> 投资评级: **{rating}**")
        sections.append(f"> 目标价: ¥{target:.2f} | 当前价: ¥{current:.2f} | 空间: {upside:+.1%}")

        # 各章节
        section_titles = {
            'executive_summary': '投资摘要',
            'investment_thesis': '投资逻辑',
            'business_overview': '业务概览',
            'financial_analysis': '财务分析',
            'valuation': '估值分析',
            'risks': '风险提示',
            'catalysts': '催化剂日历',
        }

        for section_name, section_content in content.items():
            if section_content and section_name not in ['rating', 'target_price', 'current_price', 'upside']:
                title = section_titles.get(section_name, section_name)
                sections.append(f"\n## {title}\n")
                sections.append(section_content)

        return '\n'.join(sections)


class WordFormatter:
    """Word文档格式化器"""

    def format(self, content: Dict, data: Dict) -> bytes:
        """格式化为Word文档"""
        try:
            from docx import Document
            from docx.shared import Pt, RGBColor, Inches
        except ImportError:
            raise ImportError("请安装python-docx: pip install python-docx")

        doc = Document()

        metadata = data.get('metadata', {})
        company_name = metadata.get('name', data.get('ticker', ''))

        # 封面
        title = doc.add_heading(f"{company_name} 研究报告", 0)
        title.alignment = 1  # 居中

        doc.add_paragraph(f"股票代码: {data.get('ticker', '')}")
        doc.add_paragraph(f"报告日期: {datetime.now().strftime('%Y-%m-%d')}")
        doc.add_paragraph(f"投资评级: {content.get('rating', 'N/A')}")
        doc.add_paragraph(f"目标价格: ¥{content.get('target_price', 0):.2f}")

        doc.add_page_break()

        # 各章节
        section_titles = {
            'executive_summary': '投资摘要',
            'investment_thesis': '投资逻辑',
            'business_overview': '业务概览',
            'financial_analysis': '财务分析',
            'valuation': '估值分析',
            'risks': '风险提示',
            'catalysts': '催化剂日历',
        }

        for section_name, section_content in content.items():
            if section_content and section_name not in ['rating', 'target_price', 'current_price', 'upside']:
                title = section_titles.get(section_name, section_name)
                doc.add_heading(title, level=1)
                doc.add_paragraph(str(section_content))

        # 保存到字节流
        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        return output.getvalue()


class ResearchReportGenerator:
    """研报生成器"""

    def __init__(self, data_source: Optional[Any] = None):
        self.data_source = data_source
        self.formatters = {
            'markdown': MarkdownFormatter(),
            'word': WordFormatter(),
            'json': None,  # JSON直接返回数据结构
        }
        self.catalyst_calendar = CatalystCalendar(data_source)

    async def generate(
        self,
        ticker: str,
        sections: List[ReportSection] = None,
        format_type: str = 'markdown',
        custom_data: Optional[Dict] = None,
    ) -> ResearchReport:
        """
        生成研报

        Args:
            ticker: 股票代码
            sections: 包含的章节，默认全部
            format_type: 输出格式 (markdown/word/json)
            custom_data: 自定义数据，覆盖自动获取
        """
        # 收集数据
        if custom_data:
            data = custom_data
            data['ticker'] = ticker
        else:
            data = await self._collect_data(ticker)

        # 生成各章节
        sections = sections or DEFAULT_SECTIONS
        content = {}

        for section in sections:
            section_content = await self._generate_section(section, data)
            if section_content:
                content[section.value] = section_content

        # 添加评级和目标价到content
        rating, target_price, current_price, upside = self._calculate_rating(data)
        content['rating'] = rating
        content['target_price'] = target_price
        content['current_price'] = current_price
        content['upside'] = upside

        # 格式化
        formatter = self.formatters.get(format_type)
        if format_type == 'json':
            output = json.dumps(content, ensure_ascii=False, indent=2)
        elif formatter:
            output = formatter.format(content, data)
        else:
            output = str(content)

        return ResearchReport(
            ticker=ticker,
            company_name=data.get('metadata', {}).get('name', ticker),
            generated_at=datetime.now(),
            rating=rating,
            target_price=target_price,
            current_price=current_price,
            upside=upside,
            content=output,
            metadata=data.get('metadata', {}),
            format_type=format_type,
        )

    async def _collect_data(self, ticker: str) -> Dict:
        """收集研报所需的所有数据"""
        return {
            'ticker': ticker,
            'metadata': await self._get_company_info(ticker),
            'technical': await self._get_technical_analysis(ticker),
            'fundamental': await self._get_fundamental_data(ticker),
            'valuation': await self._get_valuation(ticker),
            'sentiment': await self._get_sentiment(ticker),
            'catalysts': await self._get_catalysts(ticker),
        }

    async def _get_company_info(self, ticker: str) -> Dict:
        """获取公司信息"""
        # 实际应从数据源获取
        return {
            'ticker': ticker,
            'name': ticker,  # 实际应查询公司名称
            'sector': '',
            'industry': '',
            'market_cap': 0,
        }

    async def _get_technical_analysis(self, ticker: str) -> Dict:
        """获取技术面分析"""
        return {
            'current_price': 100.0,
            'trend': 'bullish',
            'ema_trend': '多头排列',
            'support': 95.0,
            'resistance': 110.0,
        }

    async def _get_fundamental_data(self, ticker: str) -> Dict:
        """获取基本面数据"""
        return {
            'revenue_growth': 0.15,
            'net_margin': 0.20,
            'roe': 0.18,
            'pe_ratio': 15.0,
            'pb_ratio': 2.0,
            'historical': {
                'revenue': [100, 120, 140],
                'net_income': [20, 25, 30],
            }
        }

    async def _get_valuation(self, ticker: str) -> Dict:
        """获取估值数据"""
        return {
            'dcf_value': 120.0,
            'comps_value': 110.0,
            'historical_multiple': 100.0,
            'target_price': 113.0,
            'pe_ratio': 15.0,
            'pe_historical_avg': 18.0,
        }

    async def _get_sentiment(self, ticker: str) -> Dict:
        """获取舆情数据"""
        return {
            'overall': 'positive',
            'news_sentiment': 0.6,
            'social_sentiment': 0.5,
        }

    async def _get_catalysts(self, ticker: str) -> List[CatalystEvent]:
        """获取催化剂事件"""
        return await self.catalyst_calendar.get_upcoming_events(ticker)

    def _calculate_rating(self, data: Dict) -> tuple:
        """计算评级和目标价"""
        current_price = data.get('technical', {}).get('current_price', 100.0)

        # 计算加权目标价
        valuation = ValuationSummary(
            dcf_value=data.get('valuation', {}).get('dcf_value'),
            comps_value=data.get('valuation', {}).get('comps_value'),
            historical_multiple=data.get('valuation', {}).get('historical_multiple'),
        )
        target_price = valuation.weighted_target()

        # 计算上涨空间
        upside = (target_price - current_price) / current_price if current_price > 0 else 0

        # 确定评级
        if upside > 0.30:
            rating = "买入"
        elif upside > 0.15:
            rating = "增持"
        elif upside > -0.10:
            rating = "中性"
        else:
            rating = "减持"

        return rating, target_price, current_price, upside

    async def _generate_section(
        self,
        section: ReportSection,
        data: Dict,
    ) -> str:
        """生成单个章节"""
        generators = {
            ReportSection.EXECUTIVE_SUMMARY: self._generate_summary,
            ReportSection.INVESTMENT_THESIS: self._generate_thesis,
            ReportSection.BUSINESS_OVERVIEW: self._generate_business,
            ReportSection.FINANCIAL_ANALYSIS: self._generate_financial,
            ReportSection.VALUATION: self._generate_valuation_section,
            ReportSection.RISKS: self._generate_risks,
            ReportSection.CATALYSTS: self._generate_catalysts_section,
        }

        generator = generators.get(section)
        if generator:
            return await generator(data)
        return ""

    async def _generate_summary(self, data: Dict) -> str:
        """生成投资摘要"""
        metadata = data.get('metadata', {})
        ticker = data.get('ticker', '')
        tech = data.get('technical', {})
        fund = data.get('fundamental', {})
        val = data.get('valuation', {})

        current_price = tech.get('current_price', 0)
        target_price = val.get('target_price', current_price * 1.2)
        upside = (target_price - current_price) / current_price if current_price > 0 else 0

        # 确定评级
        if upside > 0.30:
            rating = "买入"
        elif upside > 0.15:
            rating = "增持"
        elif upside > -0.10:
            rating = "中性"
        else:
            rating = "减持"

        points = self._generate_thesis_bullet_points(data)

        summary = f"""
**{metadata.get('name', ticker)} ({ticker})** - {rating}

- **当前股价**: ¥{current_price:.2f}
- **目标价位**: ¥{target_price:.2f}
- **上涨空间**: {upside:+.1%}
- **投资评级**: {rating}

### 核心投资逻辑

{points}

### 关键催化剂

{await self._format_top_catalysts(data, 3)}

### 主要风险

{await self._format_top_risks(data, 3)}
"""
        return summary

    def _generate_thesis_bullet_points(self, data: Dict) -> str:
        """生成投资要点"""
        points = []

        # 技术面要点
        tech = data.get('technical', {})
        if tech.get('trend') == 'bullish':
            points.append(f"**技术形态向好**: 股价处于{tech.get('ema_trend', '上涨')}趋势，支撑位于¥{tech.get('support', 0):.2f}")

        # 基本面要点
        fund = data.get('fundamental', {})
        if fund.get('revenue_growth', 0) > 0.15:
            points.append(f"**成长性突出**: 收入同比增长{fund['revenue_growth']:.1%}，高于行业平均")
        if fund.get('roe', 0) > 0.15:
            points.append(f"**盈利能力优异**: ROE达{fund['roe']:.1%}，护城河稳固")

        # 估值要点
        val = data.get('valuation', {})
        if val.get('pe_ratio', 100) < val.get('pe_historical_avg', 0):
            points.append(f"**估值处于低位**: 当前PE {val['pe_ratio']:.1f}x低于历史均值{val['pe_historical_avg']:.1f}x")

        return '\n'.join(f"- {p}" for p in points) if points else "- 暂无明确投资要点"

    async def _format_top_catalysts(self, data: Dict, n: int) -> str:
        """格式化前N个催化剂"""
        catalysts = data.get('catalysts', [])
        if not catalysts:
            return "- 暂无可预见重大催化剂"

        lines = []
        for c in catalysts[:n]:
            lines.append(f"- **{c.date.strftime('%Y-%m-%d')}** - {c.title}")
        return '\n'.join(lines)

    async def _format_top_risks(self, data: Dict, n: int) -> str:
        """格式化前N个风险"""
        # 实际应从风险数据库获取
        default_risks = [
            "宏观经济下行风险",
            "行业竞争加剧风险",
            "政策监管变化风险",
        ]
        return '\n'.join(f"- {r}" for r in default_risks[:n])

    async def _generate_thesis(self, data: Dict) -> str:
        """生成投资逻辑章节"""
        return """
基于技术面、基本面和估值的综合分析，我们认为该股具备以下投资逻辑：

1. **技术面**: 股价处于上升趋势，关键支撑位稳固
2. **基本面**: 公司盈利能力良好，成长性优于行业平均
3. **估值面**: 当前估值相对合理，具备一定安全边际
"""

    async def _generate_business(self, data: Dict) -> str:
        """生成业务概览章节"""
        metadata = data.get('metadata', {})
        return f"""
{metadata.get('name', '该公司')}主要从事{metadata.get('industry', '相关业务')}业务。

### 主营业务

- 核心业务板块
- 收入来源构成
- 竞争优势分析

### 行业地位

- 市场份额
- 竞争格局
- 发展趋势
"""

    async def _generate_financial(self, data: Dict) -> str:
        """生成财务分析章节"""
        fund = data.get('fundamental', {})

        # 生成盈利预测表
        forecast = await self._generate_forecast(data)

        content = f"""
### 历史财务表现

- **收入增长率**: {fund.get('revenue_growth', 0):.1%}
- **净利润率**: {fund.get('net_margin', 0):.1%}
- **ROE**: {fund.get('roe', 0):.1%}

### 盈利预测

{forecast.to_markdown(index=False) if not forecast.empty else '暂无预测数据'}
"""
        return content

    async def _generate_forecast(self, data: Dict) -> pd.DataFrame:
        """生成盈利预测表"""
        fund = data.get('fundamental', {})
        historical = fund.get('historical', {})

        if not historical or 'revenue' not in historical:
            return pd.DataFrame()

        base_revenue = historical['revenue'][-1] if historical['revenue'] else 100
        base_net_income = historical['net_income'][-1] if historical.get('net_income') else 20

        # 增长率假设
        growth_assumptions = {
            'revenue_growth': [0.15, 0.12, 0.10],
            'net_margin': [0.20, 0.21, 0.22],
        }

        forecast = []
        revenue = base_revenue

        for year in range(1, 4):
            revenue = revenue * (1 + growth_assumptions['revenue_growth'][year-1])
            net_income = revenue * growth_assumptions['net_margin'][year-1]

            forecast.append({
                '年度': f"FY{datetime.now().year + year}E",
                '收入(百万)': f"{revenue:.1f}",
                '收入YoY': f"{growth_assumptions['revenue_growth'][year-1]:+.0%}",
                '净利润(百万)': f"{net_income:.1f}",
                '净利率': f"{growth_assumptions['net_margin'][year-1]:.1%}",
            })

        return pd.DataFrame(forecast)

    async def _generate_valuation_section(self, data: Dict) -> str:
        """生成估值分析章节"""
        val = data.get('valuation', {})
        summary = ValuationSummary(
            dcf_value=val.get('dcf_value'),
            comps_value=val.get('comps_value'),
            historical_multiple=val.get('historical_multiple'),
        )

        weights = {'dcf': 0.5, 'comps': 0.4, 'historical': 0.1}

        content = f"""
### 估值方法对比

| 方法 | 目标价 | 权重 |
|------|--------|------|
"""
        if summary.dcf_value:
            content += f"| DCF | ¥{summary.dcf_value:.2f} | {weights['dcf']:.0%} |\n"
        if summary.comps_value:
            content += f"| Comps | ¥{summary.comps_value:.2f} | {weights['comps']:.0%} |\n"
        if summary.historical_multiple:
            content += f"| 历史倍数 | ¥{summary.historical_multiple:.2f} | {weights['historical']:.0%} |\n"

        content += f"| **加权平均** | **¥{summary.weighted_target(weights):.2f}** | 100% |\n"

        content += f"""
### 估值分析

- 当前PE: {val.get('pe_ratio', 'N/A')}x
- 历史平均PE: {val.get('pe_historical_avg', 'N/A')}x
- 估值分位: 相对历史水平
"""
        return content

    async def _generate_risks(self, data: Dict) -> str:
        """生成风险提示章节"""
        return """
### 主要风险因素

1. **宏观经济风险**: 经济周期波动可能影响公司业绩增长
2. **行业竞争风险**: 行业竞争加剧可能压缩利润率
3. **政策监管风险**: 政策变化可能对公司经营产生不利影响
4. **估值波动风险**: 市场估值中枢下移风险
5. **流动性风险**: 特定市场条件下可能出现流动性不足

### 风险缓释因素

- 公司具备较强的抗周期能力
- 行业龙头地位稳固
- 现金流状况良好
"""

    async def _generate_catalysts_section(self, data: Dict) -> str:
        """生成催化剂章节"""
        catalysts = data.get('catalysts', [])

        if not catalysts:
            return "暂无可预见的重大催化剂事件。"

        sections = ["###  upcoming 催化剂\n"]

        # 按重要性分组
        high_impact = [c for c in catalysts if c.impact == 'high']
        medium_impact = [c for c in catalysts if c.impact == 'medium']

        if high_impact:
            sections.append("**高影响事件:**")
            for c in high_impact:
                sections.append(f"- **{c.date.strftime('%Y-%m-%d')}** - {c.title}")
                sections.append(f"  {c.description}")

        if medium_impact:
            sections.append("\n**中等影响事件:**")
            for c in medium_impact:
                sections.append(f"- {c.date.strftime('%Y-%m-%d')} - {c.title}")

        return '\n'.join(sections)


# Streamlit 集成
def render_report_page():
    """渲染研报生成页面"""
    import streamlit as st

    st.header("研报生成")

    ticker = st.text_input("股票代码", "600519.SS")

    # 选择章节
    section_options = {
        "投资摘要": ReportSection.EXECUTIVE_SUMMARY,
        "投资逻辑": ReportSection.INVESTMENT_THESIS,
        "业务概览": ReportSection.BUSINESS_OVERVIEW,
        "财务分析": ReportSection.FINANCIAL_ANALYSIS,
        "估值": ReportSection.VALUATION,
        "风险": ReportSection.RISKS,
        "催化剂": ReportSection.CATALYSTS,
    }

    selected_sections = st.multiselect(
        "包含章节",
        list(section_options.keys()),
        default=["投资摘要", "投资逻辑", "估值", "风险"],
    )

    sections = [section_options[s] for s in selected_sections]

    format_type = st.radio("输出格式", ["Markdown", "Word文档"])
    format_map = {"Markdown": "markdown", "Word文档": "word"}

    if st.button("生成研报", type="primary"):
        with st.spinner("生成中..."):
            generator = ResearchReportGenerator()
            report = asyncio.run(generator.generate(
                ticker=ticker,
                sections=sections,
                format_type=format_map[format_type],
            ))

        # 展示
        if format_type == "Markdown":
            st.markdown(report.content)
            st.download_button(
                "下载Markdown",
                data=report.content,
                file_name=f"{ticker}_report.md",
            )
        else:
            st.download_button(
                "下载Word文档",
                data=report.content,
                file_name=f"{ticker}_report.docx",
            )

        # 生成推文选项
        if st.checkbox("生成社媒推文"):
            style_map = {
                "专业研报": "professional",
                "小红书风格": "xiaohongshu",
                "简洁摘要": "concise",
            }
            style = st.selectbox("风格", list(style_map.keys()))
            post = report.to_social_post(style=style_map[style])
            st.text_area("推文内容", post)


if __name__ == "__main__":
    # 测试代码
    async def test():
        generator = ResearchReportGenerator()
        report = await generator.generate(
            ticker="600519.SS",
            format_type='markdown',
        )
        print(report.content)

    asyncio.run(test())
