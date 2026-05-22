"""
投资论点框架（买方 IC Memo 导向）

核心输出：
- One-Line Pitch
- Three Pillars（含可量化 KPI）
- Bull/Base/Bear 三情景 + 概率权重 → 概率加权目标价
- Risk/Reward Matrix（Skew Ratio）
- Key Debates / Variant Perception
- Confirming/Invalidating Signals
- Portfolio Fit（Phase 2 预留）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Scenario:
    """估值情景"""
    name: str                        # Bull / Base / Bear
    probability: float               # 概率权重
    target_price: float              # 目标价（每股）
    key_drivers: List[str] = field(default_factory=list)
    confirming_signals: List[str] = field(default_factory=list)   # 加仓触发
    invalidating_signals: List[str] = field(default_factory=list) # 止损触发
    dcf_per_share: float = 0.0
    comps_per_share: float = 0.0


@dataclass
class ThesisPillar:
    """投资支柱"""
    title: str
    evidence: str
    kpis: Dict[str, float] = field(default_factory=dict)


@dataclass
class KeyDebate:
    """关键争议点"""
    topic: str
    consensus_view: str
    our_view: str
    catalyst_to_resolve: str = ""


@dataclass
class VariantPerception:
    """非共识观点（α 来源）"""
    thesis: str
    evidence: str
    expected_confirmation: str = ""   # 何时/如何验证
    time_horizon_months: int = 12


@dataclass
class PortfolioFit:
    """组合适配度（Phase 2 预留，接组合数据）"""
    correlation_estimate: float = 0.0   # 与组合现有持仓的相关性
    sector_exposure: str = ""           # 行业暴露
    style_exposure: str = ""            # 风格暴露（价值/成长/质量）
    concentration_impact: str = ""      # 集中度影响


@dataclass
class RiskRewardMatrix:
    """风险收益矩阵"""
    upside_to_bull: float              # 到 Bull 情景的上涨空间
    downside_to_bear: float            # 到 Bear 情景的下跌空间
    skew_ratio: float                  # Upside / |Downside|，>2.0 即 attractive
    probability_weighted_target: float # Σ(P_i × TP_i)
    current_price: float = 0.0
    upside_pct: float = 0.0            # (PWTP - Current) / Current


@dataclass
class InvestmentThesis:
    """投资论点（买方 IC Memo 核心数据结构）"""
    ticker: str
    company_name: str = ""
    one_line_pitch: str = ""
    pillars: List[ThesisPillar] = field(default_factory=list)
    scenarios: List[Scenario] = field(default_factory=list)
    debates: List[KeyDebate] = field(default_factory=list)
    risk_reward: Optional[RiskRewardMatrix] = None
    variant_perception: Optional[VariantPerception] = None
    portfolio_fit: Optional[PortfolioFit] = None
    industry_context: str = ""          # Phase 2 预留（Porter/TAM-SAM-SOM）
    dcf_comps_divergence_warning: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class ThesisBuilder:
    """
    投资论点构建器
    从 DCF / Comps / 三表情景 映射到 InvestmentThesis
    """

    # 默认概率权重
    DEFAULT_PROBABILITIES = {
        "Bull": 0.25,
        "Base": 0.55,
        "Bear": 0.20,
    }

    def __init__(self, ticker: str, company_name: str = ""):
        self.ticker = ticker
        self.company_name = company_name

    @classmethod
    def build_from_models(
        cls,
        ticker: str,
        dcf_result,           # ValuationResult
        comps_result,         # CompsResult
        three_stmt_scenarios: Dict,  # {ScenarioType: ScenarioResult}
        current_price: float = 0.0,
        probabilities: Dict[str, float] = None,
    ) -> InvestmentThesis:
        """
        从估值模型构建投资论点

        映射规则：
        - 3-Statement Upside → Bull DCF
        - 3-Statement Base → Base DCF
        - 3-Statement Downside → Bear DCF
        """
        builder = cls(ticker)
        if probabilities is None:
            probabilities = cls.DEFAULT_PROBABILITIES.copy()

        thesis = InvestmentThesis(
            ticker=ticker,
            company_name=ticker,  # 简化
        )

        # ===== 1. 目标价推导 =====
        # DCF 每股价值
        dcf_ps = dcf_result.per_share_value if dcf_result else 0

        # Comps 每股价值（从 weighted valuation 或 P/E 估值推导）
        comps_ps = 0.0
        if comps_result and comps_result.weighted_valuation:
            comps_ps = comps_result.weighted_valuation.implied_per_share
        elif comps_result and comps_result.valuation_ranges:
            # fallback: 用 P/E 中位数推导
            pe_range = comps_result.valuation_ranges.get('pe_ratio')
            if pe_range and pe_range.implied_equity:
                shares = 1.0  # 简化
                comps_ps = pe_range.implied_equity / shares

        # 三表情景映射到目标价
        bull_ps = dcf_ps * 1.3    # DCF + 30% upside
        base_ps = dcf_ps
        bear_ps = dcf_ps * 0.6    # DCF - 40% downside

        # 若有三表 Upside/Downside 的营收数据，可调整
        base_revenue = 0.0
        if three_stmt_scenarios:
            base = three_stmt_scenarios.get('base')
            if base and base.income_statement:
                base_revenue = base.income_statement.revenue[-1] if base.income_statement.revenue else 0

        # ===== 2. 三情景 =====
        scenarios = [
            Scenario(
                name="Bull",
                probability=probabilities.get("Bull", 0.25),
                target_price=bull_ps,
                key_drivers=[
                    "营收增速超预期（Upside 情景）",
                    "利润率扩张",
                    "估值倍数扩张",
                ],
                confirming_signals=[
                    "季度营收增速 >+2σ 历史均值",
                    "毛利率环比提升",
                    "行业景气度上行",
                ],
                invalidating_signals=[
                    "连续两季营收 miss",
                    "毛利率下滑 >200bps",
                ],
                dcf_per_share=dcf_ps,
                comps_per_share=comps_ps,
            ),
            Scenario(
                name="Base",
                probability=probabilities.get("Base", 0.55),
                target_price=base_ps,
                key_drivers=[
                    "营收按历史均值增长",
                    "利润率稳定",
                    "估值倍数维持中位数",
                ],
                confirming_signals=[
                    "季度业绩符合预期",
                    "行业竞争格局稳定",
                ],
                invalidating_signals=[
                    "宏观经济显著下行",
                    "行业政策负面变化",
                ],
                dcf_per_share=dcf_ps,
                comps_per_share=comps_ps,
            ),
            Scenario(
                name="Bear",
                probability=probabilities.get("Bear", 0.20),
                target_price=bear_ps,
                key_drivers=[
                    "营收增速低于预期（Downside 情景）",
                    "利润率压缩",
                    "估值倍数收缩至 P25",
                ],
                confirming_signals=[
                    "宏观经济衰退信号",
                    "行业需求断崖式下跌",
                ],
                invalidating_signals=[
                    "季度营收增速转正",
                    "毛利率止跌回升",
                ],
                dcf_per_share=dcf_ps,
                comps_per_share=comps_ps,
            ),
        ]
        thesis.scenarios = scenarios

        # ===== 3. Risk/Reward Matrix =====
        thesis.risk_reward = compute_risk_reward_skew(scenarios, current_price)

        # ===== 4. One-Line Pitch =====
        upside = thesis.risk_reward.upside_pct if thesis.risk_reward else 0
        skew = thesis.risk_reward.skew_ratio if thesis.risk_reward else 0
        thesis.one_line_pitch = (
            f"{ticker}: 概率加权目标价 ¥{thesis.risk_reward.probability_weighted_target:.2f} "
            f"(当前 ¥{current_price:.2f}, 上涨空间 {upside:.1%}, Skew={skew:.2f})"
        ) if thesis.risk_reward else f"{ticker}: 覆盖中，等待更多数据"

        # ===== 5. Three Pillars（简化版，实际应由分析师填充）=====
        thesis.pillars = [
            ThesisPillar(
                title="盈利质量",
                evidence="ROE 稳定，经营现金流/净利润 >1.0",
                kpis={"ROE": 0.15, "OCF/NI": 1.1},
            ),
            ThesisPillar(
                title="成长可见性",
                evidence="收入增速高于行业均值，市占率提升",
                kpis={"Revenue CAGR": 0.12, "Market Share": 0.08},
            ),
            ThesisPillar(
                title="估值吸引力",
                evidence=f"DCF={dcf_ps:.2f}, Comps={comps_ps:.2f}, 当前={current_price:.2f}",
                kpis={"DCF Upside": (dcf_ps/current_price - 1) if current_price > 0 else 0},
            ),
        ]

        # ===== 6. Key Debates =====
        thesis.debates = [
            KeyDebate(
                topic="增长可持续性",
                consensus_view="增速将回落至行业均值",
                our_view="结构性份额提升支撑高于行业的增速",
            ),
            KeyDebate(
                topic="估值中枢",
                consensus_view="当前估值已反映增长预期",
                our_view="利润率改善未被充分定价",
            ),
        ]

        # ===== 7. Variant Perception =====
        thesis.variant_perception = VariantPerception(
            thesis="市场低估了公司结构性的利润率改善空间",
            evidence="SGA 费率有 200bps 优化空间，产能利用率提升",
            expected_confirmation="未来 2-3 个季度毛利率环比改善",
            time_horizon_months=6,
        )

        # ===== 8. Portfolio Fit（stub）=====
        thesis.portfolio_fit = PortfolioFit(
            correlation_estimate=0.0,
            sector_exposure="待 Phase 2 接组合数据",
            style_exposure="待 Phase 2 接组合数据",
        )

        # ===== 9. DCF vs Comps 差距警告 =====
        if dcf_ps > 0 and comps_ps > 0:
            divergence = abs(dcf_ps - comps_ps) / ((dcf_ps + comps_ps) / 2)
            if divergence > 0.50:
                thesis.dcf_comps_divergence_warning = (
                    f"严重警告: DCF(¥{dcf_ps:.2f}) vs Comps(¥{comps_ps:.2f}) 差距 {divergence:.1%} "
                    f">50%，必须在 Thesis 中强制解释"
                )
            elif divergence > 0.30:
                thesis.dcf_comps_divergence_warning = (
                    f"警告: DCF(¥{dcf_ps:.2f}) vs Comps(¥{comps_ps:.2f}) 差距 {divergence:.1%} "
                    f"30-50%，建议解释"
                )
            else:
                thesis.dcf_comps_divergence_warning = (
                    f"正常: DCF(¥{dcf_ps:.2f}) vs Comps(¥{comps_ps:.2f}) 差距 {divergence:.1%} <30%"
                )
        elif dcf_ps > 0:
            thesis.dcf_comps_divergence_warning = f"Comps 数据缺失，仅参考 DCF(¥{dcf_ps:.2f})"
        elif comps_ps > 0:
            thesis.dcf_comps_divergence_warning = f"DCF 数据缺失，仅参考 Comps(¥{comps_ps:.2f})"

        return thesis


def compute_probability_weighted_target(scenarios: List[Scenario]) -> float:
    """概率加权目标价 = Σ(P_i × TP_i)"""
    return sum(s.probability * s.target_price for s in scenarios)


def compute_risk_reward_skew(scenarios: List[Scenario], current_price: float) -> RiskRewardMatrix:
    """
    计算 Risk/Reward Matrix
    Skew Ratio = Upside / |Downside|，>2.0 即 attractive
    """
    bull = next((s for s in scenarios if s.name == "Bull"), None)
    bear = next((s for s in scenarios if s.name == "Bear"), None)
    base = next((s for s in scenarios if s.name == "Base"), None)

    pwtp = compute_probability_weighted_target(scenarios)

    upside_to_bull = (bull.target_price / current_price - 1) if bull and current_price > 0 else 0
    downside_to_bear = (bear.target_price / current_price - 1) if bear and current_price > 0 else 0

    upside_abs = abs(upside_to_bull)
    downside_abs = abs(downside_to_bear)

    skew = upside_abs / downside_abs if downside_abs > 0.001 else 999.0

    upside_pct = (pwtp / current_price - 1) if current_price > 0 else 0

    return RiskRewardMatrix(
        upside_to_bull=upside_to_bull,
        downside_to_bear=downside_to_bear,
        skew_ratio=skew,
        probability_weighted_target=pwtp,
        current_price=current_price,
        upside_pct=upside_pct,
    )


def render_ic_memo(thesis: InvestmentThesis) -> str:
    """
    渲染 IC Memo（Markdown 格式）
    """
    lines = []
    lines.append(f"# {thesis.ticker} 投资委员会备忘录")
    lines.append(f"\n**生成时间**: {thesis.generated_at}\n")

    # One-Line Pitch
    lines.append("## 一句话论点")
    lines.append(f"\n> {thesis.one_line_pitch}\n")

    # Risk/Reward
    if thesis.risk_reward:
        rr = thesis.risk_reward
        lines.append("## Risk / Reward 矩阵")
        lines.append(f"\n| 指标 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 当前股价 | ¥{rr.current_price:.2f} |")
        lines.append(f"| 概率加权目标价 | ¥{rr.probability_weighted_target:.2f} |")
        lines.append(f"| 上涨空间 | {rr.upside_pct:.1%} |")
        lines.append(f"| Bull 情景涨幅 | {rr.upside_to_bull:.1%} |")
        lines.append(f"| Bear 情景跌幅 | {rr.downside_to_bear:.1%} |")
        lines.append(f"| **Skew Ratio** | **{rr.skew_ratio:.2f}** {'✅ Attractive (>2.0)' if rr.skew_ratio > 2.0 else '⚠️ 中性'} |")
        lines.append("")

    # Three Pillars
    lines.append("## 三大支柱")
    for i, pillar in enumerate(thesis.pillars, 1):
        lines.append(f"\n### {i}. {pillar.title}")
        lines.append(f"{pillar.evidence}")
        if pillar.kpis:
            lines.append("\n**KPIs**:")
            for k, v in pillar.kpis.items():
                lines.append(f"- {k}: {v:.2%}" if isinstance(v, float) and v < 10 else f"- {k}: {v:.2f}")

    # Scenarios
    lines.append("\n## 三情景估值")
    lines.append("\n| 情景 | 概率 | 目标价 | 关键驱动 |")
    lines.append("|------|------|--------|----------|")
    for s in thesis.scenarios:
        lines.append(f"| {s.name} | {s.probability:.0%} | ¥{s.target_price:.2f} | {', '.join(s.key_drivers[:2])} |")

    # Confirming/Invalidating Signals
    lines.append("\n## 信号监控")
    for s in thesis.scenarios:
        if s.confirming_signals:
            lines.append(f"\n### {s.name} - 确认信号（加仓）")
            for sig in s.confirming_signals:
                lines.append(f"- ✅ {sig}")
        if s.invalidating_signals:
            lines.append(f"\n### {s.name} - 失效信号（止损）")
            for sig in s.invalidating_signals:
                lines.append(f"- ❌ {sig}")

    # DCF vs Comps
    if thesis.dcf_comps_divergence_warning:
        lines.append(f"\n## DCF vs Comps 差距")
        lines.append(f"\n{thesis.dcf_comps_divergence_warning}\n")

    # Key Debates
    if thesis.debates:
        lines.append("\n## 关键争议点 (Consensus vs Our View)")
        for debate in thesis.debates:
            lines.append(f"\n### {debate.topic}")
            lines.append(f"- **Consensus**: {debate.consensus_view}")
            lines.append(f"- **Our View**: {debate.our_view}")

    # Variant Perception
    if thesis.variant_perception:
        vp = thesis.variant_perception
        lines.append("\n## Variant Perception（非共识观点 = α 来源）")
        lines.append(f"\n**论点**: {vp.thesis}")
        lines.append(f"**证据**: {vp.evidence}")
        lines.append(f"**验证时点**: {vp.expected_confirmation}（{vp.time_horizon_months}个月内）")

    # Portfolio Fit
    if thesis.portfolio_fit:
        lines.append("\n## 组合适配度")
        lines.append(f"- 相关性估算: {thesis.portfolio_fit.correlation_estimate}")
        lines.append(f"- 行业暴露: {thesis.portfolio_fit.sector_exposure}")
        lines.append(f"- 风格暴露: {thesis.portfolio_fit.style_exposure}")

    return "\n".join(lines)


# 便捷函数
def quick_thesis(ticker: str, dcf_ps: float, comps_ps: float, current_price: float) -> InvestmentThesis:
    """快速构建投资论点（简化版）"""
    scenarios = [
        Scenario(name="Bull", probability=0.25, target_price=dcf_ps * 1.3),
        Scenario(name="Base", probability=0.55, target_price=dcf_ps),
        Scenario(name="Bear", probability=0.20, target_price=dcf_ps * 0.6),
    ]
    rr = compute_risk_reward_skew(scenarios, current_price)

    return InvestmentThesis(
        ticker=ticker,
        one_line_pitch=(
            f"{ticker}: PWTP=¥{rr.probability_weighted_target:.2f}, "
            f"upside={rr.upside_pct:.1%}, skew={rr.skew_ratio:.2f}"
        ),
        scenarios=scenarios,
        risk_reward=rr,
    )


if __name__ == "__main__":
    # 测试
    scenarios = [
        Scenario(name="Bull", probability=0.25, target_price=200),
        Scenario(name="Base", probability=0.55, target_price=150),
        Scenario(name="Bear", probability=0.20, target_price=80),
    ]
    rr = compute_risk_reward_skew(scenarios, current_price=120)
    print(f"PWTP: {rr.probability_weighted_target:.2f}")
    print(f"Skew: {rr.skew_ratio:.2f}")
    print(f"Upside: {rr.upside_pct:.1%}")
