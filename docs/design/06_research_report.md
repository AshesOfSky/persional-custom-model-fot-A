# 研报生成引擎详细设计

> 优先级: Sprint 4 (P2)
> 模块路径: `modules/research_report.py`

---

## 1. 功能概述

研报生成引擎自动整合技术面、基本面、估值和舆情数据，生成专业投资研究报告。支持多种输出格式（Markdown/Word/推文）。

### 1.1 核心能力
- 投资摘要自动生成（投资逻辑、关键假设、风险提示）
- 盈利预测表格（未来3年收入/利润预测）
- 目标价推导（基于DCF/Comps的加权平均）
- 催化剂日历（财报日期、产品发布、监管节点）
- 与现有推文生成功能联动

### 1.2 输入输出

**输入:**
- 股票代码
- 技术面分析结果
- 基本面数据
- 估值模型结果
- 舆情分析

**输出:**
- Markdown报告（快速预览）
- Word文档（正式报告）
- 投资摘要卡片
- 与推文生成联动

---

## 2. 架构设计

### 2.1 类图

```python
class ResearchReportGenerator:
    """研报生成器"""

    def __init__(self):
        self.templates = ReportTemplates()
        self.formatters = {
            'markdown': MarkdownFormatter(),
            'word': WordFormatter(),
            'json': JSONFormatter(),
        }

    async def generate(
        self,
        ticker: str,
        sections: List[ReportSection] = None,
        format: str = 'markdown',
    ) -> ResearchReport:
        """
        生成研报

        流程:
        1. 收集所有相关数据
        2. 执行分析模块
        3. 生成各章节内容
        4. 格式化输出
        """
        # 收集数据
        data = await self._collect_data(ticker)

        # 生成各章节
        sections = sections or DEFAULT_SECTIONS
        content = {}
        for section in sections:
            content[section] = await self._generate_section(section, data)

        # 格式化
        formatter = self.formatters.get(format)
        output = formatter.format(content, data)

        return ResearchReport(
            ticker=ticker,
            generated_at=datetime.now(),
            content=output,
            metadata=data['metadata'],
        )

    async def _collect_data(self, ticker: str) -> Dict:
        """收集研报所需的所有数据"""
        return {
            'metadata': await self._get_company_info(ticker),
            'technical': await self._get_technical_analysis(ticker),
            'fundamental': await self._get_fundamental_data(ticker),
            'valuation': await self._get_valuation(ticker),
            'sentiment': await self._get_sentiment(ticker),
            'catalysts': await self._get_catalysts(ticker),
        }

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


class ReportSection(Enum):
    """研报章节"""
    EXECUTIVE_SUMMARY = "executive_summary"
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


class ResearchReport:
    """研报"""

    ticker: str
    company_name: str
    generated_at: datetime
    rating: str  # 买入/增持/中性/减持
    target_price: float
    current_price: float
    upside: float
    content: str
    metadata: Dict

    def save(self, filepath: str) -> None:
        """保存报告"""
        pass

    def to_social_post(self, style: str = 'professional') -> str:
        """转换为社媒推文（与现有功能联动）"""
        pass


class CatalystCalendar:
    """催化剂日历"""

    def __init__(self):
        self.events = []

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


class CatalystEvent:
    """催化剂事件"""

    date: datetime
    type: str  # earnings / agm / product / regulatory
    title: str
    description: str
    impact: str  # high / medium / low
    sentiment: str  # positive / negative / neutral


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
        if self.dcf_value:
            values.append(self.dcf_value * weights['dcf'])
        if self.comps_value:
            values.append(self.comps_value * weights['comps'])
        if self.historical_multiple:
            values.append(self.historical_multiple * weights['historical'])

        return sum(values) / sum(weights.values()) if values else 0


class MarkdownFormatter:
    """Markdown格式化器"""

    def format(self, content: Dict, data: Dict) -> str:
        """格式化为Markdown"""
        sections = []

        # 标题
        sections.append(f"# {data['metadata']['name']} ({data['ticker']}) 研究报告")
        sections.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        sections.append(f"> 投资评级: {content.get('rating', 'N/A')}")
        sections.append(f"> 目标价: ¥{content.get('target_price', 0):.2f}")

        # 各章节
        for section_name, section_content in content.items():
            if section_content:
                sections.append(f"\n## {section_name}\n")
                sections.append(section_content)

        return '\n'.join(sections)


class WordFormatter:
    """Word文档格式化器"""

    def format(self, content: Dict, data: Dict) -> bytes:
        """格式化为Word文档"""
        doc = Document()

        # 封面
        doc.add_heading(f"{data['metadata']['name']} 研究报告", 0)
        doc.add_paragraph(f"股票代码: {data['ticker']}")
        doc.add_paragraph(f"报告日期: {datetime.now().strftime('%Y-%m-%d')}")

        # 各章节
        for section_name, section_content in content.items():
            if section_content:
                doc.add_heading(section_name, level=1)
                doc.add_paragraph(section_content)

        # 保存到字节流
        output = io.BytesIO()
        doc.save(output)
        output.seek(0)
        return output.getvalue()
```

---

## 3. 章节生成算法

### 3.1 投资摘要生成

```python
async def _generate_summary(self, data: Dict) -> str:
    """
    生成投资摘要 (Executive Summary)

    结构:
    1. 一句话投资逻辑
    2. 关键数据对比 (当前价/目标价/上涨空间)
    3. 核心催化剂
    4. 主要风险
    """
    ticker = data['metadata']['ticker']
    current_price = data['technical']['current_price']
    target_price = data['valuation']['target_price']
    upside = (target_price - current_price) / current_price

    # 确定评级
    if upside > 0.30:
        rating = "买入"
    elif upside > 0.15:
        rating = "增持"
    elif upside > -0.10:
        rating = "中性"
    else:
        rating = "减持"

    # 生成摘要文本
    summary = f"""
## 投资要点

**{data['metadata']['name']} ({ticker})** - {rating}

- **当前股价**: ¥{current_price:.2f}
- **目标价位**: ¥{target_price:.2f}
- **上涨空间**: {upside:+.1%}
- **投资评级**: {rating}

### 核心投资逻辑

{self._generate_thesis_bullet_points(data)}

### 关键催化剂

{self._format_catalysts(data['catalysts'][:3])}

### 风险提示

{self._format_risks(data['risks'][:3])}
"""

    return summary


def _generate_thesis_bullet_points(self, data: Dict) -> str:
    """生成投资要点"""
    points = []

    # 技术面要点
    tech = data['technical']
    if tech.get('trend') == 'bullish':
        points.append(f"**技术形态向好**: 股价处于{tech['ema_trend']}排列，支撑位于¥{tech['support']:.2f}")

    # 基本面要点
    fund = data['fundamental']
    if fund.get('revenue_growth', 0) > 0.15:
        points.append(f"**成长性突出**: 收入同比增长{fund['revenue_growth']:.1%}，高于行业平均")
    if fund.get('roe', 0) > 0.15:
        points.append(f"**盈利能力优异**: ROE达{fund['roe']:.1%}，护城河稳固")

    # 估值要点
    val = data['valuation']
    if val.get('pe_ratio', 100) < val.get('pe_historical_avg', 0):
        points.append(f"**估值处于低位**: 当前PE {val['pe_ratio']:.1f}x低于历史均值{val['pe_historical_avg']:.1f}x")

    return '\n'.join(f"- {p}" for p in points)
```

### 3.2 盈利预测

```python
async def _generate_forecast(self, data: Dict) -> pd.DataFrame:
    """
    生成盈利预测表

    预测未来3年收入、利润、EPS
    """
    historical = data['fundamental']['historical']

    # 基于历史趋势和分析师预期生成预测
    base_revenue = historical['revenue'][-1]
    base_net_income = historical['net_income'][-1]
    shares = data['metadata']['shares_outstanding']

    # 增长率假设（可调整）
    growth_assumptions = {
        'revenue_growth': [0.15, 0.12, 0.10],
        'net_margin': [0.20, 0.21, 0.22],
    }

    forecast = []
    revenue = base_revenue
    net_income = base_net_income

    for year in range(1, 4):
        revenue *= (1 + growth_assumptions['revenue_growth'][year-1])
        net_income = revenue * growth_assumptions['net_margin'][year-1]
        eps = net_income / shares if shares else 0

        forecast.append({
            '年度': f"FY{datetime.now().year + year}E",
            '收入(百万)': revenue / 1e6,
            '收入YoY': growth_assumptions['revenue_growth'][year-1],
            '净利润(百万)': net_income / 1e6,
            '净利率': growth_assumptions['net_margin'][year-1],
            'EPS': eps,
        })

    return pd.DataFrame(forecast)
```

### 3.3 催化剂日历生成

```python
async def _generate_catalysts_section(self, data: Dict) -> str:
    """生成催化剂章节"""
    catalysts = data['catalysts']

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
```

---

## 4. 输出格式

### 4.1 Markdown模板示例

```markdown
# 贵州茅台 (600519.SS) 研究报告

> 生成时间: 2026-03-05
> 投资评级: 买入
> 目标价: ¥2,350.00

---

## 投资摘要

**贵州茅台** - 买入

- **当前股价**: ¥1,680.00
- **目标价位**: ¥2,350.00
- **上涨空间**: +39.9%

### 核心投资逻辑

- **品牌护城河深厚**: 茅台在高端白酒市场占有绝对主导地位，定价权强
- **盈利能力优异**: ROE达28%，毛利率常年维持在90%以上
- **技术形态向好**: 股价处于EMA多头排列，突破关键阻力位

### 关键催化剂

- **2026-03-25**: 年报发布，预计业绩超预期
- **2026-04-15**: 股东大会，关注分红政策

### 风险提示

- 消费下行风险
- 政策监管风险
- 库存周期波动

---

## 估值分析

| 方法 | 目标价 | 权重 |
|------|--------|------|
| DCF | ¥2,500 | 50% |
| Comps | ¥2,200 | 40% |
| 历史PE | ¥2,100 | 10% |
| **加权平均** | **¥2,350** | 100% |

---

## 盈利预测

| 年度 | 收入(亿) | YoY | 净利润(亿) | EPS |
|------|----------|-----|------------|-----|
| FY2026E | 1,850 | +12% | 890 | ¥70.8 |
| FY2027E | 2,070 | +12% | 1,020 | ¥81.2 |
| FY2028E | 2,280 | +10% | 1,150 | ¥91.5 |
```

### 4.2 Word模板

Word文档使用python-docx生成，包含：
- 封面（公司名称、股票代码、评级、目标价）
- 目录
- 各章节内容
- 图表（权益曲线、估值对比图）

---

## 5. 与现有功能联动

### 5.1 与推文生成联动

```python
def to_social_post(self, style: str = 'professional') -> str:
    """
    转换为社媒推文

    风格选项:
    - professional: 专业研报风
    - xiaohongshu: 小红书风
    - concise: 简洁摘要
    """
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
```

---

## 6. 接口定义

### 6.1 Python API

```python
from modules.research_report import ResearchReportGenerator, ReportSection

# 创建生成器
generator = ResearchReportGenerator()

# 生成完整研报
report = await generator.generate(
    ticker='600519.SS',
    sections=[
        ReportSection.EXECUTIVE_SUMMARY,
        ReportSection.INVESTMENT_THESIS,
        ReportSection.VALUATION,
        ReportSection.FINANCIAL_ANALYSIS,
        ReportSection.RISKS,
        ReportSection.CATALYSTS,
    ],
    format='markdown',
)

# 保存Markdown
report.save('600519_research_report.md')

# 导出Word
word_report = await generator.generate(
    ticker='600519.SS',
    format='word',
)
word_report.save('600519_research_report.docx')

# 生成推文
post = report.to_social_post(style='professional')
print(post)
```

### 6.2 Streamlit集成

```python
def render_report_page():
    st.header("研报生成")

    ticker = st.text_input("股票代码", "600519.SS")

    # 选择章节
    sections = st.multiselect(
        "包含章节",
        ["投资摘要", "投资逻辑", "业务概览", "财务分析", "估值", "风险", "催化剂"],
        default=["投资摘要", "投资逻辑", "估值", "风险"],
    )

    format_type = st.radio("输出格式", ["Markdown", "Word文档"])

    if st.button("生成研报", type="primary"):
        with st.spinner("生成中..."):
            generator = ResearchReportGenerator()
            report = asyncio.run(generator.generate(
                ticker=ticker,
                format='markdown' if format_type == "Markdown" else 'word',
            ))

        # 展示
        if format_type == "Markdown":
            st.markdown(report.content)
        else:
            st.download_button(
                "下载Word文档",
                data=report.content,
                file_name=f"{ticker}_report.docx",
            )

        # 生成推文选项
        if st.checkbox("生成社媒推文"):
            style = st.selectbox("风格", ["专业研报", "小红书风格", "简洁摘要"])
            post = report.to_social_post(style=style)
            st.text_area("推文内容", post)
```

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
