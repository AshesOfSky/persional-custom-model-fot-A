# 财务三表联动模型详细设计

> 优先级: Sprint 1 (P0)
> 模块路径: `plugins/fundamental/three_statements.py`

---

## 1. 功能概述

财务三表联动模型整合利润表(Income Statement)、资产负债表(Balance Sheet)和现金流量表(Cash Flow Statement)，通过关键驱动假设实现预测期的联动配平。

### 1.1 核心能力
- 三表联动建模（IS/BS/CF相互勾稽）
- 关键驱动假设（收入增长、利润率、运营资本天数等）
- 自动配平检查（资产负债表配平、现金流量表配平）
- 信用指标计算（Debt/EBITDA、Interest Coverage等）
- 多情景分析（Base/Upside/Downside）

### 1.2 输入输出

**输入:**
- 股票代码 (ticker)
- 历史财务数据（3-5年）
- 驱动假设（增长率、利润率、运营资本天数等）

**输出:**
- 完整的三表预测模型
- 信用指标分析
- 配平检查报告
- Excel模型文件

---

## 2. 架构设计

### 2.1 类图

```python
class ThreeStatementModel:
    """财务三表联动模型"""

    def __init__(self, ticker: str, forecast_years: int = 5):
        self.ticker = ticker
        self.forecast_years = forecast_years
        self.historical = None
        self.assumptions = Assumptions()
        self.income_statement = None
        self.balance_sheet = None
        self.cash_flow = None
        self.credit_metrics = None

    def load_historical_data(self) -> pd.DataFrame:
        """加载历史财务数据"""
        pass

    def set_assumptions(self, assumptions: Assumptions) -> None:
        """设置驱动假设"""
        pass

    def build_income_statement(self) -> IncomeStatement:
        """构建利润表"""
        pass

    def build_balance_sheet(self) -> BalanceSheet:
        """构建资产负债表（需配合CF配平）"""
        pass

    def build_cash_flow(self) -> CashFlowStatement:
        """构建现金流量表"""
        pass

    def balance_check(self) -> BalanceCheckResult:
        """三表配平检查"""
        pass

    def calculate_credit_metrics(self) -> CreditMetrics:
        """计算信用指标"""
        pass

    def run_scenario(self, scenario: str) -> ScenarioResult:
        """运行情景分析"""
        pass

    def export_excel(self, filepath: str) -> None:
        """导出Excel模型"""
        pass


class Assumptions:
    """驱动假设"""

    # 收入增长
    revenue_growth_y1: float
    revenue_growth_y2: float
    revenue_growth_y3: float
    revenue_growth_y4: float
    revenue_growth_y5: float

    # 利润率
    gross_margin: float
    sga_rate: float              # SG&A占收入比例
    dna_rate: float              # D&A占收入比例

    # 运营资本天数
    dso: float                   # 应收账款周转天数
    dio: float                   # 存货周转天数
    dpo: float                   # 应付账款周转天数

    # 资本支出
    capex_rate: float            # CapEx占收入比例

    # 融资
    tax_rate: float
    interest_rate: float


class IncomeStatement:
    """利润表"""

    years: List[int]
    revenue: List[float]
    cogs: List[float]
    gross_profit: List[float]
    sga: List[float]
    ebitda: List[float]
    dna: List[float]
    ebit: List[float]
    interest_expense: List[float]
    ebt: List[float]
    tax: List[float]
    net_income: List[float]

    # 比率
    gross_margin: List[float]
    ebitda_margin: List[float]
    net_margin: List[float]


class BalanceSheet:
    """资产负债表"""

    # 资产
    cash: List[float]
    accounts_receivable: List[float]
    inventory: List[float]
    current_assets: List[float]
    ppe: List[float]
    total_assets: List[float]

    # 负债
    accounts_payable: List[float]
    current_liabilities: List[float]
    total_debt: List[float]
    total_liabilities: List[float]

    # 权益
    shareholders_equity: List[float]
    total_liabilities_equity: List[float]


class CashFlowStatement:
    """现金流量表"""

    # 经营活动
    net_income: List[float]
    dna_add_back: List[float]
    wc_changes: List[float]
    CFO: List[float]              # 经营现金流

    # 投资活动
    capex: List[float]
    CFI: List[float]              # 投资现金流

    # 融资活动
    debt_issuance: List[float]
    dividends: List[float]
    CFF: List[float]              # 融资现金流

    # 净变动
    net_change_cash: List[float]
    beginning_cash: List[float]
    ending_cash: List[float]


class CreditMetrics:
    """信用指标"""

    years: List[int]
    total_debt_ebitda: List[float]      # 总债务/EBITDA
    net_debt_ebitda: List[float]        # 净债务/EBITDA
    interest_coverage: List[float]      # 利息覆盖倍数
    debt_total_capital: List[float]     # 债务/总资本
    current_ratio: List[float]          # 流动比率


class BalanceCheckResult:
    """配平检查结果"""

    bs_balanced: bool                   # 资产负债是否配平
    bs_difference: float                # 差异金额
    cf_reconciled: bool                 # 现金流是否配平
    cf_difference: float
    checks_passed: List[str]
    warnings: List[str]
```

### 2.2 三表勾稽关系

```
┌─────────────────────────────────────────────────────────────────┐
│                        驱动假设                                  │
│  Revenue Growth │ Gross Margin │ DSO/DIO/DPO │ CapEx Rate      │
└────────────────┬────────────────┬─────────────┬─────────────────┘
                 │                │             │
                 ▼                ▼             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      利润表 (Income Statement)                   │
│  Revenue → COGS → Gross Profit                                  │
│                    ↓                                            │
│              SG&A + D&A → EBITDA                                │
│                            ↓                                    │
│                      D&A → EBIT → Interest → EBT → Tax → NI    │
└─────────────────────────────────────────────────────────────────┘
         │                              │
         │ NI                           │ D&A, Interest
         │                              │
         ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    现金流量表 (Cash Flow)                        │
│  CFO: NI + D&A - WC Changes                                     │
│  CFI: CapEx                                                     │
│  CFF: Debt Changes - Dividends                                  │
│       ↓                                                         │
│  Net Change in Cash → Ending Cash ──────────────────────────────┤
└─────────────────────────────────────────────────────────────────┘
         │
         │ Ending Cash (下一年期初)
         ▼
┌─────────────────────────────────────────────────────────────────┐
│                    资产负债表 (Balance Sheet)                    │
│  Assets:                                                        │
│    Cash ←───────────────────────────────────────────────────────┘
│    + AR (from Revenue & DSO)
│    + Inventory (from COGS & DIO)
│    + PP&E (from CapEx & D&A)
│    = Total Assets                                               │
│                                                                 │
│  Liabilities & Equity:                                          │
│    AP (from COGS & DPO)
│    + Debt (from CFF)
│    + Equity (from NI - Dividends)
│    = Total L+E ◄────── 必须等于 Total Assets                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 核心算法

### 3.1 利润表构建

```python
def build_income_statement(
    base_revenue: float,
    assumptions: Assumptions,
    years: int = 5,
) -> IncomeStatement:
    """
    构建预测期利润表
    """
    projections = {
        'revenue': [],
        'gross_profit': [],
        'ebitda': [],
        'ebit': [],
        'net_income': [],
    }

    revenue = base_revenue
    growth_rates = [
        assumptions.revenue_growth_y1,
        assumptions.revenue_growth_y2,
        assumptions.revenue_growth_y3,
        assumptions.revenue_growth_y4,
        assumptions.revenue_growth_y5,
    ]

    for i in range(years):
        # 收入
        revenue *= (1 + growth_rates[i])
        projections['revenue'].append(revenue)

        # 毛利润
        cogs = revenue * (1 - assumptions.gross_margin)
        gross_profit = revenue - cogs
        projections['gross_profit'].append(gross_profit)

        # SG&A
        sga = revenue * assumptions.sga_rate

        # EBITDA
        ebitda = gross_profit - sga
        projections['ebitda'].append(ebitda)

        # D&A
        dna = revenue * assumptions.dna_rate

        # EBIT
        ebit = ebitda - dna
        projections['ebit'].append(ebit)

        # 利息费用（需要先知道债务，这里用迭代或简化处理）
        interest = 0  # 占位，实际应从BS联动

        # EBT
        ebt = ebit - interest

        # 税收
        tax = ebt * assumptions.tax_rate

        # 净利润
        net_income = ebt - tax
        projections['net_income'].append(net_income)

    return IncomeStatement(**projections)
```

### 3.2 运营资本计算

```python
def calculate_working_capital(
    revenue: float,
    cogs: float,
    assumptions: Assumptions,
) -> Dict[str, float]:
    """
    计算运营资本项目

    公式:
    AR = Revenue × (DSO / 365)
    Inventory = COGS × (DIO / 365)
    AP = COGS × (DPO / 365)
    NWC = AR + Inventory - AP
    """
    ar = revenue * (assumptions.dso / 365)
    inventory = cogs * (assumptions.dio / 365)
    ap = cogs * (assumptions.dpo / 365)

    nwc = ar + inventory - ap

    return {
        'accounts_receivable': ar,
        'inventory': inventory,
        'accounts_payable': ap,
        'net_working_capital': nwc,
    }


def calculate_nwc_change(
    current_nwc: float,
    previous_nwc: float,
) -> float:
    """
    计算运营资本变动

    NWC增加 = 现金流出 (负)
    NWC减少 = 现金流入 (正)
    """
    return previous_nwc - current_nwc
```

### 3.3 资产负债表配平（循环计算）

```python
def build_balance_sheet_with_cash_sweep(
    income_stmt: IncomeStatement,
    assumptions: Assumptions,
    initial_balance: Dict[str, float],
) -> BalanceSheet:
    """
    构建资产负债表（含现金清扫Cash Sweep）

    逻辑:
    1. 先计算非现金项目（AR, Inventory, PP&E, AP, Debt固定）
    2. 从CF计算现金变动
    3. 检查资产负债是否配平
    4. 如有差额，调整现金或循环贷款
    """
    projections = {
        'cash': [],
        'accounts_receivable': [],
        'inventory': [],
        'ppe': [],
        'accounts_payable': [],
        'total_debt': [],
        'shareholders_equity': [],
    }

    # 期初值
    cash = initial_balance['cash']
    ppe = initial_balance['ppe']
    equity = initial_balance['shareholders_equity']
    debt = initial_balance['total_debt']

    prev_nwc = initial_balance.get('net_working_capital', 0)

    for i in range(len(income_stmt.revenue)):
        revenue = income_stmt.revenue[i]
        cogs = revenue * (1 - assumptions.gross_margin)
        ebitda = income_stmt.ebitda[i]
        dna = revenue * assumptions.dna_rate
        net_income = income_stmt.net_income[i]

        # 运营资本
        wc = calculate_working_capital(revenue, cogs, assumptions)
        projections['accounts_receivable'].append(wc['accounts_receivable'])
        projections['inventory'].append(wc['inventory'])
        projections['accounts_payable'].append(wc['accounts_payable'])

        # NWC变动
        nwc_change = wc['net_working_capital'] - prev_nwc
        prev_nwc = wc['net_working_capital']

        # CapEx
        capex = revenue * assumptions.capex_rate

        # PP&E
        ppe = ppe + capex - dna
        projections['ppe'].append(ppe)

        # 现金流（简化版，不含利息）
        cfo = net_income + dna - nwc_change
        cfi = -capex

        # 假设债务不变，分红为0（可扩展）
        cff = 0

        # 现金变动
        cash_change = cfo + cfi + cff
        cash += cash_change
        projections['cash'].append(cash)

        # 权益累计
        equity += net_income  # 假设无分红
        projections['shareholders_equity'].append(equity)

        # 债务（简化：保持不变，或根据现金调整）
        projections['total_debt'].append(debt)

    return BalanceSheet(**projections)
```

### 3.4 配平检查

```python
def balance_check(
    income_stmt: IncomeStatement,
    balance_sheet: BalanceSheet,
    cash_flow: CashFlowStatement,
) -> BalanceCheckResult:
    """
    三表配平检查
    """
    checks = []
    warnings = []

    for i in range(len(income_stmt.years)):
        year = income_stmt.years[i]

        # 1. 资产负债配平检查
        total_assets = (
            balance_sheet.cash[i] +
            balance_sheet.accounts_receivable[i] +
            balance_sheet.inventory[i] +
            balance_sheet.ppe[i]
        )

        total_liabilities_equity = (
            balance_sheet.accounts_payable[i] +
            balance_sheet.total_debt[i] +
            balance_sheet.shareholders_equity[i]
        )

        bs_diff = total_assets - total_liabilities_equity
        bs_balanced = abs(bs_diff) < 0.01  # 允许0.01的舍入误差

        # 2. 现金流量表配平检查
        cf_change = (
            cash_flow.CFO[i] +
            cash_flow.CFI[i] +
            cash_flow.CFF[i]
        )

        bs_cash_change = balance_sheet.cash[i] - (balance_sheet.cash[i-1] if i > 0 else 0)
        cf_diff = cf_change - bs_cash_change
        cf_reconciled = abs(cf_diff) < 0.01

        # 3. 净利润勾稽检查
        if abs(income_stmt.net_income[i] - cash_flow.net_income[i]) > 0.01:
            warnings.append(f"Year {year}: IS与CF净利润不一致")

        checks.append({
            'year': year,
            'bs_balanced': bs_balanced,
            'bs_difference': bs_diff,
            'cf_reconciled': cf_reconciled,
            'cf_difference': cf_diff,
        })

    return BalanceCheckResult(
        bs_balanced=all(c['bs_balanced'] for c in checks),
        bs_difference=max(abs(c['bs_difference']) for c in checks),
        cf_reconciled=all(c['cf_reconciled'] for c in checks),
        cf_difference=max(abs(c['cf_difference']) for c in checks),
        checks_passed=[c['year'] for c in checks if c['bs_balanced'] and c['cf_reconciled']],
        warnings=warnings,
    )
```

### 3.5 信用指标计算

```python
def calculate_credit_metrics(
    income_stmt: IncomeStatement,
    balance_sheet: BalanceSheet,
) -> CreditMetrics:
    """
    计算信用分析指标
    """
    metrics = {
        'years': income_stmt.years,
        'total_debt_ebitda': [],
        'net_debt_ebitda': [],
        'interest_coverage': [],
        'debt_total_capital': [],
        'current_ratio': [],
    }

    for i in range(len(income_stmt.years)):
        ebitda = income_stmt.ebitda[i]
        debt = balance_sheet.total_debt[i]
        cash = balance_sheet.cash[i]
        equity = balance_sheet.shareholders_equity[i]

        # 总债务/EBITDA
        total_debt_ebitda = debt / ebitda if ebitda > 0 else None
        metrics['total_debt_ebitda'].append(total_debt_ebitda)

        # 净债务/EBITDA
        net_debt = max(0, debt - cash)
        net_debt_ebitda = net_debt / ebitda if ebitda > 0 else None
        metrics['net_debt_ebitda'].append(net_debt_ebitda)

        # 利息覆盖倍数
        interest = income_stmt.ebit[i] - income_stmt.ebt[i] if hasattr(income_stmt, 'ebt') else 0
        interest_coverage = ebitda / interest if interest > 0 else None
        metrics['interest_coverage'].append(interest_coverage)

        # 债务/总资本
        total_capital = debt + equity
        debt_total_capital = debt / total_capital if total_capital > 0 else None
        metrics['debt_total_capital'].append(debt_total_capital)

        # 流动比率
        current_assets = balance_sheet.cash[i] + balance_sheet.accounts_receivable[i] + balance_sheet.inventory[i]
        current_liabilities = balance_sheet.accounts_payable[i]  # 简化
        current_ratio = current_assets / current_liabilities if current_liabilities > 0 else None
        metrics['current_ratio'].append(current_ratio)

    return CreditMetrics(**metrics)
```

---

## 4. 情景分析

```python
class ScenarioAssumptions:
    """情景假设"""

    BASE = Assumptions(
        revenue_growth_y1=0.10, revenue_growth_y2=0.10,
        revenue_growth_y3=0.08, revenue_growth_y4=0.08, revenue_growth_y5=0.06,
        gross_margin=0.40, sga_rate=0.15, dna_rate=0.05,
        dso=60, dio=45, dpo=30,
        capex_rate=0.05, tax_rate=0.25,
    )

    UPSIDE = Assumptions(
        revenue_growth_y1=0.15, revenue_growth_y2=0.15,
        revenue_growth_y3=0.12, revenue_growth_y4=0.10, revenue_growth_y5=0.08,
        gross_margin=0.45, sga_rate=0.14, dna_rate=0.05,
        dso=55, dio=40, dpo=35,
        capex_rate=0.06, tax_rate=0.25,
    )

    DOWNSIDE = Assumptions(
        revenue_growth_y1=0.05, revenue_growth_y2=0.05,
        revenue_growth_y3=0.03, revenue_growth_y4=0.03, revenue_growth_y5=0.02,
        gross_margin=0.35, sga_rate=0.16, dna_rate=0.05,
        dso=70, dio=55, dpo=25,
        capex_rate=0.04, tax_rate=0.25,
    )


def run_all_scenarios(
    ticker: str,
) -> Dict[str, ScenarioResult]:
    """
    运行三种情景分析
    """
    results = {}

    for scenario_name, assumptions in [
        ('Base', ScenarioAssumptions.BASE),
        ('Upside', ScenarioAssumptions.UPSIDE),
        ('Downside', ScenarioAssumptions.DOWNSIDE),
    ]:
        model = ThreeStatementModel(ticker)
        model.set_assumptions(assumptions)
        model.build_income_statement()
        model.build_balance_sheet()
        model.build_cash_flow()

        results[scenario_name] = ScenarioResult(
            assumptions=assumptions,
            income_statement=model.income_statement,
            balance_sheet=model.balance_sheet,
            credit_metrics=model.calculate_credit_metrics(),
        )

    return results
```

---

## 5. Excel输出规范

### 5.1 工作表结构

```
Sheet1: Assumptions (假设)
  - 收入增长率 (Y1-Y5)
  - 利润率假设
  - 运营资本天数
  - 资本支出率

Sheet2: Income Statement (利润表)
  | 项目 | Y1A | Y2A | Y3A | Y1E | Y2E | Y3E | Y4E | Y5E |

Sheet3: Balance Sheet (资产负债表)
  | 项目 | Y1A | Y2A | Y3A | Y1E | Y2E | Y3E | Y4E | Y5E |

Sheet4: Cash Flow (现金流量表)
  | 项目 | Y1A | Y2A | Y3A | Y1E | Y2E | Y3E | Y4E | Y5E |

Sheet5: Credit Metrics (信用指标)
  | 指标 | Y1E | Y2E | Y3E | Y4E | Y5E |

Sheet6: Balance Check (配平检查)
  - 每期的资产负债差异
  - 现金流勾稽检查
```

### 5.2 颜色规范

- 蓝色字体：输入/假设
- 黑色字体：公式计算
- 绿色字体：链接其他工作表
- 黄色背景：需要关注的异常

---

## 6. 接口定义

### 6.1 Python API

```python
from plugins.fundamental.three_statements import ThreeStatementModel, Assumptions

# 创建模型
model = ThreeStatementModel(ticker='600519.SS', forecast_years=5)

# 设置假设
assumptions = Assumptions(
    revenue_growth_y1=0.10,
    revenue_growth_y2=0.10,
    revenue_growth_y3=0.08,
    revenue_growth_y4=0.08,
    revenue_growth_y5=0.06,
    gross_margin=0.50,
    sga_rate=0.10,
    dna_rate=0.03,
    dso=30, dio=60, dpo=45,
    capex_rate=0.05,
    tax_rate=0.25,
)
model.set_assumptions(assumptions)

# 构建模型
model.build_income_statement()
model.build_balance_sheet()
model.build_cash_flow()

# 配平检查
check = model.balance_check()
print(f"资产负债表配平: {check.bs_balanced}")
print(f"现金流配平: {check.cf_reconciled}")

# 信用指标
credit = model.calculate_credit_metrics()
print(f"债务/EBITDA: {credit.total_debt_ebitda[-1]:.2f}x")

# 导出Excel
model.export_excel('600519_3statements.xlsx')
```

### 6.2 Streamlit集成

```python
def render_three_statements_page():
    st.header("财务三表联动模型")

    ticker = st.text_input("股票代码", "600519.SS")

    with st.expander("驱动假设"):
        col1, col2 = st.columns(2)
        with col1:
            growth_y1 = st.slider("Y1 收入增长", 0.0, 0.50, 0.10, 0.01)
            gross_margin = st.slider("毛利率", 0.0, 0.80, 0.50, 0.01)
            dso = st.number_input("应收账款天数(DSO)", 0, 180, 30)
        with col2:
            capex_rate = st.slider("CapEx/收入", 0.0, 0.20, 0.05, 0.01)
            tax_rate = st.slider("税率", 0.0, 0.40, 0.25, 0.01)
            dio = st.number_input("存货天数(DIO)", 0, 180, 60)

    if st.button("生成模型"):
        with st.spinner("构建模型中..."):
            model = build_model(ticker, assumptions)

            # 展示利润表
            st.subheader("利润表预测")
            st.dataframe(model.income_statement.to_df())

            # 配平检查
            check = model.balance_check()
            if check.bs_balanced and check.cf_reconciled:
                st.success("✅ 三表配平检查通过")
            else:
                st.error("❌ 配平检查未通过")
                st.write(f"资产负债差异: {check.bs_difference:.2f}")

            # 信用指标
            st.subheader("信用指标")
            credit = model.calculate_credit_metrics()
            st.line_chart({
                '债务/EBITDA': credit.total_debt_ebitda,
                '利息覆盖': credit.interest_coverage,
            })
```

---

## 7. 数据源

| 数据项 | 来源 | 方法 |
|--------|------|------|
| 历史利润表 | data_router | `get_income_statement()` |
| 历史资产负债表 | data_router | `get_balance_sheet()` |
| 历史现金流量表 | data_router | `get_cash_flow()` |
| 行业平均假设 | 内置数据库 | 按GICS/SW行业分类 |

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
