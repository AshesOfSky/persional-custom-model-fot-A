"""
DCF 估值模型插件（华尔街化升级 v2.0）
基于贴现现金流(Discounted Cash Flow)的股权估值模型

升级点（B.1.1）：
1. Beta 三级 fallback + Hamada 杠杆调整
2. EquityValue 修复（EV - 净债务 - 少数股东 - 优先股）
3. 双终值强制并列（Gordon Growth + Exit Multiple）
4. Tornado 敏感性（5 变量 ±2σ 排序）+ 5x5 WACC×g 矩阵
5. 真实财报数据接入（DataRouter）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional, NamedTuple
from dataclasses import dataclass, field
from enum import Enum
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class TerminalValueMethod(Enum):
    """终值计算方法"""
    GORDON_GROWTH = "perpetuity"
    EXIT_MULTIPLE = "exit_multiple"


@dataclass
class WACCResult:
    """WACC计算结果"""
    cost_of_equity: float          # 权益成本 (Re)
    cost_of_debt: float            # 债务成本 (Rd)
    debt_ratio: float              # 债务占比 (D/V)
    equity_ratio: float            # 权益占比 (E/V)
    tax_rate: float                # 税率 (T)
    wacc: float                    # 加权平均资本成本
    beta: float                    # Beta系数（杠杆）
    beta_unlevered: float          # 无杠杆 Beta
    risk_free_rate: float          # 无风险利率
    market_premium: float          # 市场风险溢价
    hamada_applied: bool = False   # 是否应用了 Hamada 调整


@dataclass
class ProjectionTable:
    """预测期数据表"""
    years: List[int]
    revenue: List[float]
    ebitda: List[float]
    ebit: List[float]
    nopat: List[float]
    dna: List[float]
    capex: List[float]
    nwc_change: List[float]
    fcf: List[float]

    def to_dataframe(self) -> pd.DataFrame:
        return pd.DataFrame({
            'Year': self.years,
            'Revenue': self.revenue,
            'EBITDA': self.ebitda,
            'EBIT': self.ebit,
            'NOPAT': self.nopat,
            'D&A': self.dna,
            'CapEx': self.capex,
            'NWC Change': self.nwc_change,
            'FCF': self.fcf,
        })


@dataclass
class TerminalValueResult:
    """双终值计算结果"""
    gordon_growth_tv: float
    exit_multiple_tv: float
    gordon_implied_g: float        # Gordon 法隐含的永续增长率
    exit_multiple: float
    divergence_pct: float          # 两者差距百分比
    divergence_warning: bool       # 差距 >20% 警告
    selected_tv: float             # 默认选择（Gordon）
    selected_method: str


@dataclass
class ValuationResult:
    """估值结果"""
    enterprise_value: float
    equity_value: float            # 修复：不再是 enterprise_value 的别名
    per_share_value: float
    pv_fcf: List[float]
    pv_terminal: float
    implied_multiple: float
    terminal_value_result: Optional[TerminalValueResult] = None
    current_price: float = 0.0
    upside: float = 0.0
    # 估值构成明细
    net_debt: float = 0.0
    minority_interest: float = 0.0
    shares_outstanding: float = 0.0


@dataclass
class SensitivityMatrix:
    """5x5 敏感性矩阵"""
    wacc_values: List[float]
    growth_values: List[float]
    matrix: pd.DataFrame
    base_wacc: float = 0.0
    base_growth: float = 0.0
    base_value: float = 0.0


@dataclass
class TornadoItem:
    """Tornado 图单项"""
    variable: str
    base_value: float
    low_value: float
    high_value: float
    impact_low: float              # 对 per_share_value 的影响（低侧）
    impact_high: float             # 对 per_share_value 的影响（高侧）
    swing: float                   # |impact_high - impact_low|


@dataclass
class TornadoResult:
    """Tornado 敏感性结果"""
    items: List[TornadoItem]
    sorted_by_swing: List[TornadoItem] = field(default_factory=list)


@dataclass
class DCFAssumptions:
    """DCF模型假设参数"""
    revenue_cagr: float = 0.10
    revenue_growth_y1: float = None
    revenue_growth_y2: float = None
    revenue_growth_y3: float = None
    revenue_growth_y4: float = None
    revenue_growth_y5: float = None

    ebitda_margin: float = 0.20
    tax_rate: float = 0.25

    dna_rate: float = 0.03
    capex_rate: float = 0.05
    nwc_rate: float = 0.02

    terminal_growth: float = 0.025
    terminal_method: TerminalValueMethod = TerminalValueMethod.GORDON_GROWTH
    exit_multiple: float = 8.0

    risk_free_rate: Optional[float] = None
    market_premium: float = 0.06
    beta: Optional[float] = None
    cost_of_debt: Optional[float] = None
    wacc_override: Optional[float] = None

    # Hamada 相关
    apply_hamada: bool = True      # 是否应用 Hamada 调整
    target_de_ratio: Optional[float] = None  # 目标 D/E（用于再杠杆）

    def get_growth_rates(self) -> List[float]:
        rates = [
            self.revenue_growth_y1,
            self.revenue_growth_y2,
            self.revenue_growth_y3,
            self.revenue_growth_y4,
            self.revenue_growth_y5,
        ]
        for i, rate in enumerate(rates):
            if rate is None:
                rates[i] = self.revenue_cagr
        return rates


class DCFEngine:
    """
    DCF估值引擎（华尔街化升级）
    """

    EXCEL_COLORS = {
        'input': '0000FF',
        'formula': '000000',
        'link': '008000',
        'header': 'D9E1F2',
        'highlight': 'FFF2CC',
    }

    def __init__(self, ticker: str, forecast_years: int = 5, data_router=None):
        self.ticker = ticker
        self.forecast_years = forecast_years
        self.data_router = data_router
        self.historical_data: Optional[pd.DataFrame] = None
        self.assumptions: Optional[DCFAssumptions] = None
        self.wacc: Optional[WACCResult] = None
        self.projections: Optional[ProjectionTable] = None
        self.valuation: Optional[ValuationResult] = None
        self.sensitivity: Optional[SensitivityMatrix] = None
        self.tornado: Optional[TornadoResult] = None
        self.current_price: float = 0.0

        # 从财报提取的原始数据缓存
        self._latest_income: Optional[pd.Series] = None
        self._latest_balance: Optional[pd.Series] = None
        self._latest_cashflow: Optional[pd.Series] = None
        self._cached_shares: Optional[float] = None
        self._cached_net_debt: Optional[float] = None
        self._cached_base_revenue: Optional[float] = None

    def _ensure_router(self):
        if self.data_router is None:
            from modules.data_router import DataRouter
            self.data_router = DataRouter()

    def load_historical_data(self) -> pd.DataFrame:
        """加载历史财务数据（接入真实财报）"""
        self._ensure_router()
        try:
            stmts = self.data_router.get_financial_statements(self.ticker)
            income = stmts.get("income", pd.DataFrame())
            balance = stmts.get("balance", pd.DataFrame())
            cashflow = stmts.get("cashflow", pd.DataFrame())

            if not income.empty:
                self._latest_income = income.iloc[-1]
            if not balance.empty:
                self._latest_balance = balance.iloc[-1]
            if not cashflow.empty:
                self._latest_cashflow = cashflow.iloc[-1]

            # 合并为三表 DataFrame（DCF 消费方格式）
            merged = pd.concat([income, balance, cashflow], axis=1)
            # 去重列名
            merged = merged.loc[:, ~merged.columns.duplicated()]
            self.historical_data = merged

            if self.historical_data.empty:
                logger.warning(f"No financial data found for {self.ticker}, using sample data")
                self.historical_data = pd.DataFrame({
                    'revenue': [100, 110, 121],
                    'ebitda': [20, 22, 24],
                    'ebit': [17, 19, 21],
                    'net_income': [12.75, 14.25, 15.75],
                })
            return self.historical_data
        except Exception as e:
            logger.error(f"Failed to load historical data: {e}")
            self.historical_data = pd.DataFrame({
                'revenue': [100, 110, 121],
                'ebitda': [20, 22, 24],
                'ebit': [17, 19, 21],
                'net_income': [12.75, 14.25, 15.75],
            })
            return self.historical_data

    def _get_base_revenue_from_data(self) -> float:
        """从历史数据获取最新收入（亿元）"""
        if self._latest_income is not None and "revenue" in self._latest_income.index:
            rev = self._latest_income["revenue"]
            if pd.notna(rev) and rev > 0:
                # A 股报表单位是元，转为亿元
                return rev / 1e8
        if self.historical_data is not None and not self.historical_data.empty and "revenue" in self.historical_data.columns:
            rev = self.historical_data["revenue"].iloc[-1]
            if pd.notna(rev) and rev > 0:
                return rev / 1e8
        return 100.0

    def _get_risk_free_rate(self) -> float:
        """获取无风险利率"""
        try:
            from plugins.data_ingestion.macro_flow import MacroFlowPlugin
            plugin = MacroFlowPlugin()
            rate = plugin.get_risk_free_rate("CN")
            return rate.value / 100 if rate.value > 1 else rate.value
        except Exception:
            return 0.025

    def _get_beta(self) -> float:
        """调用 DataRouter 的 Beta（三级 fallback）"""
        self._ensure_router()
        try:
            return self.data_router.get_beta(self.ticker)
        except Exception as e:
            logger.warning(f"Beta fetch failed: {e}, using 1.0")
            return 1.0

    def _get_capital_structure(self) -> Tuple[float, float]:
        """从 DataRouter 获取资本结构"""
        self._ensure_router()
        try:
            return self.data_router.get_capital_structure(self.ticker)
        except Exception as e:
            logger.warning(f"Capital structure fetch failed: {e}, using default")
            return 0.3, 0.7

    def _get_shares_outstanding(self) -> float:
        """获取总股本（亿股）—— 缓存避免重复网络请求"""
        if self._cached_shares is not None:
            return self._cached_shares
        self._ensure_router()
        try:
            self._cached_shares = self.data_router.get_shares_outstanding(self.ticker)
            return self._cached_shares
        except Exception as e:
            logger.warning(f"Shares outstanding fetch failed: {e}, using 1.0")
            self._cached_shares = 1.0
            return self._cached_shares

    def _get_net_debt(self) -> float:
        """获取净债务（亿元）—— 缓存避免重复网络请求"""
        if self._cached_net_debt is not None:
            return self._cached_net_debt
        self._ensure_router()
        try:
            self._cached_net_debt = self.data_router.get_net_debt(self.ticker)
            return self._cached_net_debt
        except Exception as e:
            logger.warning(f"Net debt fetch failed: {e}, using 0.0")
            self._cached_net_debt = 0.0
            return self._cached_net_debt

    def _get_minority_interest(self) -> float:
        """从资产负债表获取少数股东权益（亿元）"""
        if self._latest_balance is not None:
            mi = self._latest_balance.get("minority_interest_bs", 0)
            if pd.notna(mi) and mi > 0:
                return mi / 1e8
        return 0.0

    def calculate_wacc(self, assumptions: DCFAssumptions) -> WACCResult:
        """
        计算 WACC，含 Hamada 杠杆 Beta 调整
        WACC = (E/V) × Re + (D/V) × Rd × (1 - T)
        Re = Rf + β_L × (Rm - Rf)

        Hamada:
        β_U = β_L / [1 + (1-T) × D/E]
        若目标 D/E 与当前差异 >30%，用目标 D/E 再杠杆：
        β_L_target = β_U × [1 + (1-T) × D/E_target]
        """
        if assumptions.wacc_override:
            return WACCResult(
                cost_of_equity=assumptions.wacc_override,
                cost_of_debt=0.05,
                debt_ratio=0.3,
                equity_ratio=0.7,
                tax_rate=assumptions.tax_rate,
                wacc=assumptions.wacc_override,
                beta=assumptions.beta or 1.0,
                beta_unlevered=(assumptions.beta or 1.0) / (1 + (1 - assumptions.tax_rate) * 0.3 / 0.7),
                risk_free_rate=assumptions.risk_free_rate or 0.03,
                market_premium=assumptions.market_premium,
            )

        risk_free = assumptions.risk_free_rate or self._get_risk_free_rate()
        beta_levered = assumptions.beta or self._get_beta()
        market_premium = assumptions.market_premium
        cost_of_debt = assumptions.cost_of_debt or 0.05
        tax_rate = assumptions.tax_rate

        debt_ratio, equity_ratio = self._get_capital_structure()

        # Hamada 去杠杆
        d_e_current = debt_ratio / equity_ratio if equity_ratio > 0 else 0.43
        beta_unlevered = beta_levered / (1 + (1 - tax_rate) * d_e_current)

        hamada_applied = False
        # 若用户指定了目标 D/E 且与当前差异 >30%，再杠杆
        if assumptions.apply_hamada and assumptions.target_de_ratio is not None:
            de_diff = abs(assumptions.target_de_ratio - d_e_current) / d_e_current if d_e_current > 0 else 0
            if de_diff > 0.30:
                beta_levered = beta_unlevered * (1 + (1 - tax_rate) * assumptions.target_de_ratio)
                hamada_applied = True
                logger.info(f"Hamada applied: β_U={beta_unlevered:.3f} → β_L={beta_levered:.3f} (target D/E={assumptions.target_de_ratio:.2f})")

        cost_of_equity = risk_free + beta_levered * market_premium
        wacc = (equity_ratio * cost_of_equity +
                debt_ratio * cost_of_debt * (1 - tax_rate))

        self.wacc = WACCResult(
            cost_of_equity=cost_of_equity,
            cost_of_debt=cost_of_debt,
            debt_ratio=debt_ratio,
            equity_ratio=equity_ratio,
            tax_rate=tax_rate,
            wacc=wacc,
            beta=beta_levered,
            beta_unlevered=beta_unlevered,
            risk_free_rate=risk_free,
            market_premium=market_premium,
            hamada_applied=hamada_applied,
        )
        return self.wacc

    def build_projections(self, assumptions: DCFAssumptions,
                         base_revenue: float = None) -> ProjectionTable:
        """构建预测期财务数据"""
        if base_revenue is not None:
            self._cached_base_revenue = base_revenue
        if base_revenue is None:
            if self._cached_base_revenue is not None:
                base_revenue = self._cached_base_revenue
            else:
                base_revenue = self._get_base_revenue_from_data()
                self._cached_base_revenue = base_revenue

        growth_rates = assumptions.get_growth_rates()
        years = list(range(1, self.forecast_years + 1))

        projections = {
            'years': years,
            'revenue': [],
            'ebitda': [],
            'ebit': [],
            'nopat': [],
            'dna': [],
            'capex': [],
            'nwc_change': [],
            'fcf': [],
        }

        revenue = base_revenue
        prev_nwc = 0

        for i, year in enumerate(years):
            revenue *= (1 + growth_rates[i])
            projections['revenue'].append(revenue)

            ebitda = revenue * assumptions.ebitda_margin
            projections['ebitda'].append(ebitda)

            dna = revenue * assumptions.dna_rate
            projections['dna'].append(dna)

            ebit = ebitda - dna
            projections['ebit'].append(ebit)

            nopat = ebit * (1 - assumptions.tax_rate)
            projections['nopat'].append(nopat)

            capex = revenue * assumptions.capex_rate
            projections['capex'].append(capex)

            nwc = revenue * assumptions.nwc_rate
            nwc_change = nwc - prev_nwc if i > 0 else nwc
            projections['nwc_change'].append(nwc_change)
            prev_nwc = nwc

            fcf = nopat + dna - capex - nwc_change
            projections['fcf'].append(fcf)

        self.projections = ProjectionTable(**projections)
        return self.projections

    def calculate_terminal_value_dual(self, final_fcf: float, final_ebitda: float,
                                      assumptions: DCFAssumptions) -> TerminalValueResult:
        """
        双终值强制并列：Gordon Growth + Exit Multiple
        同时输出，差距 >20% 时警告
        """
        # 支持 wacc_override（敏感性分析用）
        wacc = assumptions.wacc_override if assumptions.wacc_override else (self.wacc.wacc if self.wacc else 0.08)
        g = assumptions.terminal_growth

        if g >= wacc:
            g = wacc - 0.005

        # Gordon Growth
        fcf_n1 = final_fcf * (1 + g)
        gordon_tv = fcf_n1 / (wacc - g)

        # Exit Multiple
        exit_mult = assumptions.exit_multiple
        exit_tv = final_ebitda * exit_mult

        # 隐含永续增长率一致性校验（从 Exit Multiple 反推）
        implied_g = wacc - (fcf_n1 / exit_tv) if exit_tv > 0 else 0

        divergence = abs(gordon_tv - exit_tv) / ((gordon_tv + exit_tv) / 2) if (gordon_tv + exit_tv) > 0 else 0
        warning = divergence > 0.20

        # 根据 terminal_method 选择
        if assumptions.terminal_method == TerminalValueMethod.EXIT_MULTIPLE:
            selected = exit_tv
            selected_method = "Exit Multiple"
        else:
            selected = gordon_tv
            selected_method = "Gordon Growth"

        return TerminalValueResult(
            gordon_growth_tv=gordon_tv,
            exit_multiple_tv=exit_tv,
            gordon_implied_g=implied_g,
            exit_multiple=exit_mult,
            divergence_pct=divergence,
            divergence_warning=warning,
            selected_tv=selected,
            selected_method=selected_method,
        )

    def calculate_terminal_value(self, final_fcf: float, final_ebitda: float,
                                assumptions: DCFAssumptions) -> float:
        """兼容旧接口：单终值计算"""
        if assumptions.terminal_method == TerminalValueMethod.EXIT_MULTIPLE:
            return final_ebitda * assumptions.exit_multiple

        g = assumptions.terminal_growth
        wacc = assumptions.wacc_override if assumptions.wacc_override else (self.wacc.wacc if self.wacc else 0.08)
        if g >= wacc:
            g = wacc - 0.005
        fcf_n1 = final_fcf * (1 + g)
        return fcf_n1 / (wacc - g)

    def calculate_valuation(self, assumptions: DCFAssumptions) -> ValuationResult:
        """
        计算DCF估值（含 EquityValue 修复和双终值）
        """
        if self.projections is None:
            self.build_projections(assumptions)

        if self.wacc is None:
            self.calculate_wacc(assumptions)

        # 支持 wacc_override（敏感性分析用）
        wacc = assumptions.wacc_override if assumptions.wacc_override else self.wacc.wacc
        fcf_projections = self.projections.fcf

        # 预测期FCF现值
        pv_fcf = []
        for i, fcf in enumerate(fcf_projections, 1):
            pv = fcf / ((1 + wacc) ** i)
            pv_fcf.append(pv)

        # 终值（双终值）
        final_fcf = fcf_projections[-1]
        final_ebitda = self.projections.ebitda[-1]
        tv_result = self.calculate_terminal_value_dual(final_fcf, final_ebitda, assumptions)

        # 使用选定的终值
        terminal_value = tv_result.selected_tv
        n = len(fcf_projections)
        pv_terminal = terminal_value / ((1 + wacc) ** n)

        # 企业价值
        enterprise_value = sum(pv_fcf) + pv_terminal

        # ===== EquityValue 修复 =====
        # EquityValue = EV - 总债务 + 现金 - 少数股东权益 - 优先股
        net_debt = self._get_net_debt()
        minority = self._get_minority_interest()
        # 优先股简化处理（A股较少，从balance sheet拿 if available）
        preferred = 0.0
        if self._latest_balance is not None:
            pref = self._latest_balance.get("preferred_stock", 0)
            if pd.notna(pref) and pref > 0:
                preferred = pref / 1e8

        equity_value = enterprise_value - net_debt - minority - preferred
        if equity_value < 0:
            logger.warning(f"Negative equity value for {self.ticker}: EV={enterprise_value:.2f}, net_debt={net_debt:.2f}")
            equity_value = max(0, equity_value)

        # 每股价值
        shares = self._get_shares_outstanding()
        per_share_value = equity_value / shares if shares > 0 else 0

        implied_multiple = (enterprise_value / final_ebitda
                          if final_ebitda > 0 else 0)

        self.valuation = ValuationResult(
            enterprise_value=enterprise_value,
            equity_value=equity_value,
            per_share_value=per_share_value,
            pv_fcf=pv_fcf,
            pv_terminal=pv_terminal,
            implied_multiple=implied_multiple,
            terminal_value_result=tv_result,
            current_price=self.current_price,
            upside=(per_share_value / self.current_price - 1) if self.current_price > 0 else 0,
            net_debt=net_debt,
            minority_interest=minority,
            shares_outstanding=shares,
        )
        return self.valuation

    def run_sensitivity(self, assumptions: DCFAssumptions,
                       wacc_range: Tuple[float, float] = (-0.02, 0.02),
                       growth_range: Tuple[float, float] = (-0.01, 0.01),
                       steps: int = 5) -> SensitivityMatrix:
        """5x5 WACC × 永续增长率 敏感性矩阵"""
        if self.wacc is None:
            self.calculate_wacc(assumptions)

        base_wacc = self.wacc.wacc
        base_growth = assumptions.terminal_growth

        wacc_values = np.linspace(base_wacc + wacc_range[0], base_wacc + wacc_range[1], steps)
        growth_values = np.linspace(base_growth + growth_range[0], base_growth + growth_range[1], steps)

        original_growth = assumptions.terminal_growth
        original_wacc = assumptions.wacc_override

        pv_fcf_sum = sum(self.valuation.pv_fcf) if self.valuation else 0
        final_fcf = self.projections.fcf[-1] if self.projections else 0
        final_ebitda = self.projections.ebitda[-1] if self.projections else 0

        matrix_data = []
        for w in wacc_values:
            row = []
            for g in growth_values:
                if g >= w:
                    row.append(None)
                else:
                    assumptions.terminal_growth = g
                    assumptions.wacc_override = w
                    tv = self.calculate_terminal_value(final_fcf, final_ebitda, assumptions)
                    pv_tv = tv / ((1 + w) ** self.forecast_years)
                    ev = pv_fcf_sum + pv_tv
                    row.append(ev)
            matrix_data.append(row)

        assumptions.terminal_growth = original_growth
        assumptions.wacc_override = original_wacc

        matrix = pd.DataFrame(
            matrix_data,
            index=[f"{w:.1%}" for w in wacc_values],
            columns=[f"{g:.1%}" for g in growth_values]
        )

        self.sensitivity = SensitivityMatrix(
            wacc_values=wacc_values.tolist(),
            growth_values=growth_values.tolist(),
            matrix=matrix,
            base_wacc=base_wacc,
            base_growth=base_growth,
            base_value=self.valuation.enterprise_value if self.valuation else 0,
        )
        return self.sensitivity

    def run_tornado(self, assumptions: DCFAssumptions,
                    std_multiplier: float = 2.0) -> TornadoResult:
        """
        Tornado 敏感性分析：5 变量 ±2σ 对 per_share_value 的影响排序
        变量：WACC / g / EBITDA margin / Revenue Growth / Exit Multiple
        """
        if self.valuation is None:
            self.calculate_valuation(assumptions)

        base_ps = self.valuation.per_share_value
        if base_ps <= 0:
            return TornadoResult(items=[], sorted_by_swing=[])

        items = []

        # 1. WACC (± 2%)
        wacc_base = self.wacc.wacc if self.wacc else 0.08
        for delta, label in [(-0.02, "low"), (0.02, "high")]:
            assumptions.wacc_override = max(0.01, wacc_base + delta)
            ps = self._quick_recalculate(assumptions)
            if label == "low":
                wacc_low, wacc_high = ps, None
            else:
                wacc_high = ps
        assumptions.wacc_override = None
        items.append(TornadoItem(
            variable="WACC", base_value=wacc_base,
            low_value=wacc_base - 0.02, high_value=wacc_base + 0.02,
            impact_low=wacc_low - base_ps, impact_high=wacc_high - base_ps,
            swing=abs(wacc_high - wacc_low),
        ))

        # 2. Terminal Growth (± 1%)
        g_base = assumptions.terminal_growth
        for delta, label in [(-0.01, "low"), (0.01, "high")]:
            assumptions.terminal_growth = max(0.001, g_base + delta)
            ps = self._quick_recalculate(assumptions)
            if label == "low":
                g_low, g_high = ps, None
            else:
                g_high = ps
        assumptions.terminal_growth = g_base
        items.append(TornadoItem(
            variable="Terminal Growth", base_value=g_base,
            low_value=g_base - 0.01, high_value=g_base + 0.01,
            impact_low=g_low - base_ps, impact_high=g_high - base_ps,
            swing=abs(g_high - g_low),
        ))

        # 3. EBITDA Margin (± 3%)
        margin_base = assumptions.ebitda_margin
        for delta, label in [(-0.03, "low"), (0.03, "high")]:
            assumptions.ebitda_margin = max(0.01, margin_base + delta)
            ps = self._quick_recalculate(assumptions)
            if label == "low":
                m_low, m_high = ps, None
            else:
                m_high = ps
        assumptions.ebitda_margin = margin_base
        items.append(TornadoItem(
            variable="EBITDA Margin", base_value=margin_base,
            low_value=margin_base - 0.03, high_value=margin_base + 0.03,
            impact_low=m_low - base_ps, impact_high=m_high - base_ps,
            swing=abs(m_high - m_low),
        ))

        # 4. Revenue CAGR (± 3%)
        cagr_base = assumptions.revenue_cagr
        for delta, label in [(-0.03, "low"), (0.03, "high")]:
            assumptions.revenue_cagr = max(-0.05, cagr_base + delta)
            ps = self._quick_recalculate(assumptions)
            if label == "low":
                cagr_low, cagr_high = ps, None
            else:
                cagr_high = ps
        assumptions.revenue_cagr = cagr_base
        items.append(TornadoItem(
            variable="Revenue CAGR", base_value=cagr_base,
            low_value=cagr_base - 0.03, high_value=cagr_base + 0.03,
            impact_low=cagr_low - base_ps, impact_high=cagr_high - base_ps,
            swing=abs(cagr_high - cagr_low),
        ))

        # 5. Exit Multiple (± 2x）— 临时切换 terminal_method 为 EXIT_MULTIPLE
        em_base = assumptions.exit_multiple
        original_method = assumptions.terminal_method
        assumptions.terminal_method = TerminalValueMethod.EXIT_MULTIPLE
        for delta, label in [(-2.0, "low"), (2.0, "high")]:
            assumptions.exit_multiple = max(1.0, em_base + delta)
            ps = self._quick_recalculate(assumptions)
            if label == "low":
                em_low, em_high = ps, None
            else:
                em_high = ps
        assumptions.exit_multiple = em_base
        assumptions.terminal_method = original_method
        items.append(TornadoItem(
            variable="Exit Multiple", base_value=em_base,
            low_value=em_base - 2.0, high_value=em_base + 2.0,
            impact_low=em_low - base_ps, impact_high=em_high - base_ps,
            swing=abs(em_high - em_low),
        ))

        sorted_items = sorted(items, key=lambda x: x.swing, reverse=True)
        self.tornado = TornadoResult(items=items, sorted_by_swing=sorted_items)
        return self.tornado

    def _quick_recalculate(self, assumptions: DCFAssumptions) -> float:
        """快速重算（仅终值变化时）返回 per_share_value"""
        # 重建预测（参数变化）
        self.projections = None
        self.valuation = None
        try:
            result = self.calculate_valuation(assumptions)
            return result.per_share_value
        except Exception:
            return 0.0

    def run(self, assumptions: DCFAssumptions,
            base_revenue: float = None,
            current_price: float = 0.0) -> ValuationResult:
        """
        运行完整DCF估值流程
        """
        self.current_price = current_price
        self.assumptions = assumptions

        self.load_historical_data()
        self.calculate_wacc(assumptions)
        self.build_projections(assumptions, base_revenue)
        self.calculate_valuation(assumptions)

        return self.valuation

    def export_excel(self, filepath: str = None) -> bytes:
        """导出Excel模型"""
        try:
            import xlsxwriter
            from io import BytesIO
        except ImportError:
            logger.error("xlsxwriter not installed.")
            return b""

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output)

        header_format = workbook.add_format({'bold': True, 'bg_color': self.EXCEL_COLORS['header'], 'border': 1})
        input_format = workbook.add_format({'font_color': self.EXCEL_COLORS['input']})
        formula_format = workbook.add_format({'font_color': self.EXCEL_COLORS['formula']})
        highlight_format = workbook.add_format({'bg_color': self.EXCEL_COLORS['highlight'], 'bold': True})
        warning_format = workbook.add_format({'bg_color': '#FF6B6B', 'bold': True, 'font_color': 'white'})

        # Sheet 1: Assumptions
        ws_assumptions = workbook.add_worksheet('Assumptions')
        ws_assumptions.write('A1', 'DCF模型假设', header_format)

        row = 2
        ws_assumptions.write(row, 0, 'WACC假设', header_format)
        row += 1
        if self.wacc:
            ws_assumptions.write(row, 0, 'Risk-free Rate', input_format)
            ws_assumptions.write(row, 1, self.wacc.risk_free_rate)
            row += 1
            ws_assumptions.write(row, 0, 'Market Premium', input_format)
            ws_assumptions.write(row, 1, self.wacc.market_premium)
            row += 1
            ws_assumptions.write(row, 0, 'Beta (Levered)', input_format)
            ws_assumptions.write(row, 1, self.wacc.beta)
            row += 1
            ws_assumptions.write(row, 0, 'Beta (Unlevered)', input_format)
            ws_assumptions.write(row, 1, self.wacc.beta_unlevered)
            row += 1
            if self.wacc.hamada_applied:
                ws_assumptions.write(row, 0, 'Hamada Adjusted', input_format)
                ws_assumptions.write(row, 1, 'Yes')
                row += 1
            ws_assumptions.write(row, 0, 'Cost of Equity', formula_format)
            ws_assumptions.write(row, 1, self.wacc.cost_of_equity)
            row += 1
            ws_assumptions.write(row, 0, 'WACC', highlight_format)
            ws_assumptions.write(row, 1, self.wacc.wacc)

        row += 2
        ws_assumptions.write(row, 0, '增长假设', header_format)
        row += 1
        if self.assumptions:
            for i, (year, growth) in enumerate(zip(range(1, self.forecast_years + 1), self.assumptions.get_growth_rates())):
                ws_assumptions.write(row, 0, f'Revenue Growth Y{year}', input_format)
                ws_assumptions.write(row, 1, growth)
                row += 1
            ws_assumptions.write(row, 0, 'Terminal Growth', input_format)
            ws_assumptions.write(row, 1, self.assumptions.terminal_growth)
            row += 1
            ws_assumptions.write(row, 0, 'Exit Multiple', input_format)
            ws_assumptions.write(row, 1, self.assumptions.exit_multiple)

        # Sheet 2: Projections
        ws_proj = workbook.add_worksheet('Projections')
        if self.projections:
            df = self.projections.to_dataframe()
            for col, header in enumerate(df.columns):
                ws_proj.write(0, col, header, header_format)
            for row_idx, row_data in enumerate(df.values):
                for col_idx, value in enumerate(row_data):
                    ws_proj.write(row_idx + 1, col_idx, value)

        # Sheet 3: DCF
        ws_dcf = workbook.add_worksheet('DCF')
        ws_dcf.write('A1', 'DCF估值计算', header_format)

        if self.valuation:
            row = 2
            ws_dcf.write(row, 0, '预测期FCF现值', header_format)
            for i, pv in enumerate(self.valuation.pv_fcf, 1):
                row += 1
                ws_dcf.write(row, 0, f'Year {i}')
                ws_dcf.write(row, 1, pv)

            row += 1
            ws_dcf.write(row, 0, '终值现值', formula_format)
            ws_dcf.write(row, 1, self.valuation.pv_terminal)

            # 双终值展示
            if self.valuation.terminal_value_result:
                tv = self.valuation.terminal_value_result
                row += 2
                ws_dcf.write(row, 0, '双终值校验', header_format)
                row += 1
                ws_dcf.write(row, 0, 'Gordon Growth TV')
                ws_dcf.write(row, 1, tv.gordon_growth_tv)
                row += 1
                ws_dcf.write(row, 0, 'Exit Multiple TV')
                ws_dcf.write(row, 1, tv.exit_multiple_tv)
                row += 1
                ws_dcf.write(row, 0, 'Divergence')
                ws_dcf.write(row, 1, tv.divergence_pct, None, f'{tv.divergence_pct:.1%}')
                if tv.divergence_warning:
                    ws_dcf.write(row, 2, 'WARNING: >20%', warning_format)
                row += 1
                ws_dcf.write(row, 0, 'Selected Method')
                ws_dcf.write(row, 1, tv.selected_method)

            row += 2
            ws_dcf.write(row, 0, '企业价值 (EV)', highlight_format)
            ws_dcf.write(row, 1, self.valuation.enterprise_value)
            row += 1
            ws_dcf.write(row, 0, 'Less: Net Debt')
            ws_dcf.write(row, 1, -self.valuation.net_debt)
            row += 1
            ws_dcf.write(row, 0, 'Less: Minority Interest')
            ws_dcf.write(row, 1, -self.valuation.minority_interest)
            row += 1
            ws_dcf.write(row, 0, '股权价值', highlight_format)
            ws_dcf.write(row, 1, self.valuation.equity_value)
            row += 1
            ws_dcf.write(row, 0, 'Shares Outstanding (亿)')
            ws_dcf.write(row, 1, self.valuation.shares_outstanding)
            row += 1
            ws_dcf.write(row, 0, '每股价值', highlight_format)
            ws_dcf.write(row, 1, self.valuation.per_share_value)

        # Sheet 4: Sensitivity
        if self.sensitivity:
            ws_sens = workbook.add_worksheet('Sensitivity')
            ws_sens.write('A1', '敏感性分析: WACC vs Terminal Growth', header_format)
            matrix = self.sensitivity.matrix
            for col, header in enumerate(matrix.columns):
                ws_sens.write(2, col + 1, header, header_format)
            for row_idx, (idx, row_data) in enumerate(matrix.iterrows()):
                ws_sens.write(row_idx + 3, 0, idx, header_format)
                for col_idx, value in enumerate(row_data):
                    if value is not None:
                        ws_sens.write(row_idx + 3, col_idx + 1, value)

        # Sheet 5: Tornado
        if self.tornado:
            ws_tornado = workbook.add_worksheet('Tornado')
            ws_tornado.write('A1', 'Tornado 敏感性分析', header_format)
            ws_tornado.write(2, 0, 'Variable', header_format)
            ws_tornado.write(2, 1, 'Base', header_format)
            ws_tornado.write(2, 2, 'Low Impact', header_format)
            ws_tornado.write(2, 3, 'High Impact', header_format)
            ws_tornado.write(2, 4, 'Swing', header_format)
            for i, item in enumerate(self.tornado.sorted_by_swing):
                ws_tornado.write(3 + i, 0, item.variable)
                ws_tornado.write(3 + i, 1, item.base_value)
                ws_tornado.write(3 + i, 2, item.impact_low)
                ws_tornado.write(3 + i, 3, item.impact_high)
                ws_tornado.write(3 + i, 4, item.swing)

        # Sheet 6: Summary
        ws_summary = workbook.add_worksheet('Summary')
        ws_summary.write('A1', '估值摘要', header_format)

        if self.valuation:
            row = 2
            ws_summary.write(row, 0, '当前股价')
            ws_summary.write(row, 1, self.current_price)
            row += 1
            ws_summary.write(row, 0, '每股内在价值', highlight_format)
            ws_summary.write(row, 1, self.valuation.per_share_value)
            row += 1
            ws_summary.write(row, 0, '上涨空间')
            ws_summary.write(row, 1, self.valuation.upside, None, f'{self.valuation.upside:.1%}')
            row += 1
            ws_summary.write(row, 0, 'EV/EBITDA 隐含倍数')
            ws_summary.write(row, 1, self.valuation.implied_multiple)

        workbook.close()
        output.seek(0)

        if filepath:
            with open(filepath, 'wb') as f:
                f.write(output.getvalue())

        return output.getvalue()


def quick_dcf_valuation(
    ticker: str,
    revenue_cagr: float = 0.10,
    ebitda_margin: float = 0.20,
    terminal_growth: float = 0.025,
    wacc: float = None,
) -> Dict:
    """快速DCF估值函数"""
    assumptions = DCFAssumptions(
        revenue_cagr=revenue_cagr,
        ebitda_margin=ebitda_margin,
        terminal_growth=terminal_growth,
        wacc_override=wacc,
    )

    engine = DCFEngine(ticker)
    result = engine.run(assumptions)

    return {
        'ticker': ticker,
        'per_share_value': result.per_share_value,
        'enterprise_value': result.enterprise_value,
        'equity_value': result.equity_value,
        'implied_multiple': result.implied_multiple,
        'wacc': engine.wacc.wacc if engine.wacc else None,
        'beta': engine.wacc.beta if engine.wacc else None,
        'terminal_value_divergence': (
            result.terminal_value_result.divergence_pct
            if result.terminal_value_result else None
        ),
    }


# Streamlit集成接口
def render_dcf_page():
    """Streamlit页面渲染函数"""
    try:
        import streamlit as st
    except ImportError:
        return

    st.header("DCF估值模型（华尔街化 v2.0）")

    ticker = st.text_input("股票代码", "600519.SS")

    col1, col2 = st.columns(2)
    with col1:
        revenue_growth = st.slider("收入增长率", 0.0, 0.30, 0.10, 0.01)
        ebitda_margin = st.slider("EBITDA利润率", 0.0, 0.80, 0.20, 0.01)
        dna_rate = st.slider("D&A/收入", 0.0, 0.10, 0.03, 0.005)
    with col2:
        terminal_growth = st.slider("永续增长率", 0.0, 0.05, 0.025, 0.005)
        wacc_override = st.number_input("WACC覆盖值(可选)", 0.0, 0.20, 0.08, 0.005)
        capex_rate = st.slider("CapEx/收入", 0.0, 0.20, 0.05, 0.01)
        exit_multiple = st.number_input("Exit Multiple (EV/EBITDA)", 1.0, 30.0, 8.0, 0.5)

    use_wacc_override = st.checkbox("使用自定义WACC")
    apply_hamada = st.checkbox("应用Hamada杠杆调整", value=True)

    if st.button("运行估值", type="primary"):
        with st.spinner("计算中..."):
            assumptions = DCFAssumptions(
                revenue_cagr=revenue_growth,
                ebitda_margin=ebitda_margin,
                terminal_growth=terminal_growth,
                dna_rate=dna_rate,
                capex_rate=capex_rate,
                exit_multiple=exit_multiple,
                wacc_override=wacc_override if use_wacc_override else None,
                apply_hamada=apply_hamada,
            )

            engine = DCFEngine(ticker)
            result = engine.run(assumptions)

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("每股内在价值", f"¥{result.per_share_value:.2f}")
            with col2:
                st.metric("企业价值", f"¥{result.enterprise_value/1e8:.1f}亿")
            with col3:
                st.metric("股权价值", f"¥{result.equity_value/1e8:.1f}亿")

            if engine.wacc:
                with st.expander("WACC详情"):
                    wacc_col1, wacc_col2 = st.columns(2)
                    with wacc_col1:
                        st.write(f"Risk-free Rate: {engine.wacc.risk_free_rate:.2%}")
                        st.write(f"Market Premium: {engine.wacc.market_premium:.2%}")
                        st.write(f"Beta (Levered): {engine.wacc.beta:.2f}")
                        st.write(f"Beta (Unlevered): {engine.wacc.beta_unlevered:.2f}")
                    with wacc_col2:
                        st.write(f"Cost of Equity: {engine.wacc.cost_of_equity:.2%}")
                        st.write(f"Cost of Debt: {engine.wacc.cost_of_debt:.2%}")
                        st.write(f"WACC: **{engine.wacc.wacc:.2%}**")
                        if engine.wacc.hamada_applied:
                            st.write("Hamada Adjusted: **Yes**")

            # 双终值
            if result.terminal_value_result:
                with st.expander("双终值校验"):
                    tv = result.terminal_value_result
                    st.write(f"Gordon Growth TV: ¥{tv.gordon_growth_tv/1e8:.1f}亿")
                    st.write(f"Exit Multiple TV: ¥{tv.exit_multiple_tv/1e8:.1f}亿")
                    st.write(f"Divergence: {tv.divergence_pct:.1%}")
                    if tv.divergence_warning:
                        st.warning("终值差距 >20%，请关注")

            with st.expander("财务预测"):
                if engine.projections:
                    st.dataframe(engine.projections.to_dataframe(), width="stretch")

            st.subheader("敏感性分析")
            engine.run_sensitivity(assumptions)
            if engine.sensitivity:
                st.dataframe(engine.sensitivity.matrix, width="stretch")

            st.subheader("Tornado 敏感性")
            engine.run_tornado(assumptions)
            if engine.tornado:
                tornado_df = pd.DataFrame([
                    {
                        'Variable': item.variable,
                        'Swing': item.swing,
                        'Low Impact': item.impact_low,
                        'High Impact': item.impact_high,
                    }
                    for item in engine.tornado.sorted_by_swing
                ])
                st.dataframe(tornado_df, width="stretch")

            excel_bytes = engine.export_excel()
            if excel_bytes:
                st.download_button(
                    "下载Excel模型",
                    data=excel_bytes,
                    file_name=f"{ticker}_dcf_model.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )


if __name__ == "__main__":
    assumptions = DCFAssumptions(
        revenue_cagr=0.12,
        ebitda_margin=0.25,
        terminal_growth=0.03,
    )

    engine = DCFEngine("TEST", forecast_years=5)
    result = engine.run(assumptions, base_revenue=1000)

    print(f"每股价值: {result.per_share_value:.2f}")
    print(f"企业价值: {result.enterprise_value:.2f}")
    print(f"股权价值: {result.equity_value:.2f}")
    print(f"WACC: {engine.wacc.wacc:.2%}")
    if result.terminal_value_result:
        print(f"终值差距: {result.terminal_value_result.divergence_pct:.1%}")
