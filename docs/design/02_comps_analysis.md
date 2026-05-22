# 可比公司分析(Comps)详细设计

> 优先级: Sprint 1 (P0)
> 模块路径: `plugins/fundamental/comps_analysis.py`

---

## 1. 功能概述

可比公司分析(Comparable Company Analysis)通过筛选与目标公司业务相似、规模相近的上市公司，计算其交易倍数来推导目标公司的合理估值范围。

### 1.1 核心能力
- 自动筛选可比公司（行业匹配 + 规模相近）
- 计算多种交易倍数（EV/EBITDA, P/E, EV/Revenue, P/B）
- 统计摘要（均值、中位数、最高/最低）
- 可视化散点图（增长率 vs 倍数关系）
- 输出专业Comps表格（Excel格式）

### 1.2 输入输出

**输入:**
- 目标股票代码 (ticker)
- 可比公司列表（可选，自动或手动指定）
- 筛选标准（行业、市值范围、地域等）

**输出:**
- 可比公司估值表
- 统计摘要
- 估值范围推导
- Excel输出

---

## 2. 架构设计

### 2.1 类图

```python
class CompsAnalyzer:
    """可比公司分析器"""

    def __init__(self, target_ticker: str):
        self.target = TargetCompany(target_ticker)
        self.comparables: List[ComparableCompany] = []
        self.metrics: Dict[str, List[float]] = {}

    def find_comparables(
        self,
        industry_match: bool = True,
        market_cap_range: Tuple[float, float] = (0.3, 3.0),
        max_count: int = 10,
    ) -> List[str]:
        """自动筛选可比公司"""
        pass

    def add_comparable(self, ticker: str) -> None:
        """手动添加可比公司"""
        pass

    def calculate_multiples(self) -> pd.DataFrame:
        """计算所有公司的估值倍数"""
        pass

    def generate_summary_stats(self) -> SummaryStats:
        """生成统计摘要"""
        pass

    def derive_valuation_range(self) -> ValuationRange:
        """推导目标公司估值范围"""
        pass

    def export_excel(self, filepath: str) -> None:
        """导出Excel表格"""
        pass


class TargetCompany:
    """目标公司"""

    ticker: str
    name: str
    industry: str
    market_cap: float
    enterprise_value: float
    revenue: float
    ebitda: float
    net_income: float
    book_value: float


class ComparableCompany:
    """可比公司"""

    ticker: str
    name: str
    industry: str
    market_cap: float
    enterprise_value: float
    revenue: float
    revenue_growth: float
    ebitda: float
    ebitda_margin: float
    net_income: float
    book_value: float
    ev_ebitda: float
    pe_ratio: float
    ev_revenue: float
    pb_ratio: float


class SummaryStats:
    """统计摘要"""

    metric_name: str
    mean: float
    median: float
    min: float
    max: float
    std: float
    q25: float
    q75: float


class ValuationRange:
    """估值范围"""

    metric: str
    low: float           # 基于P25或Min
    base: float          # 基于Median
    high: float          # 基于P75或Max
    implied_ev: float    # 隐含企业价值
    implied_equity: float  # 隐含股权价值
    implied_per_share: float  # 隐含每股价值
```

### 2.2 数据流

```
┌─────────────────────────────────────────────────────────────┐
│                      输入层                                  │
│  ┌─────────────┐  ┌─────────────────────────────────────┐  │
│  │ 目标公司    │  │ 筛选条件                            │  │
│  │ (ticker)    │  │ · 行业代码 (GICS/SW)               │  │
│  └──────┬──────┘  │ · 市值范围 (0.3x - 3x)             │  │
│         │         │ · 地域限制 (A股/港股/美股)         │  │
│         │         │ · 最大数量 (5-15家)                │  │
│         │         └─────────────────────────────────────┘  │
└─────────┼───────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│                    公司筛选层                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 获取行业    │  │ 候选池构建  │  │ 相似度排序          │  │
│  │ 分类信息    │  │ (同行业)    │  │ · 市值相似度       │  │
│  │             │  │             │  │ · 业务相似度       │  │
│  └──────┬──────┘  └──────┬──────┘  │ · 成长性相似度     │  │
│         │                │         └──────────┬──────────┘  │
│         └────────────────┼────────────────────┘             │
│                          ▼                                  │
│                   ┌─────────────┐                           │
│                   │ Top N 选择  │                           │
│                   │ (默认8-10家)│                           │
│                   └──────┬──────┘                           │
└─────────────────────────┼───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          │               │               │
          ▼               ▼               ▼
┌─────────────────┐ ┌─────────┐ ┌─────────────────┐
│   倍数计算       │ │ 统计计算 │ │    估值推导      │
│ ┌─────────────┐ │ │         │ │ ┌─────────────┐ │
│ │ EV/EBITDA   │ │ │ 均值    │ │ │ Low (P25)   │ │
│ │ P/E (TTM)   │ │ │ 中位数  │ │ │ Base (P50)  │ │
│ │ EV/Revenue  │ │ │ 标准差  │ │ │ High (P75)  │ │
│ │ P/B         │ │ │ 范围    │ │ └─────────────┘ │
│ └─────────────┘ │ │         │ │                 │
└─────────────────┘ └─────────┘ └─────────────────┘
```

---

## 3. 核心算法

### 3.1 可比公司筛选算法

```python
def find_comparables(
    target_ticker: str,
    market_cap_range: Tuple[float, float] = (0.3, 3.0),
    revenue_range: Tuple[float, float] = (0.2, 5.0),
    max_count: int = 10,
) -> List[str]:
    """
    自动筛选可比公司

    策略:
    1. 获取目标公司行业分类
    2. 获取同行业所有上市公司
    3. 根据市值和收入筛选
    4. 按相似度排序取Top N
    """
    # 获取目标公司信息
    target = get_company_info(target_ticker)
    target_industry = target['industry_code']
    target_market_cap = target['market_cap']
    target_revenue = target['revenue']

    # 获取同行业候选公司
    candidates = get_companies_by_industry(target_industry)

    # 筛选和评分
    scored_candidates = []
    for candidate in candidates:
        if candidate['ticker'] == target_ticker:
            continue

        # 市值筛选
        mc_ratio = candidate['market_cap'] / target_market_cap
        if not (market_cap_range[0] <= mc_ratio <= market_cap_range[1]):
            continue

        # 收入筛选
        rev_ratio = candidate['revenue'] / target_revenue
        if not (revenue_range[0] <= rev_ratio <= revenue_range[1]):
            continue

        # 计算相似度得分 (越低越相似)
        mc_score = abs(1 - mc_ratio)
        rev_score = abs(1 - rev_ratio)
        growth_score = abs(target['revenue_growth'] - candidate['revenue_growth'])

        total_score = mc_score * 0.4 + rev_score * 0.3 + growth_score * 0.3
        scored_candidates.append((candidate['ticker'], total_score))

    # 排序取前N
    scored_candidates.sort(key=lambda x: x[1])
    return [ticker for ticker, _ in scored_candidates[:max_count]]
```

### 3.2 估值倍数计算

```python
class MultiplesCalculator:
    """估值倍数计算器"""

    def calculate_ev_ebitda(self, company: Dict) -> float:
        """企业价值/EBITDA"""
        ev = company['market_cap'] + company['total_debt'] - company['cash']
        return ev / company['ebitda'] if company['ebitda'] > 0 else None

    def calculate_pe_ratio(self, company: Dict) -> float:
        """市盈率 (P/E)"""
        return company['market_cap'] / company['net_income'] \
               if company['net_income'] > 0 else None

    def calculate_ev_revenue(self, company: Dict) -> float:
        """企业价值/收入"""
        ev = company['market_cap'] + company['total_debt'] - company['cash']
        return ev / company['revenue'] if company['revenue'] > 0 else None

    def calculate_pb_ratio(self, company: Dict) -> float:
        """市净率 (P/B)"""
        return company['market_cap'] / company['book_value'] \
               if company['book_value'] > 0 else None

    def calculate_all(self, company: Dict) -> Dict[str, float]:
        """计算所有倍数"""
        return {
            'ev_ebitda': self.calculate_ev_ebitda(company),
            'pe_ratio': self.calculate_pe_ratio(company),
            'ev_revenue': self.calculate_ev_revenue(company),
            'pb_ratio': self.calculate_pb_ratio(company),
            'market_cap': company['market_cap'],
            'revenue': company['revenue'],
            'ebitda': company['ebitda'],
            'revenue_growth': company['revenue_growth'],
            'ebitda_margin': company['ebitda'] / company['revenue'] \
                             if company['revenue'] > 0 else None,
        }
```

### 3.3 统计摘要计算

```python
def calculate_summary_stats(values: List[float]) -> Dict[str, float]:
    """
    计算统计摘要

    排除异常值（Outliers）:
    - 倍数 > 100x 通常视为异常
    - 负值（亏损公司）单独处理
    """
    # 过滤有效值
    valid_values = [v for v in values if v is not None and 0 < v < 100]

    if not valid_values:
        return None

    sorted_values = sorted(valid_values)
    n = len(sorted_values)

    return {
        'count': n,
        'mean': np.mean(valid_values),
        'median': np.median(valid_values),
        'std': np.std(valid_values),
        'min': sorted_values[0],
        'max': sorted_values[-1],
        'q25': sorted_values[int(n * 0.25)],
        'q75': sorted_values[int(n * 0.75)],
    }
```

### 3.4 估值范围推导

```python
def derive_valuation_range(
    target_metrics: Dict[str, float],
    comps_stats: Dict[str, Dict],
) -> Dict[str, ValuationRange]:
    """
    基于可比公司倍数推导目标公司估值范围

    方法:
    - Low: P25倍数 × 目标指标
    - Base: 中位数倍数 × 目标指标
    - High: P75倍数 × 目标指标
    """
    ranges = {}

    for metric_name, stats in comps_stats.items():
        if stats is None:
            continue

        target_value = target_metrics.get(metric_name.replace('_multiple', ''))
        if target_value is None or target_value <= 0:
            continue

        low = stats['q25'] * target_value
        base = stats['median'] * target_value
        high = stats['q75'] * target_value

        ranges[metric_name] = ValuationRange(
            metric=metric_name,
            low=low,
            base=base,
            high=high,
        )

    return ranges
```

---

## 4. 数据标准与映射

### 4.1 行业分类映射

| 数据源 | 分类标准 | 映射方法 |
|--------|----------|----------|
| A股 | 申万行业 (SW) | 直接使用 |
| 港股 | 恒生行业 | 映射到GICS |
| 美股 | GICS | 直接使用 |

### 4.2 财务指标标准化

```python
# 中国会计准则 vs 国际会计准则 科目映射
CHINA_TO_IFRS_MAPPING = {
    '营业收入': 'Revenue',
    '营业利润': 'Operating Income',
    '净利润': 'Net Income',
    '总资产': 'Total Assets',
    '股东权益': 'Shareholders Equity',
    '货币资金': 'Cash and Equivalents',
    '短期借款+长期借款': 'Total Debt',
}
```

---

## 5. Excel输出规范

### 5.1 表格结构

```
Sheet1: Trading Comps (可比公司估值表)
| 公司名称 | 市值 | EV | 收入 | EBITDA | EV/EBITDA | P/E | EV/Rev | P/B |
|---------|------|-----|------|--------|-----------|-----|--------|-----|
| 公司A   | ...  | ... | ...  | ...    | 8.5x      | 15x | 2.1x   | 1.8x|
| 公司B   | ...  | ... | ...  | ...    | 10.2x     | 18x | 2.5x   | 2.1x|
| ...     |      |     |      |        |           |     |        |     |
|---------|------|-----|------|--------|-----------|-----|--------|-----|
| 均值    |      |     |      |        | 9.5x      | 16x | 2.3x   | 2.0x|
| 中位数  |      |     |      |        | 9.2x      | 15x | 2.2x   | 1.9x|
| 最大值  |      |     |      |        | 12.0x     | 22x | 3.0x   | 2.5x|
| 最小值  |      |     |      |        | 7.0x      | 12x | 1.8x   | 1.5x|

Sheet2: Valuation Summary (估值摘要)
| 指标        | Low    | Base   | High   |
|-------------|--------|--------|--------|
| EV/EBITDA   | 8.0x   | 9.5x   | 11.0x  |
| 隐含EV      | 800M   | 950M   | 1100M  |
| 隐含Equity  | 700M   | 850M   | 1000M  |
| 每股价值    | ¥35    | ¥42.5  | ¥50    |

Sheet3: Charts (图表)
- 倍数分布箱线图
- 增长率 vs EV/EBITDA 散点图
```

### 5.2 颜色规范

- 目标公司行：浅黄色背景高亮
- 统计行（均值/中位数）：浅灰色背景
- 倍数列：条件格式（高于中位数红色，低于中位数绿色）

---

## 6. 可视化设计

### 6.1 散点图：增长率 vs 倍数

```python
import plotly.express as px

def plot_growth_vs_multiple(comps_df: pd.DataFrame):
    """增长率与估值倍数关系图"""
    fig = px.scatter(
        comps_df,
        x='revenue_growth',
        y='ev_ebitda',
        size='market_cap',
        color='ebitda_margin',
        hover_data=['ticker', 'name'],
        labels={
            'revenue_growth': '收入增长率',
            'ev_ebitda': 'EV/EBITDA',
        },
        title='增长率 vs 估值倍数'
    )
    # 添加目标公司标记
    fig.add_scatter(
        x=[target_growth],
        y=[target_implied_multiple],
        mode='markers',
        marker=dict(size=20, color='red', symbol='star'),
        name='目标公司'
    )
    return fig
```

### 6.2 箱线图：倍数分布

```python
def plot_multiple_distribution(comps_df: pd.DataFrame):
    """估值倍数分布箱线图"""
    fig = px.box(
        comps_df[['ev_ebitda', 'pe_ratio', 'ev_revenue']],
        title='可比公司估值倍数分布'
    )
    return fig
```

---

## 7. 接口定义

### 7.1 Python API

```python
from plugins.fundamental.comps_analysis import CompsAnalyzer

# 快速分析
analyzer = CompsAnalyzer(target_ticker='600519.SS')
analyzer.find_comparables(max_count=8)
result = analyzer.run()

print(f"中位数 EV/EBITDA: {result.summary['ev_ebitda']['median']:.1f}x")
print(f"估值范围: ¥{result.valuation_range['low']:.2f} - ¥{result.valuation_range['high']:.2f}")

# 手动指定可比公司
analyzer = CompsAnalyzer('000858.SZ')  # 五粮液
analyzer.add_comparable('600519.SS')   # 茅台
analyzer.add_comparable('000568.SZ')   # 泸州老窖
analyzer.add_comparable('002304.SZ')   # 洋河
result = analyzer.run()

# 导出Excel
analyzer.export_excel('wuliangye_comps.xlsx')
```

### 7.2 Streamlit集成

```python
def render_comps_page():
    st.header("可比公司分析")

    ticker = st.text_input("目标股票代码", "600519.SS")

    col1, col2 = st.columns(2)
    with col1:
        auto_select = st.checkbox("自动筛选可比公司", True)
        max_comps = st.slider("最大可比公司数", 5, 15, 8)
    with col2:
        if not auto_select:
            manual_comps = st.text_area("手动输入可比公司代码（逗号分隔）")

    if st.button("运行分析"):
        analyzer = CompsAnalyzer(ticker)

        if auto_select:
            analyzer.find_comparables(max_count=max_comps)
        else:
            for comp in manual_comps.split(','):
                analyzer.add_comparable(comp.strip())

        result = analyzer.run()

        # 展示可比公司表格
        st.subheader("可比公司估值表")
        st.dataframe(result.comps_table, use_container_width=True)

        # 展示统计摘要
        st.subheader("统计摘要")
        st.dataframe(result.summary_stats, use_container_width=True)

        # 展示估值范围
        st.subheader("估值推导")
        st.metric("目标公司每股价值（中位数）", f"¥{result.valuation_range['base']:.2f}")

        # 图表
        st.plotly_chart(result.charts['growth_vs_multiple'])
        st.plotly_chart(result.charts['distribution'])

        # 下载Excel
        st.download_button(
            "下载Excel表格",
            data=result.excel_bytes,
            file_name=f"{ticker}_comps.xlsx"
        )
```

---

## 8. 特殊处理

### 8.1 亏损公司处理

```python
def handle_negative_earnings(companies: List[Dict]) -> Dict[str, List]:
    """
    处理亏损公司（无法计算P/E的情况）

    策略:
    - P/E: 标记为N/A，不参与统计
    - EV/EBITDA: 若EBITDA为正则保留
    - 单独列出亏损公司
    """
    profitable = [c for c in companies if c['net_income'] > 0]
    unprofitable = [c for c in companies if c['net_income'] <= 0]

    return {
        'profitable': profitable,
        'unprofitable': unprofitable,
        'note': f"{len(unprofitable)}家公司亏损，P/E不适用"
    }
```

### 8.2 多元化业务公司

对于业务多元化的公司（如伯克希尔、腾讯），可以：
- 使用SOTP (Sum of The Parts) 分析
- 按业务板块分别找可比公司
- 或调整倍数以反映业务结构差异

---

## 9. 数据源

| 数据项 | 来源 | 方法 |
|--------|------|------|
| 行业分类 | akshare | `stock_industry_classify_sina()` |
| 公司列表 | akshare | `stock_info_a_code_name()` |
| 财务数据 | data_router | `get_financial_statements()` |
| 市值 | data_router | `get_quote()` |

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
