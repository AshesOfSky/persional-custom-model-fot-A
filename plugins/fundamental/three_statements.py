"""
财务三表联动模型插件（华尔街化升级 v2.0）

升级点（B.1.3）：
1. Cash Sweep 循环求解（负现金自动拉短期债务，迭代收敛）
2. 配平容差：绝对值 → 相对值 0.1%；失败自动诊断
3. Scenario 假设自动推导（Base=历史均值，Upside=+1σ，Downside=-1σ）
4. 历史 Normalize（过去5年毛利率/SGA率/营运资本天数均值）
5. Altman Z-Score + S&P 评级映射
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Assumptions:
    """驱动假设"""
    revenue_growth_y1: float = 0.10
    revenue_growth_y2: float = 0.10
    revenue_growth_y3: float = 0.08
    revenue_growth_y4: float = 0.08
    revenue_growth_y5: float = 0.06

    gross_margin: float = 0.40
    sga_rate: float = 0.15
    dna_rate: float = 0.05

    dso: float = 60
    dio: float = 45
    dpo: float = 30

    capex_rate: float = 0.05
    tax_rate: float = 0.25
    interest_rate: float = 0.05

    # Cash Sweep 参数
    min_cash_pct_revenue: float = 0.10  # 最低现金占收入比例

    def get_growth_rates(self) -> List[float]:
        return [
            self.revenue_growth_y1, self.revenue_growth_y2,
            self.revenue_growth_y3, self.revenue_growth_y4,
            self.revenue_growth_y5,
        ]

    @classmethod
    def from_historical_stats(cls, stats: Dict, scenario_shift: float = 0.0) -> "Assumptions":
        """从历史统计生成假设，scenario_shift 为上下调幅度"""
        return cls(
            revenue_growth_y1=stats.get("revenue_growth_mean", 0.10) + scenario_shift,
            revenue_growth_y2=stats.get("revenue_growth_mean", 0.10) + scenario_shift * 0.8,
            revenue_growth_y3=stats.get("revenue_growth_mean", 0.08) + scenario_shift * 0.6,
            revenue_growth_y4=stats.get("revenue_growth_mean", 0.08) + scenario_shift * 0.4,
            revenue_growth_y5=stats.get("revenue_growth_mean", 0.06) + scenario_shift * 0.2,
            gross_margin=stats.get("gross_margin_mean", 0.40) + scenario_shift * 0.3,
            sga_rate=stats.get("sga_rate_mean", 0.15) - scenario_shift * 0.1,
            dna_rate=stats.get("dna_rate_mean", 0.05),
            dso=stats.get("dso_mean", 60),
            dio=stats.get("dio_mean", 45),
            dpo=stats.get("dpo_mean", 30),
            capex_rate=stats.get("capex_rate_mean", 0.05),
            tax_rate=stats.get("tax_rate_mean", 0.25),
            interest_rate=stats.get("interest_rate_mean", 0.05),
        )


@dataclass
class IncomeStatement:
    """利润表"""
    years: List[int] = field(default_factory=list)
    revenue: List[float] = field(default_factory=list)
    cogs: List[float] = field(default_factory=list)
    gross_profit: List[float] = field(default_factory=list)
    sga: List[float] = field(default_factory=list)
    ebitda: List[float] = field(default_factory=list)
    dna: List[float] = field(default_factory=list)
    ebit: List[float] = field(default_factory=list)
    interest_expense: List[float] = field(default_factory=list)
    ebt: List[float] = field(default_factory=list)
    tax: List[float] = field(default_factory=list)
    net_income: List[float] = field(default_factory=list)
    net_income_attributable: List[float] = field(default_factory=list)
    minority_interest: List[float] = field(default_factory=list)

    gross_margin: List[float] = field(default_factory=list)
    ebitda_margin: List[float] = field(default_factory=list)
    net_margin: List[float] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            'Year': self.years,
            'Revenue': self.revenue,
            'COGS': self.cogs,
            'Gross Profit': self.gross_profit,
            'Gross Margin': self.gross_margin,
            'SG&A': self.sga,
            'EBITDA': self.ebitda,
            'EBITDA Margin': self.ebitda_margin,
            'D&A': self.dna,
            'EBIT': self.ebit,
            'Interest': self.interest_expense,
            'EBT': self.ebt,
            'Tax': self.tax,
            'Net Income': self.net_income,
            'Minority Interest': self.minority_interest,
            'Net Income Attr.': self.net_income_attributable,
            'Net Margin': self.net_margin,
        })


@dataclass
class BalanceSheet:
    """资产负债表"""
    years: List[int] = field(default_factory=list)
    cash: List[float] = field(default_factory=list)
    accounts_receivable: List[float] = field(default_factory=list)
    inventory: List[float] = field(default_factory=list)
    current_assets: List[float] = field(default_factory=list)
    ppe: List[float] = field(default_factory=list)
    total_assets: List[float] = field(default_factory=list)
    accounts_payable: List[float] = field(default_factory=list)
    current_liabilities: List[float] = field(default_factory=list)
    short_term_debt: List[float] = field(default_factory=list)
    total_debt: List[float] = field(default_factory=list)
    total_liabilities: List[float] = field(default_factory=list)
    shareholders_equity: List[float] = field(default_factory=list)
    minority_interest_bs: List[float] = field(default_factory=list)
    total_liabilities_equity: List[float] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            'Year': self.years,
            'Cash': self.cash,
            'AR': self.accounts_receivable,
            'Inventory': self.inventory,
            'Current Assets': self.current_assets,
            'PP&E': self.ppe,
            'Total Assets': self.total_assets,
            'AP': self.accounts_payable,
            'Current Liabilities': self.current_liabilities,
            'Short-term Debt': self.short_term_debt,
            'Total Debt': self.total_debt,
            'Total Liabilities': self.total_liabilities,
            'Shareholders Equity': self.shareholders_equity,
            'Minority Interest': self.minority_interest_bs,
            'Total L+E': self.total_liabilities_equity,
        })


@dataclass
class CashFlowStatement:
    """现金流量表"""
    years: List[int] = field(default_factory=list)
    net_income: List[float] = field(default_factory=list)
    dna_add_back: List[float] = field(default_factory=list)
    wc_changes: List[float] = field(default_factory=list)
    cfo: List[float] = field(default_factory=list)
    capex: List[float] = field(default_factory=list)
    cfi: List[float] = field(default_factory=list)
    debt_issuance: List[float] = field(default_factory=list)
    debt_repayment: List[float] = field(default_factory=list)
    dividends: List[float] = field(default_factory=list)
    cff: List[float] = field(default_factory=list)
    net_change_cash: List[float] = field(default_factory=list)
    beginning_cash: List[float] = field(default_factory=list)
    ending_cash: List[float] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            'Year': self.years,
            'Net Income': self.net_income,
            'D&A Add Back': self.dna_add_back,
            'WC Changes': self.wc_changes,
            'CFO': self.cfo,
            'CapEx': self.capex,
            'CFI': self.cfi,
            'Debt Issuance': self.debt_issuance,
            'Debt Repayment': self.debt_repayment,
            'Dividends': self.dividends,
            'CFF': self.cff,
            'Net Change in Cash': self.net_change_cash,
            'Beginning Cash': self.beginning_cash,
            'Ending Cash': self.ending_cash,
        })


@dataclass
class CreditMetrics:
    """信用指标（含 Altman Z-Score）"""
    years: List[int] = field(default_factory=list)
    total_debt_ebitda: List[float] = field(default_factory=list)
    net_debt_ebitda: List[float] = field(default_factory=list)
    interest_coverage: List[float] = field(default_factory=list)
    debt_total_capital: List[float] = field(default_factory=list)
    current_ratio: List[float] = field(default_factory=list)
    # Altman Z-Score
    altman_z: List[float] = field(default_factory=list)
    altman_rating: List[str] = field(default_factory=list)
    # S&P 风格映射
    sp_rating: List[str] = field(default_factory=list)
    fixed_charge_coverage: List[float] = field(default_factory=list)

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            'Year': self.years,
            'Total Debt/EBITDA': self.total_debt_ebitda,
            'Net Debt/EBITDA': self.net_debt_ebitda,
            'Interest Coverage': self.interest_coverage,
            'Debt/Total Capital': self.debt_total_capital,
            'Current Ratio': self.current_ratio,
            'Altman Z': self.altman_z,
            'Altman Rating': self.altman_rating,
            'S&P Rating': self.sp_rating,
            'Fixed Charge Coverage': self.fixed_charge_coverage,
        })


@dataclass
class BalanceCheckResult:
    """配平检查结果（含诊断）"""
    bs_balanced: bool = False
    bs_difference: float = 0.0
    bs_difference_pct: float = 0.0
    cf_reconciled: bool = False
    cf_difference: float = 0.0
    cf_difference_pct: float = 0.0
    checks_passed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    year_checks: List[Dict] = field(default_factory=list)


@dataclass
class ScenarioResult:
    """情景分析结果"""
    scenario_name: str = ""
    assumptions: Assumptions = field(default_factory=Assumptions)
    income_statement: Optional[IncomeStatement] = None
    balance_sheet: Optional[BalanceSheet] = None
    cash_flow: Optional[CashFlowStatement] = None
    credit_metrics: Optional[CreditMetrics] = None


class ScenarioType(Enum):
    BASE = "Base"
    UPSIDE = "Upside"
    DOWNSIDE = "Downside"


class ThreeStatementModel:
    """
    财务三表联动模型（华尔街化升级）
    """

    EXCEL_COLORS = {
        'input': '0000FF',
        'formula': '000000',
        'link': '008000',
        'header': 'D9E1F2',
        'highlight': 'FFF2CC',
        'warning': 'FF6B6B',
    }

    def __init__(self, ticker: str, forecast_years: int = 5, data_router=None):
        self.ticker = ticker
        self.forecast_years = forecast_years
        self.data_router = data_router
        self.historical: Optional[pd.DataFrame] = None
        self.assumptions: Optional[Assumptions] = None
        self.income_statement: Optional[IncomeStatement] = None
        self.balance_sheet: Optional[BalanceSheet] = None
        self.cash_flow: Optional[CashFlowStatement] = None
        self.credit_metrics: Optional[CreditMetrics] = None
        self.balance_check_result: Optional[BalanceCheckResult] = None
        self.historical_stats: Dict = {}

        self.initial_balance: Dict[str, float] = {
            'cash': 0, 'accounts_receivable': 0, 'inventory': 0,
            'ppe': 0, 'accounts_payable': 0, 'short_term_debt': 0,
            'total_debt': 0, 'shareholders_equity': 0,
            'minority_interest': 0,
        }

        self._income_df: Optional[pd.DataFrame] = None
        self._balance_df: Optional[pd.DataFrame] = None
        self._cashflow_df: Optional[pd.DataFrame] = None

    def _ensure_router(self):
        if self.data_router is None:
            from modules.data_router import DataRouter
            self.data_router = DataRouter()

    def load_historical_data(self) -> pd.DataFrame:
        """加载历史财务数据（接入真实财报）"""
        self._ensure_router()
        try:
            income = self.data_router.get_income_statement(self.ticker)
            balance = self.data_router.get_balance_sheet(self.ticker)
            cashflow = self.data_router.get_cash_flow(self.ticker)

            self._income_df = income
            self._balance_df = balance
            self._cashflow_df = cashflow

            if not income.empty or not balance.empty:
                self.historical = pd.concat([income, balance, cashflow], axis=1)
                self.historical = self.historical.loc[:, ~self.historical.columns.duplicated()]

                # 设置初始资产负债表（最近一期，亿元）
                if not balance.empty:
                    last = balance.iloc[-1]
                    self.initial_balance['cash'] = self._safe_get(last, 'cash') / 1e8
                    self.initial_balance['accounts_receivable'] = self._safe_get(last, 'accounts_receivable') / 1e8
                    self.initial_balance['inventory'] = self._safe_get(last, 'inventory') / 1e8
                    self.initial_balance['ppe'] = self._safe_get(last, 'ppe') / 1e8
                    self.initial_balance['accounts_payable'] = self._safe_get(last, 'accounts_payable') / 1e8
                    self.initial_balance['short_term_debt'] = self._safe_get(last, 'short_term_debt') / 1e8
                    self.initial_balance['total_debt'] = self._safe_get(last, 'total_debt') / 1e8
                    self.initial_balance['shareholders_equity'] = self._safe_get(last, 'shareholders_equity') / 1e8
                    self.initial_balance['minority_interest'] = self._safe_get(last, 'minority_interest_bs') / 1e8

                # 计算历史统计
                self._compute_historical_stats()
            else:
                self._use_sample_data()

            return self.historical
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
            self._use_sample_data()
            return self.historical

    def _safe_get(self, series: pd.Series, col: str, default: float = 0.0) -> float:
        val = series.get(col)
        if val is not None and pd.notna(val):
            return float(val)
        return default

    def _use_sample_data(self):
        """使用示例数据"""
        self.historical = pd.DataFrame()
        self.initial_balance = {
            'cash': 100, 'accounts_receivable': 150, 'inventory': 80,
            'ppe': 500, 'accounts_payable': 100, 'short_term_debt': 0,
            'total_debt': 300, 'shareholders_equity': 430,
            'minority_interest': 0,
        }
        self.historical_stats = {}

    def _compute_historical_stats(self):
        """从历史数据计算均值和标准差，用于 Scenario 自动推导"""
        stats = {}
        if self._income_df is not None and not self._income_df.empty:
            df = self._income_df
            # Revenue growth
            if 'revenue' in df.columns:
                rev = df['revenue'].dropna()
                if len(rev) >= 2:
                    growth_rates = rev.pct_change().dropna().values
                    stats['revenue_growth_mean'] = float(np.mean(growth_rates))
                    stats['revenue_growth_std'] = float(np.std(growth_rates))

            # Gross margin
            if 'revenue' in df.columns and 'gross_profit' in df.columns:
                margins = (df['gross_profit'] / df['revenue']).dropna()
                if len(margins) > 0:
                    stats['gross_margin_mean'] = float(np.mean(margins))
                    stats['gross_margin_std'] = float(np.std(margins))

            # SG&A rate
            if 'revenue' in df.columns and 'sga_expenses' in df.columns:
                rates = (df['sga_expenses'] / df['revenue']).dropna()
                if len(rates) > 0:
                    stats['sga_rate_mean'] = float(np.mean(rates))

            # D&A rate
            if 'revenue' in df.columns and 'depreciation_amortization' in df.columns:
                rates = (df['depreciation_amortization'] / df['revenue']).dropna()
                if len(rates) > 0:
                    stats['dna_rate_mean'] = float(np.mean(rates))

            # Tax rate
            if 'pretax_income' in df.columns and 'income_tax' in df.columns:
                rates = (df['income_tax'] / df['pretax_income']).dropna()
                rates = rates[rates > 0]
                if len(rates) > 0:
                    stats['tax_rate_mean'] = float(np.mean(rates))

        if self._balance_df is not None and not self._balance_df.empty:
            df = self._balance_df
            # DSO
            if 'accounts_receivable' in df.columns and 'revenue' in df.columns:
                dso = (df['accounts_receivable'] / df['revenue'] * 365).dropna()
                if len(dso) > 0:
                    stats['dso_mean'] = float(np.mean(dso))

            # DIO (需要 COGS，用 revenue * 0.6 近似)
            if 'inventory' in df.columns:
                cogs_approx = df.get('revenue', pd.Series([0]*len(df))) * 0.6
                dio = (df['inventory'] / cogs_approx * 365).dropna()
                dio = dio[dio > 0]
                if len(dio) > 0:
                    stats['dio_mean'] = float(np.mean(dio))

            # DPO
            if 'accounts_payable' in df.columns:
                cogs_approx = df.get('revenue', pd.Series([0]*len(df))) * 0.6
                dpo = (df['accounts_payable'] / cogs_approx * 365).dropna()
                dpo = dpo[dpo > 0]
                if len(dpo) > 0:
                    stats['dpo_mean'] = float(np.mean(dpo))

        if self._cashflow_df is not None and not self._cashflow_df.empty:
            df = self._cashflow_df
            # CapEx rate
            if 'capex' in df.columns and 'revenue' in df.columns:
                rates = (df['capex'].abs() / df['revenue']).dropna()
                if len(rates) > 0:
                    stats['capex_rate_mean'] = float(np.mean(rates))

        # 利率（简化）
        stats['interest_rate_mean'] = 0.05

        self.historical_stats = stats
        logger.info(f"Historical stats computed for {self.ticker}: {list(stats.keys())}")

    def derive_assumptions(self, scenario: ScenarioType) -> Assumptions:
        """
        从历史统计自动推导 Scenario 假设
        Base = 历史均值
        Upside = +1σ (增长率)
        Downside = -1σ (增长率)
        """
        if not self.historical_stats:
            self.load_historical_data()

        std = self.historical_stats.get('revenue_growth_std', 0.03)

        if scenario == ScenarioType.BASE:
            return Assumptions.from_historical_stats(self.historical_stats, scenario_shift=0.0)
        elif scenario == ScenarioType.UPSIDE:
            return Assumptions.from_historical_stats(self.historical_stats, scenario_shift=std)
        elif scenario == ScenarioType.DOWNSIDE:
            return Assumptions.from_historical_stats(self.historical_stats, scenario_shift=-std)
        return Assumptions()

    def set_assumptions(self, assumptions: Assumptions) -> None:
        self.assumptions = assumptions

    def build_income_statement(self, base_revenue: float = None) -> IncomeStatement:
        """构建利润表"""
        if base_revenue is None:
            base_revenue = 1000  # 默认亿元

        if self.assumptions is None:
            self.assumptions = Assumptions()

        growth_rates = self.assumptions.get_growth_rates()
        years = list(range(1, self.forecast_years + 1))

        projections = {
            'years': years, 'revenue': [], 'cogs': [], 'gross_profit': [],
            'sga': [], 'ebitda': [], 'dna': [], 'ebit': [],
            'interest_expense': [], 'ebt': [], 'tax': [], 'net_income': [],
            'net_income_attributable': [], 'minority_interest': [],
            'gross_margin': [], 'ebitda_margin': [], 'net_margin': [],
        }

        revenue = base_revenue
        interest_expense = self.initial_balance.get('total_debt', 0) * self.assumptions.interest_rate

        for i, year in enumerate(years):
            revenue *= (1 + growth_rates[i])
            projections['revenue'].append(revenue)

            cogs = revenue * (1 - self.assumptions.gross_margin)
            gross_profit = revenue - cogs
            projections['cogs'].append(cogs)
            projections['gross_profit'].append(gross_profit)
            projections['gross_margin'].append(self.assumptions.gross_margin)

            sga = revenue * self.assumptions.sga_rate
            projections['sga'].append(sga)

            ebitda = gross_profit - sga
            projections['ebitda'].append(ebitda)
            projections['ebitda_margin'].append(ebitda / revenue if revenue > 0 else 0)

            dna = revenue * self.assumptions.dna_rate
            projections['dna'].append(dna)

            ebit = ebitda - dna
            projections['ebit'].append(ebit)

            projections['interest_expense'].append(interest_expense)

            ebt = ebit - interest_expense
            projections['ebt'].append(ebt)

            tax = max(0, ebt * self.assumptions.tax_rate)
            projections['tax'].append(tax)

            net_income = ebt - tax
            projections['net_income'].append(net_income)
            projections['net_margin'].append(net_income / revenue if revenue > 0 else 0)

            # A股：少数股东损益 = 总净利润 - 归母净利润（简化：10%）
            mi = net_income * 0.10 if net_income > 0 else 0
            projections['minority_interest'].append(mi)
            projections['net_income_attributable'].append(net_income - mi)

        self.income_statement = IncomeStatement(**projections)
        return self.income_statement

    def calculate_working_capital(self, revenue: float, cogs: float) -> Dict[str, float]:
        """计算运营资本"""
        ar = revenue * (self.assumptions.dso / 365)
        inventory = cogs * (self.assumptions.dio / 365)
        ap = cogs * (self.assumptions.dpo / 365)
        nwc = ar + inventory - ap
        return {'accounts_receivable': ar, 'inventory': inventory,
                'accounts_payable': ap, 'net_working_capital': nwc}

    def build_balance_sheet(self) -> BalanceSheet:
        """
        构建资产负债表（含 Cash Sweep 循环求解）
        若期末现金 < 10% × Revenue，自动拉短期债务补足，重算利息，迭代收敛
        """
        if self.income_statement is None:
            self.build_income_statement()

        years = list(range(1, self.forecast_years + 1))

        projections = {
            'years': years, 'cash': [], 'accounts_receivable': [], 'inventory': [],
            'current_assets': [], 'ppe': [], 'total_assets': [],
            'accounts_payable': [], 'current_liabilities': [], 'short_term_debt': [],
            'total_debt': [], 'total_liabilities': [],
            'shareholders_equity': [], 'minority_interest_bs': [],
            'total_liabilities_equity': [],
        }

        cash = self.initial_balance['cash']
        ppe = self.initial_balance['ppe']
        equity = self.initial_balance['shareholders_equity']
        long_term_debt = self.initial_balance['total_debt'] - self.initial_balance.get('short_term_debt', 0)
        short_debt = self.initial_balance.get('short_term_debt', 0)
        minority = self.initial_balance.get('minority_interest', 0)

        prev_nwc = (self.initial_balance['accounts_receivable'] +
                    self.initial_balance['inventory'] -
                    self.initial_balance['accounts_payable'])

        for i in range(len(years)):
            revenue = self.income_statement.revenue[i]
            cogs = self.income_statement.cogs[i]
            ebitda = self.income_statement.ebitda[i]
            dna = self.income_statement.dna[i]
            net_income = self.income_statement.net_income[i]
            mi = self.income_statement.minority_interest[i]
            ni_attr = self.income_statement.net_income_attributable[i]

            wc = self.calculate_working_capital(revenue, cogs)
            projections['accounts_receivable'].append(wc['accounts_receivable'])
            projections['inventory'].append(wc['inventory'])
            projections['accounts_payable'].append(wc['accounts_payable'])

            nwc = wc['net_working_capital']
            nwc_change = nwc - prev_nwc
            prev_nwc = nwc

            capex = revenue * self.assumptions.capex_rate
            ppe = ppe + capex - dna
            projections['ppe'].append(ppe)

            # ===== Cash Sweep 循环求解 =====
            cfo = net_income + dna - nwc_change
            cfi = -capex
            cff = 0

            # 迭代求解（最多3轮）
            for _ in range(3):
                total_debt_iter = long_term_debt + short_debt
                interest = total_debt_iter * self.assumptions.interest_rate
                cash_change = cfo + cfi + cff
                projected_cash = cash + cash_change

                min_cash = revenue * self.assumptions.min_cash_pct_revenue
                if projected_cash < min_cash:
                    shortfall = min_cash - projected_cash
                    short_debt += shortfall
                    cff += shortfall
                else:
                    break

            # 最终利息（使用最终总债务）
            total_debt = long_term_debt + short_debt
            final_interest = total_debt * self.assumptions.interest_rate
            self.income_statement.interest_expense[i] = final_interest

            # 重算下游利润表项目
            ebit = self.income_statement.ebit[i]
            ebt = ebit - final_interest
            self.income_statement.ebt[i] = ebt
            tax = max(0, ebt * self.assumptions.tax_rate)
            self.income_statement.tax[i] = tax
            net_income = ebt - tax
            self.income_statement.net_income[i] = net_income
            self.income_statement.net_margin[i] = net_income / revenue if revenue > 0 else 0
            mi = net_income * 0.10 if net_income > 0 else 0
            self.income_statement.minority_interest[i] = mi
            self.income_statement.net_income_attributable[i] = net_income - mi

            # 重新计算 CFO（因为 net_income 变了）
            cfo = net_income + dna - nwc_change
            cash_change = cfo + cfi + cff
            cash += cash_change
            projections['cash'].append(cash)

            current_assets = cash + wc['accounts_receivable'] + wc['inventory']
            projections['current_assets'].append(current_assets)

            # 重新读取更新后的归母净利润和少数股东损益
            ni_attr = self.income_statement.net_income_attributable[i]
            mi = self.income_statement.minority_interest[i]

            equity += ni_attr
            projections['shareholders_equity'].append(equity)

            minority += mi
            projections['minority_interest_bs'].append(minority)

            projections['short_term_debt'].append(short_debt)
            projections['total_debt'].append(total_debt)

            current_liabilities = wc['accounts_payable'] + short_debt
            projections['current_liabilities'].append(current_liabilities)

            # 总负债 = 长期债务 + 短期债务 + 应付账款（非债务流动负债）
            total_liabilities = long_term_debt + short_debt + wc['accounts_payable']
            projections['total_liabilities'].append(total_liabilities)

            total_assets = current_assets + ppe
            projections['total_assets'].append(total_assets)

            total_l_e = total_liabilities + equity + minority
            projections['total_liabilities_equity'].append(total_l_e)

        self.balance_sheet = BalanceSheet(**projections)
        return self.balance_sheet

    def build_cash_flow(self) -> CashFlowStatement:
        """构建现金流量表"""
        if self.income_statement is None or self.balance_sheet is None:
            self.build_balance_sheet()

        years = list(range(1, self.forecast_years + 1))

        projections = {
            'years': years, 'net_income': [], 'dna_add_back': [], 'wc_changes': [],
            'cfo': [], 'capex': [], 'cfi': [], 'debt_issuance': [],
            'debt_repayment': [], 'dividends': [], 'cff': [],
            'net_change_cash': [], 'beginning_cash': [], 'ending_cash': [],
        }

        beginning_cash = self.initial_balance['cash']

        for i in range(len(years)):
            revenue = self.income_statement.revenue[i]
            net_income = self.income_statement.net_income[i]
            dna = self.income_statement.dna[i]

            if i == 0:
                prev_ar = self.initial_balance['accounts_receivable']
                prev_inv = self.initial_balance['inventory']
                prev_ap = self.initial_balance['accounts_payable']
            else:
                prev_ar = self.balance_sheet.accounts_receivable[i-1]
                prev_inv = self.balance_sheet.inventory[i-1]
                prev_ap = self.balance_sheet.accounts_payable[i-1]

            ar_change = self.balance_sheet.accounts_receivable[i] - prev_ar
            inv_change = self.balance_sheet.inventory[i] - prev_inv
            ap_change = self.balance_sheet.accounts_payable[i] - prev_ap
            wc_change = ar_change + inv_change - ap_change

            projections['net_income'].append(net_income)
            projections['dna_add_back'].append(dna)
            projections['wc_changes'].append(-wc_change)
            cfo = net_income + dna - wc_change
            projections['cfo'].append(cfo)

            capex = revenue * self.assumptions.capex_rate
            projections['capex'].append(capex)
            projections['cfi'].append(-capex)

            # 债务变动
            if i == 0:
                prev_debt = self.initial_balance['total_debt']
            else:
                prev_debt = self.balance_sheet.total_debt[i-1]
            debt_change = self.balance_sheet.total_debt[i] - prev_debt
            projections['debt_issuance'].append(max(0, debt_change))
            projections['debt_repayment'].append(min(0, -debt_change))
            projections['dividends'].append(0)
            projections['cff'].append(debt_change)

            net_change = cfo - capex + debt_change
            projections['net_change_cash'].append(net_change)
            projections['beginning_cash'].append(beginning_cash)
            ending_cash = beginning_cash + net_change
            projections['ending_cash'].append(ending_cash)
            beginning_cash = ending_cash

        self.cash_flow = CashFlowStatement(**projections)
        return self.cash_flow

    def balance_check(self) -> BalanceCheckResult:
        """
        三表配平检查（相对容差 0.1% + 自动诊断）
        """
        if (self.income_statement is None or
            self.balance_sheet is None or
            self.cash_flow is None):
            return BalanceCheckResult()

        checks = []
        warnings = []
        diagnostics = []

        for i in range(self.forecast_years):
            year = i + 1
            year_warnings = []

            # 1. 资产负债配平检查（相对容差）
            total_assets = self.balance_sheet.total_assets[i]
            total_l_e = self.balance_sheet.total_liabilities_equity[i]
            bs_diff = abs(total_assets - total_l_e)
            bs_diff_pct = bs_diff / total_assets if total_assets > 0 else 0
            bs_balanced = bs_diff_pct < 0.001  # 0.1%

            if not bs_balanced:
                year_warnings.append(f"Year {year}: BS 差异 {bs_diff:.4f} ({bs_diff_pct:.4%})")
                # 诊断
                diff = total_assets - total_l_e
                if abs(diff) > 0:
                    diagnostics.append(f"Year {year}: 资产={total_assets:.2f}, L+E={total_l_e:.2f}, diff={diff:.2f}")
                    # 常见原因：NI 与 留存收益勾稽 / NWC 变动
                    if i > 0:
                        prev_equity = self.balance_sheet.shareholders_equity[i-1]
                    else:
                        prev_equity = self.initial_balance['shareholders_equity']
                    curr_equity = self.balance_sheet.shareholders_equity[i]
                    ni = self.income_statement.net_income_attributable[i]
                    expected_equity = prev_equity + ni
                    equity_diff = abs(curr_equity - expected_equity)
                    if equity_diff > 0.01:
                        diagnostics.append(f"  → 留存收益勾稽差: {equity_diff:.2f} (expected={expected_equity:.2f}, actual={curr_equity:.2f})")

            # 2. 现金流量表配平检查
            cf_change = self.cash_flow.cfo[i] + self.cash_flow.cfi[i] + self.cash_flow.cff[i]
            bs_cash_change = self.cash_flow.net_change_cash[i]
            cf_diff = abs(cf_change - bs_cash_change)
            cf_diff_pct = cf_diff / abs(bs_cash_change) if abs(bs_cash_change) > 0 else 0
            cf_reconciled = cf_diff_pct < 0.001 or cf_diff < 0.1

            if not cf_reconciled:
                year_warnings.append(f"Year {year}: CF 差异 {cf_diff:.4f} ({cf_diff_pct:.4%})")

            # 3. 净利润勾稽
            if abs(self.income_statement.net_income[i] - self.cash_flow.net_income[i]) > 0.1:
                year_warnings.append(f"Year {year}: IS/CF 净利润不一致")

            checks.append({
                'year': year,
                'bs_balanced': bs_balanced,
                'bs_difference': bs_diff,
                'bs_difference_pct': bs_diff_pct,
                'cf_reconciled': cf_reconciled,
                'cf_difference': cf_diff,
                'cf_difference_pct': cf_diff_pct,
            })
            warnings.extend(year_warnings)

        result = BalanceCheckResult(
            bs_balanced=all(c['bs_balanced'] for c in checks),
            bs_difference=max(c['bs_difference'] for c in checks),
            bs_difference_pct=max(c['bs_difference_pct'] for c in checks),
            cf_reconciled=all(c['cf_reconciled'] for c in checks),
            cf_difference=max(c['cf_difference'] for c in checks),
            cf_difference_pct=max(c['cf_difference_pct'] for c in checks),
            checks_passed=[c['year'] for c in checks if c['bs_balanced'] and c['cf_reconciled']],
            warnings=warnings,
            diagnostics=diagnostics,
            year_checks=checks,
        )
        self.balance_check_result = result
        return result

    def calculate_credit_metrics(self) -> CreditMetrics:
        """计算信用分析指标（含 Altman Z-Score）"""
        if self.balance_sheet is None or self.income_statement is None:
            self.build_balance_sheet()

        years = list(range(1, self.forecast_years + 1))

        metrics = {
            'years': years,
            'total_debt_ebitda': [], 'net_debt_ebitda': [],
            'interest_coverage': [], 'debt_total_capital': [],
            'current_ratio': [], 'altman_z': [],
            'altman_rating': [], 'sp_rating': [],
            'fixed_charge_coverage': [],
        }

        for i in range(len(years)):
            ebitda = self.income_statement.ebitda[i]
            debt = self.balance_sheet.total_debt[i]
            cash = self.balance_sheet.cash[i]
            equity = self.balance_sheet.shareholders_equity[i]
            interest = self.income_statement.interest_expense[i]
            current_assets = self.balance_sheet.current_assets[i]
            current_liabilities = self.balance_sheet.current_liabilities[i]
            total_assets = self.balance_sheet.total_assets[i]
            total_liabilities = self.balance_sheet.total_liabilities[i]
            revenue = self.income_statement.revenue[i]
            ebit = self.income_statement.ebit[i]
            wc = current_assets - current_liabilities

            # 传统指标
            metrics['total_debt_ebitda'].append(debt / ebitda if ebitda > 0 else None)
            net_debt = max(0, debt - cash)
            metrics['net_debt_ebitda'].append(net_debt / ebitda if ebitda > 0 else None)
            metrics['interest_coverage'].append(ebitda / interest if interest > 0 else None)
            total_capital = debt + equity
            metrics['debt_total_capital'].append(debt / total_capital if total_capital > 0 else None)
            metrics['current_ratio'].append(current_assets / current_liabilities if current_liabilities > 0 else None)

            # ===== Altman Z-Score =====
            # Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5
            # X1 = WC/TA, X2 = RE/TA, X3 = EBIT/TA, X4 = ME/TL, X5 = Sales/TA
            if total_assets > 0 and total_liabilities > 0:
                x1 = wc / total_assets
                # 留存收益近似 = equity - 初始股本（简化用 equity * 0.7）
                retained_earnings = equity * 0.7
                x2 = retained_earnings / total_assets
                x3 = ebit / total_assets
                # Market Value of Equity（用 book value 替代，无市价时）
                me = equity
                x4 = me / total_liabilities if total_liabilities > 0 else 0
                x5 = revenue / total_assets

                z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
                metrics['altman_z'].append(z)

                # Altman 评级
                if z > 2.99:
                    altman_rating = "安全区 (Safe)"
                elif z > 1.81:
                    altman_rating = "灰色区 (Grey)"
                else:
                    altman_rating = " distress区 (Distress)"
                metrics['altman_rating'].append(altman_rating)

                # S&P 风格映射
                if z > 4.0:
                    sp = "AAA/AA"
                elif z > 3.0:
                    sp = "A/BBB"
                elif z > 2.0:
                    sp = "BB/B"
                elif z > 1.5:
                    sp = "CCC"
                else:
                    sp = "CC/C/D"
                metrics['sp_rating'].append(sp)
            else:
                metrics['altman_z'].append(None)
                metrics['altman_rating'].append("N/A")
                metrics['sp_rating'].append("N/A")

            # Fixed Charge Coverage
            fixed_charges = interest + 0  # 简化：加 lease payments 如有
            metrics['fixed_charge_coverage'].append(ebit / fixed_charges if fixed_charges > 0 else None)

        self.credit_metrics = CreditMetrics(**metrics)
        return self.credit_metrics

    def run_scenario(self, scenario: ScenarioType, assumptions: Assumptions = None) -> ScenarioResult:
        """
        运行情景分析
        若 assumptions 为 None，自动从历史推导
        """
        if assumptions is None:
            assumptions = self.derive_assumptions(scenario)

        model = ThreeStatementModel(self.ticker, self.forecast_years, self.data_router)
        model.initial_balance = self.initial_balance.copy()
        model.historical_stats = self.historical_stats
        model.set_assumptions(assumptions)
        model.build_income_statement()
        model.build_balance_sheet()
        model.build_cash_flow()

        return ScenarioResult(
            scenario_name=scenario.value,
            assumptions=assumptions,
            income_statement=model.income_statement,
            balance_sheet=model.balance_sheet,
            cash_flow=model.cash_flow,
            credit_metrics=model.calculate_credit_metrics(),
        )

    def run_all_scenarios(self) -> Dict[ScenarioType, ScenarioResult]:
        """运行所有情景分析"""
        return {
            ScenarioType.BASE: self.run_scenario(ScenarioType.BASE),
            ScenarioType.UPSIDE: self.run_scenario(ScenarioType.UPSIDE),
            ScenarioType.DOWNSIDE: self.run_scenario(ScenarioType.DOWNSIDE),
        }

    def export_excel(self, filepath: str = None) -> bytes:
        """导出Excel模型"""
        try:
            import xlsxwriter
            from io import BytesIO
        except ImportError:
            logger.error("xlsxwriter not installed")
            return b""

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output)

        header_format = workbook.add_format({'bold': True, 'bg_color': self.EXCEL_COLORS['header'], 'border': 1})
        input_format = workbook.add_format({'font_color': self.EXCEL_COLORS['input']})
        formula_format = workbook.add_format({'font_color': self.EXCEL_COLORS['formula']})
        highlight_format = workbook.add_format({'bg_color': self.EXCEL_COLORS['highlight'], 'bold': True})
        warning_format = workbook.add_format({'bg_color': self.EXCEL_COLORS['warning'], 'font_color': 'white'})

        # Sheet 1: Assumptions
        ws_assumptions = workbook.add_worksheet('Assumptions')
        ws_assumptions.write('A1', '驱动假设', header_format)
        if self.assumptions:
            row = 2
            ws_assumptions.write(row, 0, '收入增长', header_format)
            for i, growth in enumerate(self.assumptions.get_growth_rates(), 1):
                row += 1
                ws_assumptions.write(row, 0, f'Y{i} Growth', input_format)
                ws_assumptions.write(row, 1, growth)
            row += 2
            ws_assumptions.write(row, 0, '利润率', header_format)
            row += 1
            ws_assumptions.write(row, 0, 'Gross Margin', input_format)
            ws_assumptions.write(row, 1, self.assumptions.gross_margin)
            row += 1
            ws_assumptions.write(row, 0, 'SG&A Rate', input_format)
            ws_assumptions.write(row, 1, self.assumptions.sga_rate)
            row += 2
            ws_assumptions.write(row, 0, '营运资本天数', header_format)
            for name, val in [('DSO', self.assumptions.dso), ('DIO', self.assumptions.dio), ('DPO', self.assumptions.dpo)]:
                row += 1
                ws_assumptions.write(row, 0, name, input_format)
                ws_assumptions.write(row, 1, val)

        # Sheet 2-4: 三表
        for sheet_name, stmt in [('Income Statement', self.income_statement),
                                  ('Balance Sheet', self.balance_sheet),
                                  ('Cash Flow', self.cash_flow)]:
            if stmt:
                ws = workbook.add_worksheet(sheet_name)
                df = stmt.to_dataframe()
                for col, h in enumerate(df.columns):
                    ws.write(0, col, h, header_format)
                for row_idx, row_data in enumerate(df.values):
                    for col_idx, value in enumerate(row_data):
                        ws.write(row_idx + 1, col_idx, value)

        # Sheet 5: Credit Metrics
        if self.credit_metrics:
            ws_credit = workbook.add_worksheet('Credit Metrics')
            df = self.credit_metrics.to_dataframe()
            for col, h in enumerate(df.columns):
                ws_credit.write(0, col, h, header_format)
            for row_idx, row_data in enumerate(df.values):
                for col_idx, value in enumerate(row_data):
                    ws_credit.write(row_idx + 1, col_idx, value)

        # Sheet 6: Balance Check
        if self.balance_check_result:
            ws_check = workbook.add_worksheet('Balance Check')
            ws_check.write('A1', '配平检查', header_format)
            row = 2
            ws_check.write(row, 0, 'BS 配平')
            ws_check.write(row, 1, '是' if self.balance_check_result.bs_balanced else '否')
            row += 1
            ws_check.write(row, 0, '最大差异 (%)')
            ws_check.write(row, 1, self.balance_check_result.bs_difference_pct)
            row += 1
            ws_check.write(row, 0, 'CF 配平')
            ws_check.write(row, 1, '是' if self.balance_check_result.cf_reconciled else '否')
            row += 1
            ws_check.write(row, 0, '最大差异 (%)')
            ws_check.write(row, 1, self.balance_check_result.cf_difference_pct)
            if self.balance_check_result.diagnostics:
                row += 2
                ws_check.write(row, 0, '诊断', header_format)
                for diag in self.balance_check_result.diagnostics:
                    row += 1
                    ws_check.write(row, 0, diag)

        workbook.close()
        output.seek(0)

        if filepath:
            with open(filepath, 'wb') as f:
                f.write(output.getvalue())

        return output.getvalue()


# Streamlit集成接口
def render_three_statements_page():
    """Streamlit页面渲染函数"""
    try:
        import streamlit as st
    except ImportError:
        return

    st.header("财务三表联动模型（华尔街化 v2.0）")

    ticker = st.text_input("股票代码", "600519.SS")

    scenario = st.selectbox("选择情景", ["Base", "Upside", "Downside"],
                            help="Base=历史均值, Upside=+1σ, Downside=-1σ")

    if st.button("生成模型", type="primary"):
        with st.spinner("构建模型中..."):
            model = ThreeStatementModel(ticker)
            model.load_historical_data()

            scenario_type = ScenarioType.BASE
            if scenario == "Upside":
                scenario_type = ScenarioType.UPSIDE
            elif scenario == "Downside":
                scenario_type = ScenarioType.DOWNSIDE

            result = model.run_scenario(scenario_type)

            # 配平检查
            check_model = ThreeStatementModel(ticker)
            check_model.initial_balance = model.initial_balance.copy()
            check_model.set_assumptions(result.assumptions)
            check_model.build_income_statement()
            check_model.build_balance_sheet()
            check_model.build_cash_flow()
            check = check_model.balance_check()

            if check.bs_balanced and check.cf_reconciled:
                st.success(f"三表配平检查通过 (BS diff: {check.bs_difference_pct:.4%}, CF diff: {check.cf_difference_pct:.4%})")
            else:
                st.error("配平检查未通过")
                if check.warnings:
                    for warning in check.warnings:
                        st.warning(warning)
                if check.diagnostics:
                    with st.expander("诊断"):
                        for d in check.diagnostics:
                            st.write(d)

            # 历史统计
            if model.historical_stats:
                with st.expander("历史统计（用于假设推导）"):
                    st.json(model.historical_stats)

            st.subheader("利润表预测")
            if result.income_statement:
                st.dataframe(result.income_statement.to_dataframe(), width="stretch")

            st.subheader("资产负债表预测")
            if result.balance_sheet:
                st.dataframe(result.balance_sheet.to_dataframe(), width="stretch")

            st.subheader("现金流量表预测")
            if result.cash_flow:
                st.dataframe(result.cash_flow.to_dataframe(), width="stretch")

            st.subheader("信用指标")
            if result.credit_metrics:
                credit_df = result.credit_metrics.to_dataframe()
                st.dataframe(credit_df, width="stretch")
                st.line_chart(credit_df.set_index('Year')[['Total Debt/EBITDA', 'Interest Coverage', 'Altman Z']])

            excel_bytes = check_model.export_excel()
            if excel_bytes:
                st.download_button(
                    "下载Excel模型",
                    data=excel_bytes,
                    file_name=f"{ticker}_3statements.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )


if __name__ == "__main__":
    model = ThreeStatementModel("TEST", forecast_years=5)
    model.set_assumptions(Assumptions())
    model.build_income_statement(base_revenue=1000)
    model.build_balance_sheet()
    model.build_cash_flow()

    check = model.balance_check()
    print(f"BS 配平: {check.bs_balanced} (diff: {check.bs_difference_pct:.4%})")
    print(f"CF 配平: {check.cf_reconciled} (diff: {check.cf_difference_pct:.4%})")

    credit = model.calculate_credit_metrics()
    print(f"Y5 Debt/EBITDA: {credit.total_debt_ebitda[-1]:.2f}")
    print(f"Y5 Altman Z: {credit.altman_z[-1]:.2f} ({credit.altman_rating[-1]})")
