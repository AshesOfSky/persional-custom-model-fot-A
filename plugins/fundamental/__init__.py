"""
基本面分析插件包（华尔街化升级 v2.0）

核心模块:
- dcf_model: DCF估值模型 (Beta/Hamada/双终值/Tornado)
- comps_analysis: 可比公司分析 (申万行业/IQR/4倍数加权/PEG)
- three_statements: 财务三表联动模型 (Cash Sweep/Altman Z/历史Normalize)
- thesis_framework: 投资论点框架 (IC Memo/Skew Ratio/概率加权目标价)
- catalyst_calendar: 催化剂日历 (A股财报/分红/公告/解禁)

使用示例:
    from plugins.fundamental import DCFEngine, DCFAssumptions
    from plugins.fundamental import CompsAnalyzer
    from plugins.fundamental import ThreeStatementModel, Assumptions
    from plugins.fundamental import ThesisBuilder, render_ic_memo
    from plugins.fundamental import CatalystCalendar
"""

from .dcf_model import (
    DCFEngine,
    DCFAssumptions,
    WACCResult,
    ProjectionTable,
    ValuationResult,
    SensitivityMatrix,
    TerminalValueResult,
    TornadoResult,
    TornadoItem,
    TerminalValueMethod,
    quick_dcf_valuation,
    render_dcf_page,
)

from .comps_analysis import (
    CompsAnalyzer,
    TargetCompany,
    ComparableCompany,
    SummaryStats,
    ValuationRange,
    WeightedValuation,
    CompsResult,
    MultiplesCalculator,
    SW_INDUSTRY_MULTIPLE_WEIGHTS,
    quick_comps_analysis,
    render_comps_page,
)

from .three_statements import (
    ThreeStatementModel,
    Assumptions,
    IncomeStatement,
    BalanceSheet,
    CashFlowStatement,
    CreditMetrics,
    BalanceCheckResult,
    ScenarioResult,
    ScenarioType,
    render_three_statements_page,
)

from .thesis_framework import (
    InvestmentThesis,
    Scenario,
    ThesisPillar,
    RiskRewardMatrix,
    KeyDebate,
    VariantPerception,
    PortfolioFit,
    ThesisBuilder,
    compute_probability_weighted_target,
    compute_risk_reward_skew,
    render_ic_memo,
    quick_thesis,
)

from .catalyst_calendar import (
    CatalystCalendar,
    CatalystEvent,
    quick_catalyst_check,
)

__all__ = [
    # DCF模型
    'DCFEngine',
    'DCFAssumptions',
    'WACCResult',
    'ProjectionTable',
    'ValuationResult',
    'SensitivityMatrix',
    'TerminalValueResult',
    'TornadoResult',
    'TornadoItem',
    'TerminalValueMethod',
    'quick_dcf_valuation',
    'render_dcf_page',

    # Comps分析
    'CompsAnalyzer',
    'TargetCompany',
    'ComparableCompany',
    'SummaryStats',
    'ValuationRange',
    'WeightedValuation',
    'CompsResult',
    'MultiplesCalculator',
    'SW_INDUSTRY_MULTIPLE_WEIGHTS',
    'quick_comps_analysis',
    'render_comps_page',

    # 三表模型
    'ThreeStatementModel',
    'Assumptions',
    'IncomeStatement',
    'BalanceSheet',
    'CashFlowStatement',
    'CreditMetrics',
    'BalanceCheckResult',
    'ScenarioResult',
    'ScenarioType',
    'render_three_statements_page',

    # Thesis框架
    'InvestmentThesis',
    'Scenario',
    'ThesisPillar',
    'RiskRewardMatrix',
    'KeyDebate',
    'VariantPerception',
    'PortfolioFit',
    'ThesisBuilder',
    'compute_probability_weighted_target',
    'compute_risk_reward_skew',
    'render_ic_memo',
    'quick_thesis',

    # Catalyst日历
    'CatalystCalendar',
    'CatalystEvent',
    'quick_catalyst_check',
]

__version__ = '2.0.0'
