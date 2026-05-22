# DCF 估值模型详细设计

> 优先级: Sprint 1 (P0)
> 模块路径: `plugins/fundamental/dcf_model.py`

---

## 1. 功能概述

DCF (Discounted Cash Flow) 贴现现金流模型是股权估值的核心方法。本模块实现机构级DCF建模能力，输出专业Excel模型。

### 1.1 核心能力
- WACC (Weighted Average Cost of Capital) 计算
- 自由现金流 (FCF) 预测与折现
- 终值 (Terminal Value) 计算
- 敏感性分析 (Sensitivity Analysis)
- 专业Excel输出（蓝/黑/绿配色规范）

### 1.2 输入输出

**输入:**
- 股票代码 (ticker)
- 预测期数 (默认5年)
- 假设参数 (增长率、利润率、资本结构等)

**输出:**
- 企业价值 (Enterprise Value)
- 股权价值 (Equity Value)
- 每股价值 (Value per Share)
- Excel模型文件 (.xlsx)

---

## 2. 架构设计

### 2.1 类图

```python
class DCFEngine:
    """DCF计算核心引擎"""

    def __init__(self, ticker: str, forecast_years: int = 5):
        self.ticker = ticker
        self.forecast_years = forecast_years
        self.historical_data = None
        self.assumptions = None
        self.wacc = None
        self.projections = None
        self.valuation = None

    def load_historical_data(self) -> pd.DataFrame:
        """从data_router加载历史财务数据"""
        pass

    def calculate_wacc(self) -> WACCResult:
        """计算加权平均资本成本"""
        pass

    def build_projections(self) -> ProjectionTable:
        """构建预测期财务数据"""
        pass

    def calculate_terminal_value(self, method: str = "perpetuity") -> float:
        """计算终值 (Gordon Growth 或 Exit Multiple)"""
        pass

    def calculate_valuation(self) -> ValuationResult:
        """计算企业价值和股权价值"""
        pass

    def run_sensitivity(self,
                       wacc_range: Tuple[float, float],
                       growth_range: Tuple[float, float]) -> SensitivityMatrix:
        """运行敏感性分析"""
        pass


class WACCResult(NamedTuple):
    """WACC计算结果"""
    cost_of_equity: float          # 权益成本 (Re)
    cost_of_debt: float            # 债务成本 (Rd)
    debt_ratio: float              # 债务占比 (D/V)
    equity_ratio: float            # 权益占比 (E/V)
    tax_rate: float                # 税率 (T)
    wacc: float                    # 加权平均资本成本
    beta: float                    # Beta系数
    risk_free_rate: float          # 无风险利率
    market_premium: float          # 市场风险溢价


class ProjectionTable:
    """预测期数据表"""

    years: List[int]               # 预测年份
    revenue: List[float]           # 收入
    ebitda: List[float]            # EBITDA
    ebit: List[float]              # EBIT
    nopat: List[float]             # NOPAT
    dna: List[float]               # 折旧摊销
    capex: List[float]             # 资本支出
    nwc_change: List[float]        # 营运资本变动
    fcf: List[float]               # 自由现金流


class ValuationResult:
    """估值结果"""

    enterprise_value: float        # 企业价值 (EV)
    equity_value: float            # 股权价值
    per_share_value: float         # 每股价值
    pv_fcf: List[float]            # 预测期FCF现值
    pv_terminal: float             # 终值现值
    implied_multiple: float        # 隐含EV/EBITDA倍数


class SensitivityMatrix:
    """敏感性矩阵"""

    wacc_values: List[float]       # WACC轴数值
    growth_values: List[float]     # 永续增长率轴数值
    matrix: pd.DataFrame           # 价值矩阵
```

### 2.2 数据流

```
┌─────────────────────────────────────────────────────────────┐
│                      输入层                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 股票代码    │  │ 历史财务数据 │  │ 用户自定义假设      │  │
│  │ (ticker)    │  │ (from DB)   │  │ (growth, margin...) │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
└─────────┼────────────────┼────────────────────┼─────────────┘
          │                │                    │
          ▼                ▼                    ▼
┌─────────────────────────────────────────────────────────────┐
│                      计算层                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ WACC计算    │  │ 财务预测    │  │ 终值计算            │  │
│  │ · CAPM      │  │ · Revenue   │  │ · Gordon Growth     │  │
│  │ · Beta      │  │ · EBITDA    │  │ · Exit Multiple     │  │
│  │ · 资本结构  │  │ · FCF       │  │                     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                    │             │
│         └────────────────┼────────────────────┘             │
│                          ▼                                  │
│                   ┌─────────────┐                           │
│                   │ DCF估值     │                           │
│                   │ · PV(FCF)   │                           │
│                   │ · PV(TV)    │                           │
│                   │ · EV/Equity │                           │
│                   └──────┬──────┘                           │
└─────────────────────────┼───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      输出层                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ 估值结果    │  │ 敏感性分析  │  │ Excel模型           │  │
│  │ (JSON/API)  │  │ (5x5矩阵)   │  │ (.xlsx)             │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心算法

### 3.1 WACC计算

```python
def calculate_wacc(
    risk_free_rate: float = 0.03,      # 10年期国债收益率
    market_premium: float = 0.06,      # 历史股权风险溢价
    beta: float = 1.0,                 # Beta系数
    cost_of_debt: float = 0.05,        # 债务成本
    tax_rate: float = 0.25,            # 有效税率
    debt_ratio: float = 0.3,           # 债务/总价值
) -> float:
    """
    WACC = (E/V) × Re + (D/V) × Rd × (1 - T)

    其中:
    Re = Rf + β × (Rm - Rf)  [CAPM]
    """
    cost_of_equity = risk_free_rate + beta * market_premium
    wacc = (1 - debt_ratio) * cost_of_equity + \
           debt_ratio * cost_of_debt * (1 - tax_rate)
    return wacc
```

**Beta获取策略:**
1. 优先：从数据源直接获取（如akshare、yfinance）
2. 备选：计算过去2-5年个股相对市场的日收益率回归
3. 兜底：使用行业平均Beta

### 3.2 自由现金流计算

```python
def calculate_fcf(
    nopat: float,                      # 税后净营业利润
    dna: float,                        # 折旧与摊销
    capex: float,                      # 资本支出
    nwc_change: float,                 # 营运资本变动
) -> float:
    """
    FCF = NOPAT + D&A - CapEx - ΔNWC

    NOPAT = EBIT × (1 - Tax Rate)
    """
    return nopat + dna - capex - nwc_change


def project_financials(
    base_revenue: float,
    revenue_cagr: float,               # 收入复合年增长率
    ebitda_margin: float,              # EBITDA利润率
    dna_rate: float,                   # D&A占收入比例
    capex_rate: float,                 # CapEx占收入比例
    nwc_rate: float,                   # NWC占收入比例
    years: int = 5,
) -> pd.DataFrame:
    """构建预测期财务数据"""
    projections = []
    revenue = base_revenue

    for year in range(1, years + 1):
        revenue *= (1 + revenue_cagr)
        ebitda = revenue * ebitda_margin
        dna = revenue * dna_rate
        ebit = ebitda - dna
        nopat = ebit * (1 - tax_rate)
        capex = revenue * capex_rate
        nwc = revenue * nwc_rate
        nwc_change = nwc - prev_nwc if year > 1 else nwc
        fcf = nopat + dna - capex - nwc_change

        projections.append({
            'year': year,
            'revenue': revenue,
            'ebitda': ebitda,
            'nopat': nopat,
            'fcf': fcf,
        })
        prev_nwc = nwc

    return pd.DataFrame(projections)
```

### 3.3 终值计算

**方法一: Gordon Growth (永续增长法)**
```python
def terminal_value_gordon(
    final_fcf: float,                  # 预测期最后一年FCF
    wacc: float,
    terminal_growth: float,            # 永续增长率 (通常2-3%)
) -> float:
    """
    TV = FCF(n+1) / (WACC - g)
       = FCF(n) × (1 + g) / (WACC - g)
    """
    return final_fcf * (1 + terminal_growth) / (wacc - terminal_growth)
```

**方法二: Exit Multiple (退出倍数法)**
```python
def terminal_value_exit_multiple(
    final_ebitda: float,
    ev_ebitda_multiple: float,         # 退出时EV/EBITDA倍数
) -> float:
    """
    TV = EBITDA(n) × Exit Multiple
    """
    return final_ebitda * ev_ebitda_multiple
```

### 3.4 DCF估值

```python
def calculate_dcf(
    fcf_projections: List[float],
    wacc: float,
    terminal_value: float,
) -> Dict[str, float]:
    """
    计算DCF估值

    Enterprise Value = Σ(PV of FCF) + PV(Terminal Value)
    """
    # 预测期FCF现值
    pv_fcf = []
    for i, fcf in enumerate(fcf_projections, 1):
        pv = fcf / ((1 + wacc) ** i)
        pv_fcf.append(pv)

    # 终值现值
    n = len(fcf_projections)
    pv_terminal = terminal_value / ((1 + wacc) ** n)

    # 企业价值
    enterprise_value = sum(pv_fcf) + pv_terminal

    return {
        'pv_fcf': pv_fcf,
        'pv_terminal': pv_terminal,
        'enterprise_value': enterprise_value,
    }
```

---

## 4. 敏感性分析

### 4.1 双变量敏感性矩阵

```python
def build_sensitivity_matrix(
    base_wacc: float,
    base_growth: float,
    wacc_range: Tuple[float, float] = (-0.02, 0.02),      # WACC ± 2%
    growth_range: Tuple[float, float] = (-0.01, 0.01),    # Growth ± 1%
    steps: int = 5,
) -> pd.DataFrame:
    """
    构建WACC vs 永续增长率的敏感性矩阵

    输出: 5x5矩阵，展示不同假设下的每股价值
    """
    wacc_values = np.linspace(
        base_wacc + wacc_range[0],
        base_wacc + wacc_range[1],
        steps
    )
    growth_values = np.linspace(
        base_growth + growth_range[0],
        base_growth + growth_range[1],
        steps
    )

    matrix = pd.DataFrame(
        index=[f"{w:.1%}" for w in wacc_values],
        columns=[f"{g:.1%}" for g in growth_values]
    )

    for w in wacc_values:
        for g in growth_values:
            if g >= w:  # 永续增长率必须小于WACC
                matrix.loc[f"{w:.1%}", f"{g:.1%}"] = None
            else:
                tv = terminal_value_gordon(fcf_final, w, g)
                result = calculate_dcf(fcf_projections, w, tv)
                matrix.loc[f"{w:.1%}", f"{g:.1%}"] = result['per_share_value']

    return matrix
```

### 4.2 可视化

- 热力图：颜色深浅表示价值高低
- 龙卷风图：展示各变量对估值的影响程度

---

## 5. Excel输出规范

### 5.1 工作表结构

```
Sheet1: Assumptions (假设)
  - WACC假设
  - 增长假设
  - 利润率假设

Sheet2: Projections (预测)
  - 5年预测期IS/BS/CF关键行
  - FCF计算明细

Sheet3: DCF (估值)
  - 折现计算
  - 终值计算
  - 敏感性矩阵

Sheet4: Output (输出)
  - 估值结果摘要
  - 当前股价对比
```

### 5.2 配色规范

```python
EXCEL_COLOR_SCHEME = {
    'input': '0000FF',        # 蓝色 - 用户输入/假设
    'formula': '000000',      # 黑色 - 公式计算
    'link': '008000',         # 绿色 - 链接其他单元格
    'header': 'D9E1F2',       # 浅蓝 - 表头背景
    'highlight': 'FFF2CC',    # 浅黄 - 重点标注
}
```

---

## 6. 接口定义

### 6.1 Python API

```python
# 快速估值
from plugins.fundamental.dcf_model import DCFEngine

dcf = DCFEngine(ticker='600519.SS', forecast_years=5)
result = dcf.run(
    revenue_cagr=0.10,
    ebitda_margin=0.50,
    terminal_growth=0.03,
)
print(f"每股价值: {result.per_share_value:.2f}")
print(f"当前股价: {result.current_price:.2f}")
print(f"上涨空间: {result.upside:.1%}")

# 导出Excel
dcf.export_excel('600519_dcf_model.xlsx')
```

### 6.2 Streamlit集成

```python
# 在app.py中添加页面
def render_dcf_page():
    st.header("DCF估值模型")

    ticker = st.text_input("股票代码", "600519.SS")

    col1, col2 = st.columns(2)
    with col1:
        revenue_growth = st.slider("收入增长率", 0.0, 0.30, 0.10, 0.01)
        ebitda_margin = st.slider("EBITDA利润率", 0.0, 0.80, 0.50, 0.01)
    with col2:
        terminal_growth = st.slider("永续增长率", 0.0, 0.05, 0.025, 0.005)
        wacc_override = st.number_input("WACC覆盖值(可选)", 0.0, 0.20, 0.08, 0.005)

    if st.button("运行估值"):
        with st.spinner("计算中..."):
            dcf = DCFEngine(ticker)
            result = dcf.run(...)

            # 展示结果
            st.metric("每股内在价值", f"¥{result.per_share_value:.2f}")

            # 展示敏感性矩阵
            st.subheader("敏感性分析")
            st.dataframe(result.sensitivity_matrix, use_container_width=True)

            # 下载Excel
            st.download_button(
                "下载Excel模型",
                data=result.excel_bytes,
                file_name=f"{ticker}_dcf_model.xlsx"
            )
```

---

## 7. 数据源映射

| 数据项 | 来源方法 | 备注 |
|--------|----------|------|
| 历史收入/利润 | `data_router.get_financial_statements()` | 年报数据 |
| Beta | `data_router.get_beta()` 或计算 | 优先数据源 |
| 债务/权益 | `data_router.get_balance_sheet()` | 最新季度 |
| 无风险利率 | 中国10年期国债收益率 | 内置或API |
| 当前股价 | `data_router.get_quote()` | 实时 |

---

## 8. 待解决问题

- [ ] A股财报科目标准化（中美差异）
- [ ] 银行/保险等特殊行业DCF调整
- [ ] 季度数据插值处理
- [ ] 多情景对比功能

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
