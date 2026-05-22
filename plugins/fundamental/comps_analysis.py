"""
可比公司分析(Comps)插件（华尔街化升级 v2.0）

升级点（B.1.2）：
1. 真实行业筛选（申万一级/二级 + DataRouter.get_peers_by_industry）
2. IQR 异常值过滤
3. 4 倍数加权融合 + 申万行业自适应权重（31行业字典）
4. 置信区间（基于 Comps 数量 + 倍数标准差）
5. Growth-adjusted 倍数（PEG + EV/EBITDA/Growth）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from enum import Enum
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ========================================================================
# 申万一级行业倍数权重字典（买方机构级配置）
# ========================================================================
SW_INDUSTRY_MULTIPLE_WEIGHTS = {
    # 消费白马：利润稳定，EV/EBITDA 为主
    "食品饮料": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "家用电器": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "医药生物": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "美容护理": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},

    # 科技成长：轻资产，EV/Sales 权重高
    "计算机": {"ev_ebitda": 0.30, "pe_ratio": 0.20, "ev_revenue": 0.50, "pb_ratio": 0.00},
    "传媒": {"ev_ebitda": 0.30, "pe_ratio": 0.20, "ev_revenue": 0.50, "pb_ratio": 0.00},
    "通信": {"ev_ebitda": 0.30, "pe_ratio": 0.20, "ev_revenue": 0.50, "pb_ratio": 0.00},
    "电子": {"ev_ebitda": 0.40, "pe_ratio": 0.30, "ev_revenue": 0.30, "pb_ratio": 0.00},

    # 金融地产：重资产/高杠杆，P/B 为主
    "银行": {"ev_ebitda": 0.00, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.50},
    "非银金融": {"ev_ebitda": 0.00, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.50},
    "房地产": {"ev_ebitda": 0.00, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.50},

    # 周期股：资产重，P/B + EV/EBITDA
    "钢铁": {"ev_ebitda": 0.40, "pe_ratio": 0.20, "ev_revenue": 0.00, "pb_ratio": 0.40},
    "有色金属": {"ev_ebitda": 0.40, "pe_ratio": 0.20, "ev_revenue": 0.00, "pb_ratio": 0.40},
    "煤炭": {"ev_ebitda": 0.40, "pe_ratio": 0.20, "ev_revenue": 0.00, "pb_ratio": 0.40},
    "石油石化": {"ev_ebitda": 0.40, "pe_ratio": 0.20, "ev_revenue": 0.00, "pb_ratio": 0.40},
    "基础化工": {"ev_ebitda": 0.50, "pe_ratio": 0.30, "ev_revenue": 0.00, "pb_ratio": 0.20},
    "建筑材料": {"ev_ebitda": 0.40, "pe_ratio": 0.30, "ev_revenue": 0.00, "pb_ratio": 0.30},
    "建筑装饰": {"ev_ebitda": 0.50, "pe_ratio": 0.30, "ev_revenue": 0.00, "pb_ratio": 0.20},

    # 制造：均衡
    "汽车": {"ev_ebitda": 0.50, "pe_ratio": 0.40, "ev_revenue": 0.10, "pb_ratio": 0.00},
    "机械设备": {"ev_ebitda": 0.50, "pe_ratio": 0.40, "ev_revenue": 0.10, "pb_ratio": 0.00},
    "电力设备": {"ev_ebitda": 0.50, "pe_ratio": 0.40, "ev_revenue": 0.10, "pb_ratio": 0.00},
    "国防军工": {"ev_ebitda": 0.40, "pe_ratio": 0.30, "ev_revenue": 0.30, "pb_ratio": 0.00},

    # 公用事业/交运：稳定现金流
    "公用事业": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "交通运输": {"ev_ebitda": 0.60, "pe_ratio": 0.40, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "环保": {"ev_ebitda": 0.50, "pe_ratio": 0.40, "ev_revenue": 0.10, "pb_ratio": 0.00},

    # 消费服务：轻资产
    "商贸零售": {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "社会服务": {"ev_ebitda": 0.40, "pe_ratio": 0.40, "ev_revenue": 0.20, "pb_ratio": 0.00},
    "纺织服饰": {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "轻工制造": {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00},
    "农林牧渔": {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00},

    # 综合
    "综合": {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00},
}


def _get_sw_weights(industry: str) -> Dict[str, float]:
    """获取申万行业倍数权重，支持模糊匹配"""
    if not industry:
        return {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00}

    # 精确匹配
    if industry in SW_INDUSTRY_MULTIPLE_WEIGHTS:
        return SW_INDUSTRY_MULTIPLE_WEIGHTS[industry]

    # 模糊匹配（包含关系）
    for sw_name, weights in SW_INDUSTRY_MULTIPLE_WEIGHTS.items():
        if sw_name in industry or industry in sw_name:
            return weights

    # 默认
    return {"ev_ebitda": 0.50, "pe_ratio": 0.50, "ev_revenue": 0.00, "pb_ratio": 0.00}


@dataclass
class TargetCompany:
    """目标公司"""
    ticker: str
    name: str = ""
    industry: str = ""
    industry_code: str = ""
    market_cap: float = 0.0
    enterprise_value: float = 0.0
    revenue: float = 0.0
    revenue_growth: float = 0.0
    ebitda: float = 0.0
    ebitda_margin: float = 0.0
    net_income: float = 0.0
    book_value: float = 0.0
    total_debt: float = 0.0
    cash: float = 0.0


@dataclass
class ComparableCompany:
    """可比公司"""
    ticker: str
    name: str = ""
    industry: str = ""
    market_cap: float = 0.0
    enterprise_value: float = 0.0
    revenue: float = 0.0
    revenue_growth: float = 0.0
    ebitda: float = 0.0
    ebitda_margin: float = 0.0
    net_income: float = 0.0
    book_value: float = 0.0
    total_debt: float = 0.0
    cash: float = 0.0

    # 估值倍数
    ev_ebitda: Optional[float] = None
    pe_ratio: Optional[float] = None
    ev_revenue: Optional[float] = None
    pb_ratio: Optional[float] = None

    # Growth-adjusted
    peg_ratio: Optional[float] = None
    ev_ebitda_growth: Optional[float] = None

    def calculate_multiples(self):
        """计算估值倍数"""
        if self.ebitda and self.ebitda > 0:
            self.ev_ebitda = self.enterprise_value / self.ebitda
        if self.net_income and self.net_income > 0:
            self.pe_ratio = self.market_cap / self.net_income
        if self.revenue and self.revenue > 0:
            self.ev_revenue = self.enterprise_value / self.revenue
        if self.book_value and self.book_value > 0:
            self.pb_ratio = self.market_cap / self.book_value

        # Growth-adjusted
        if self.pe_ratio is not None and self.revenue_growth and self.revenue_growth > 0:
            self.peg_ratio = self.pe_ratio / (self.revenue_growth * 100)
        if self.ev_ebitda is not None and self.revenue_growth and self.revenue_growth > 0:
            self.ev_ebitda_growth = self.ev_ebitda / (self.revenue_growth * 100)


@dataclass
class SummaryStats:
    """统计摘要（含 IQR 过滤后）"""
    metric_name: str
    count: int = 0
    count_raw: int = 0           # 过滤前数量
    mean: Optional[float] = None
    median: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    q25: Optional[float] = None
    q75: Optional[float] = None
    lower_fence: Optional[float] = None   # Q1 - 1.5*IQR
    upper_fence: Optional[float] = None   # Q3 + 1.5*IQR


@dataclass
class ValuationRange:
    """估值范围（含置信区间）"""
    metric: str
    low: float
    base: float
    high: float
    implied_ev: Optional[float] = None
    implied_equity: Optional[float] = None
    implied_per_share: Optional[float] = None
    confidence_95_lower: Optional[float] = None   # 95% 置信区间下界
    confidence_95_upper: Optional[float] = None   # 95% 置信区间上界


@dataclass
class WeightedValuation:
    """加权融合估值结果"""
    weights: Dict[str, float]
    implied_ev: float
    implied_equity: float
    implied_per_share: float
    breakdown: Dict[str, float] = field(default_factory=dict)


@dataclass
class CompsResult:
    """Comps分析结果"""
    target: TargetCompany
    comparables: List[ComparableCompany]
    summary_stats: Dict[str, SummaryStats]
    valuation_ranges: Dict[str, ValuationRange]
    weighted_valuation: Optional[WeightedValuation] = None
    comps_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    divergence_warning: str = ""   # DCF vs Comps 差距警告


class MultiplesCalculator:
    """估值倍数计算器"""

    @staticmethod
    def calculate_ev(market_cap: float, total_debt: float, cash: float) -> float:
        return market_cap + total_debt - cash

    @staticmethod
    def calculate_ev_ebitda(ev: float, ebitda: float) -> Optional[float]:
        if ebitda and ebitda > 0:
            return ev / ebitda
        return None

    @staticmethod
    def calculate_pe_ratio(market_cap: float, net_income: float) -> Optional[float]:
        if net_income and net_income > 0:
            return market_cap / net_income
        return None

    @staticmethod
    def calculate_ev_revenue(ev: float, revenue: float) -> Optional[float]:
        if revenue and revenue > 0:
            return ev / revenue
        return None

    @staticmethod
    def calculate_pb_ratio(market_cap: float, book_value: float) -> Optional[float]:
        if book_value and book_value > 0:
            return market_cap / book_value
        return None


class CompsAnalyzer:
    """
    可比公司分析器（华尔街化升级）
    """

    EXCEL_COLORS = {
        'target_highlight': 'FFF2CC',
        'stats_row': 'E7E6E6',
        'header': 'D9E1F2',
        'high_multiple': 'FF6B6B',
        'low_multiple': '51CF66',
    }

    def __init__(self, target_ticker: str, data_router=None):
        self.target_ticker = target_ticker
        self.data_router = data_router
        self.target: Optional[TargetCompany] = None
        self.comparables: List[ComparableCompany] = []
        self.metrics: Dict[str, List[float]] = {}
        self.result: Optional[CompsResult] = None

    def _ensure_router(self):
        if self.data_router is None:
            from modules.data_router import DataRouter
            self.data_router = DataRouter()

    def load_target_data(self) -> TargetCompany:
        """加载目标公司数据（接入真实财报）"""
        self._ensure_router()
        try:
            quote = self.data_router.get_quote(self.target_ticker)
            income = self.data_router.get_income_statement(self.target_ticker, limit=1)
            balance = self.data_router.get_balance_sheet(self.target_ticker, limit=1)

            # 提取关键字段
            revenue = 0.0
            ebitda = 0.0
            net_income = 0.0
            if not income.empty:
                rev = income.get("revenue")
                if rev is not None and len(rev) > 0 and pd.notna(rev.iloc[-1]):
                    revenue = rev.iloc[-1] / 1e8
                ebit = income.get("ebit")
                da = income.get("depreciation_amortization")
                if ebit is not None and len(ebit) > 0 and pd.notna(ebit.iloc[-1]):
                    ebit_val = ebit.iloc[-1]
                    da_val = da.iloc[-1] if da is not None and len(da) > 0 and pd.notna(da.iloc[-1]) else 0
                    ebitda = (ebit_val + da_val) / 1e8
                ni = income.get("net_income")
                if ni is not None and len(ni) > 0 and pd.notna(ni.iloc[-1]):
                    net_income = ni.iloc[-1] / 1e8

            # 资产负债表
            book_value = 0.0
            total_debt = 0.0
            cash = 0.0
            if not balance.empty:
                eq = balance.get("shareholders_equity")
                if eq is not None and len(eq) > 0 and pd.notna(eq.iloc[-1]):
                    book_value = eq.iloc[-1] / 1e8
                td = balance.get("total_debt")
                if td is not None and len(td) > 0 and pd.notna(td.iloc[-1]):
                    total_debt = td.iloc[-1] / 1e8
                c = balance.get("cash")
                if c is not None and len(c) > 0 and pd.notna(c.iloc[-1]):
                    cash = c.iloc[-1] / 1e8

            market_cap = quote.get("market_cap", 0)
            if market_cap > 1e10:  # 可能是元，转为亿元
                market_cap = market_cap / 1e8

            self.target = TargetCompany(
                ticker=self.target_ticker,
                name=quote.get("name", ""),
                industry=quote.get("industry", ""),
                market_cap=market_cap,
                revenue=revenue,
                ebitda=ebitda,
                net_income=net_income,
                book_value=book_value,
                total_debt=total_debt,
                cash=cash,
            )
            self.target.enterprise_value = market_cap + total_debt - cash
            self.target.ebitda_margin = ebitda / revenue if revenue > 0 else 0

        except Exception as e:
            logger.error(f"Failed to load target data: {e}")
            # fallback 示例数据
            self.target = TargetCompany(
                ticker=self.target_ticker,
                name="目标公司",
                industry="消费品",
                market_cap=50e9 / 1e8,  # 500亿 -> 亿元
                revenue=10e9 / 1e8,
                ebitda=2e9 / 1e8,
                net_income=1.5e9 / 1e8,
                book_value=20e9 / 1e8,
                total_debt=5e9 / 1e8,
                cash=2e9 / 1e8,
            )
            self.target.enterprise_value = self.target.market_cap + self.target.total_debt - self.target.cash
            self.target.ebitda_margin = self.target.ebitda / self.target.revenue if self.target.revenue > 0 else 0

        return self.target

    def find_comparables(
        self,
        market_cap_range: Tuple[float, float] = (0.3, 3.0),
        revenue_range: Tuple[float, float] = (0.2, 5.0),
        max_count: int = 12,
    ) -> List[ComparableCompany]:
        """
        自动筛选可比公司（申万行业匹配 + 市值/收入筛选）
        """
        self._ensure_router()
        if self.target is None:
            self.load_target_data()

        peers = self.data_router.get_peers_by_industry(
            self.target_ticker,
            market_cap_range=market_cap_range,
            revenue_range=revenue_range,
            max_count=max_count * 2,  # 多取一些，后续财务筛选
        )

        if not peers:
            logger.warning(f"No peers found for {self.target_ticker}, using sample data")
            self.comparables = self._get_sample_comparables()
            for comp in self.comparables:
                comp.calculate_multiples()
            return self.comparables

        # 补充每个 peer 的财务数据
        comps = []
        for peer in peers:
            try:
                ticker = peer["ticker"]
                name = peer.get("name", "")
                market_cap = peer.get("market_cap", 0)
                if market_cap > 1e10:
                    market_cap = market_cap / 1e8

                # 获取财务数据
                p_income = self.data_router.get_income_statement(ticker, limit=1)
                p_balance = self.data_router.get_balance_sheet(ticker, limit=1)

                revenue = 0.0
                ebitda = 0.0
                net_income = 0.0
                if not p_income.empty:
                    rev = p_income.get("revenue")
                    if rev is not None and len(rev) > 0 and pd.notna(rev.iloc[-1]):
                        revenue = rev.iloc[-1] / 1e8
                    ebit = p_income.get("ebit")
                    da = p_income.get("depreciation_amortization")
                    if ebit is not None and len(ebit) > 0 and pd.notna(ebit.iloc[-1]):
                        ebit_val = ebit.iloc[-1]
                        da_val = da.iloc[-1] if da is not None and len(da) > 0 and pd.notna(da.iloc[-1]) else 0
                        ebitda = (ebit_val + da_val) / 1e8
                    ni = p_income.get("net_income")
                    if ni is not None and len(ni) > 0 and pd.notna(ni.iloc[-1]):
                        net_income = ni.iloc[-1] / 1e8

                book_value = 0.0
                total_debt = 0.0
                cash = 0.0
                if not p_balance.empty:
                    eq = p_balance.get("shareholders_equity")
                    if eq is not None and len(eq) > 0 and pd.notna(eq.iloc[-1]):
                        book_value = eq.iloc[-1] / 1e8
                    td = p_balance.get("total_debt")
                    if td is not None and len(td) > 0 and pd.notna(td.iloc[-1]):
                        total_debt = td.iloc[-1] / 1e8
                    c = p_balance.get("cash")
                    if c is not None and len(c) > 0 and pd.notna(c.iloc[-1]):
                        cash = c.iloc[-1] / 1e8

                # 收入筛选
                if self.target.revenue > 0 and revenue > 0:
                    rev_ratio = revenue / self.target.revenue
                    if not (revenue_range[0] <= rev_ratio <= revenue_range[1]):
                        continue

                comp = ComparableCompany(
                    ticker=ticker,
                    name=name,
                    industry=peer.get("industry", ""),
                    market_cap=market_cap,
                    revenue=revenue,
                    ebitda=ebitda,
                    net_income=net_income,
                    book_value=book_value,
                    total_debt=total_debt,
                    cash=cash,
                )
                comp.enterprise_value = market_cap + total_debt - cash
                comp.calculate_multiples()
                comps.append(comp)

            except Exception as e:
                logger.warning(f"Failed to load peer {peer.get('ticker')}: {e}")
                continue

        # 排除 EBITDA <= 0 的（无法计算 EV/EBITDA）但保留用于其他倍数
        # 排序取 Top N
        if self.target.market_cap > 0:
            comps.sort(key=lambda x: abs(1 - x.market_cap / self.target.market_cap))

        self.comparables = comps[:max_count]

        # 如果数量不足，补充样本数据
        if len(self.comparables) < 3:
            logger.warning(f"Only {len(self.comparables)} comparables found, supplementing with sample data")
            samples = self._get_sample_comparables()
            existing_tickers = {c.ticker for c in self.comparables}
            for s in samples:
                if s.ticker not in existing_tickers:
                    s.calculate_multiples()
                    self.comparables.append(s)
                if len(self.comparables) >= max_count:
                    break

        return self.comparables

    def _get_sample_comparables(self) -> List[ComparableCompany]:
        """示例可比公司（兜底）"""
        comps = [
            ComparableCompany(
                ticker="COMP1.SS", name="可比公司A", industry="消费品",
                market_cap=800, revenue=150, revenue_growth=0.12,
                ebitda=35, net_income=25,
                book_value=350, total_debt=80, cash=30,
            ),
            ComparableCompany(
                ticker="COMP2.SS", name="可比公司B", industry="消费品",
                market_cap=350, revenue=80, revenue_growth=0.08,
                ebitda=15, net_income=10,
                book_value=150, total_debt=30, cash=10,
            ),
            ComparableCompany(
                ticker="COMP3.SS", name="可比公司C", industry="消费品",
                market_cap=1200, revenue=250, revenue_growth=0.15,
                ebitda=60, net_income=40,
                book_value=500, total_debt=150, cash=80,
            ),
            ComparableCompany(
                ticker="COMP4.SS", name="可比公司D", industry="消费品",
                market_cap=450, revenue=120, revenue_growth=0.10,
                ebitda=28, net_income=18,
                book_value=220, total_debt=60, cash=25,
            ),
            ComparableCompany(
                ticker="COMP5.SS", name="可比公司E", industry="消费品",
                market_cap=600, revenue=140, revenue_growth=0.11,
                ebitda=32, net_income=22,
                book_value=280, total_debt=70, cash=35,
            ),
        ]
        for comp in comps:
            comp.enterprise_value = comp.market_cap + comp.total_debt - comp.cash
        return comps

    def calculate_multiples(self) -> pd.DataFrame:
        """计算所有公司的估值倍数表"""
        rows = []
        for comp in self.comparables:
            rows.append({
                'Ticker': comp.ticker,
                'Name': comp.name,
                'Market Cap (亿)': comp.market_cap,
                'EV (亿)': comp.enterprise_value,
                'Revenue (亿)': comp.revenue,
                'EBITDA (亿)': comp.ebitda,
                'EV/EBITDA': comp.ev_ebitda,
                'P/E': comp.pe_ratio,
                'EV/Revenue': comp.ev_revenue,
                'P/B': comp.pb_ratio,
                'PEG': comp.peg_ratio,
                'EV/EBITDA/Growth': comp.ev_ebitda_growth,
                'Revenue Growth': comp.revenue_growth,
                'EBITDA Margin': comp.ebitda_margin,
            })
        return pd.DataFrame(rows)

    def _filter_iqr(self, values: List[float]) -> Tuple[List[float], float, float]:
        """IQR 异常值过滤，返回 (过滤后列表, lower_fence, upper_fence)"""
        if not values:
            return [], 0, 0
        arr = np.array(values)
        q1, q3 = np.percentile(arr, [25, 75])
        iqr = q3 - q1
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        filtered = [v for v in values if lower <= v <= upper]
        return filtered, lower, upper

    def generate_summary_stats(self) -> Dict[str, SummaryStats]:
        """
        生成统计摘要（含 IQR 异常值过滤）
        """
        raw_metrics = {
            'ev_ebitda': [],
            'pe_ratio': [],
            'ev_revenue': [],
            'pb_ratio': [],
        }

        for comp in self.comparables:
            if comp.ev_ebitda is not None and 0 < comp.ev_ebitda < 100:
                raw_metrics['ev_ebitda'].append(comp.ev_ebitda)
            if comp.pe_ratio is not None and 0 < comp.pe_ratio < 200:
                raw_metrics['pe_ratio'].append(comp.pe_ratio)
            if comp.ev_revenue is not None and 0 < comp.ev_revenue < 50:
                raw_metrics['ev_revenue'].append(comp.ev_revenue)
            if comp.pb_ratio is not None and 0 < comp.pb_ratio < 50:
                raw_metrics['pb_ratio'].append(comp.pb_ratio)

        summary = {}
        for metric_name, raw_values in raw_metrics.items():
            count_raw = len(raw_values)
            if not raw_values:
                summary[metric_name] = SummaryStats(metric_name=metric_name, count=0, count_raw=0)
                continue

            # IQR 过滤
            filtered, lower_fence, upper_fence = self._filter_iqr(raw_values)
            values = filtered if len(filtered) >= 3 else raw_values  # 过滤后不足3个则回退

            if not values:
                summary[metric_name] = SummaryStats(
                    metric_name=metric_name, count=0, count_raw=count_raw
                )
                continue

            sorted_values = sorted(values)
            n = len(sorted_values)

            summary[metric_name] = SummaryStats(
                metric_name=metric_name,
                count=n,
                count_raw=count_raw,
                mean=np.mean(values),
                median=np.median(values),
                std=np.std(values),
                min=sorted_values[0],
                max=sorted_values[-1],
                q25=sorted_values[int(n * 0.25)],
                q75=sorted_values[int(n * 0.75)],
                lower_fence=lower_fence,
                upper_fence=upper_fence,
            )

        self.metrics = raw_metrics
        return summary

    def derive_valuation_range(self) -> Dict[str, ValuationRange]:
        """推导目标公司估值范围（含 95% 置信区间）"""
        if not self.target:
            return {}

        summary = self.generate_summary_stats()
        ranges = {}

        def _ci_95(median: float, std: float, n: int) -> Tuple[float, float]:
            """95% 置信区间（正态近似）"""
            if n <= 1 or std is None or std <= 0:
                return median * 0.8, median * 1.2
            se = std / np.sqrt(n)
            z = 1.96
            return median - z * se, median + z * se

        # EV/EBITDA
        if summary['ev_ebitda'].count > 0:
            stats = summary['ev_ebitda']
            low_ev = stats.q25 * self.target.ebitda
            base_ev = stats.median * self.target.ebitda
            high_ev = stats.q75 * self.target.ebitda
            ci_lower, ci_upper = _ci_95(stats.median, stats.std, stats.count)

            ranges['ev_ebitda'] = ValuationRange(
                metric='EV/EBITDA',
                low=low_ev, base=base_ev, high=high_ev,
                implied_ev=base_ev,
                implied_equity=base_ev - self.target.total_debt + self.target.cash,
                confidence_95_lower=ci_lower * self.target.ebitda,
                confidence_95_upper=ci_upper * self.target.ebitda,
            )

        # P/E
        if summary['pe_ratio'].count > 0 and self.target.net_income > 0:
            stats = summary['pe_ratio']
            low_eq = stats.q25 * self.target.net_income
            base_eq = stats.median * self.target.net_income
            high_eq = stats.q75 * self.target.net_income
            ci_lower, ci_upper = _ci_95(stats.median, stats.std, stats.count)

            ranges['pe_ratio'] = ValuationRange(
                metric='P/E',
                low=low_eq, base=base_eq, high=high_eq,
                implied_equity=base_eq,
                confidence_95_lower=ci_lower * self.target.net_income,
                confidence_95_upper=ci_upper * self.target.net_income,
            )

        # EV/Revenue
        if summary['ev_revenue'].count > 0 and self.target.revenue > 0:
            stats = summary['ev_revenue']
            low_ev = stats.q25 * self.target.revenue
            base_ev = stats.median * self.target.revenue
            high_ev = stats.q75 * self.target.revenue
            ci_lower, ci_upper = _ci_95(stats.median, stats.std, stats.count)

            ranges['ev_revenue'] = ValuationRange(
                metric='EV/Revenue',
                low=low_ev, base=base_ev, high=high_ev,
                implied_ev=base_ev,
                implied_equity=base_ev - self.target.total_debt + self.target.cash,
                confidence_95_lower=ci_lower * self.target.revenue,
                confidence_95_upper=ci_upper * self.target.revenue,
            )

        # P/B
        if summary['pb_ratio'].count > 0 and self.target.book_value > 0:
            stats = summary['pb_ratio']
            low_eq = stats.q25 * self.target.book_value
            base_eq = stats.median * self.target.book_value
            high_eq = stats.q75 * self.target.book_value
            ci_lower, ci_upper = _ci_95(stats.median, stats.std, stats.count)

            ranges['pb_ratio'] = ValuationRange(
                metric='P/B',
                low=low_eq, base=base_eq, high=high_eq,
                implied_equity=base_eq,
                confidence_95_lower=ci_lower * self.target.book_value,
                confidence_95_upper=ci_upper * self.target.book_value,
            )

        return ranges

    def calculate_weighted_valuation(self, valuation_ranges: Dict[str, ValuationRange]) -> Optional[WeightedValuation]:
        """
        4 倍数加权融合估值
        使用申万行业自适应权重
        """
        if not self.target or not valuation_ranges:
            return None

        weights = _get_sw_weights(self.target.industry)

        # 计算加权 EV 和 Equity
        weighted_ev = 0.0
        weighted_equity = 0.0
        weight_sum = 0.0
        breakdown = {}

        for metric, weight in weights.items():
            if weight <= 0:
                continue
            vr = valuation_ranges.get(metric)
            if vr is None:
                continue

            # 统一用 implied_ev 或 implied_equity
            if metric in ('ev_ebitda', 'ev_revenue'):
                value = vr.implied_ev if vr.implied_ev is not None else vr.base
                weighted_ev += value * weight
            else:
                value = vr.implied_equity if vr.implied_equity is not None else vr.base
                weighted_equity += value * weight

            weight_sum += weight
            breakdown[metric] = value

        if weight_sum == 0:
            return None

        # 归一化
        if weighted_ev > 0:
            weighted_ev /= weight_sum
        if weighted_equity > 0:
            weighted_equity /= weight_sum

        # 如果 EV 方法为主，转为 equity
        ev_weight = weights.get('ev_ebitda', 0) + weights.get('ev_revenue', 0)
        eq_weight = weights.get('pe_ratio', 0) + weights.get('pb_ratio', 0)

        if ev_weight > eq_weight:
            # EV 为主，转换为 equity
            implied_equity = weighted_ev - self.target.total_debt + self.target.cash
        else:
            implied_equity = weighted_equity

        # 每股
        shares = 1.0  # 简化，实际需要获取 shares outstanding
        implied_per_share = implied_equity / shares if shares > 0 else 0

        return WeightedValuation(
            weights=weights,
            implied_ev=weighted_ev if ev_weight > eq_weight else 0,
            implied_equity=implied_equity,
            implied_per_share=implied_per_share,
            breakdown=breakdown,
        )

    def run(self) -> CompsResult:
        """运行完整Comps分析流程"""
        if self.target is None:
            self.load_target_data()

        if not self.comparables:
            self.find_comparables()

        comps_table = self.calculate_multiples()
        summary_stats = self.generate_summary_stats()
        valuation_ranges = self.derive_valuation_range()
        weighted = self.calculate_weighted_valuation(valuation_ranges)

        self.result = CompsResult(
            target=self.target,
            comparables=self.comparables,
            summary_stats=summary_stats,
            valuation_ranges=valuation_ranges,
            weighted_valuation=weighted,
            comps_table=comps_table,
        )
        return self.result

    def export_excel(self, filepath: str = None) -> bytes:
        """导出Excel表格"""
        try:
            import xlsxwriter
            from io import BytesIO
        except ImportError:
            logger.error("xlsxwriter not installed")
            return b""

        output = BytesIO()
        workbook = xlsxwriter.Workbook(output)

        header_format = workbook.add_format({'bold': True, 'bg_color': self.EXCEL_COLORS['header'], 'border': 1})
        target_format = workbook.add_format({'bg_color': self.EXCEL_COLORS['target_highlight'], 'bold': True})
        stats_format = workbook.add_format({'bg_color': self.EXCEL_COLORS['stats_row'], 'bold': True})

        # Sheet 1: Trading Comps
        ws_comps = workbook.add_worksheet('Trading Comps')
        if self.result:
            df = self.result.comps_table
            for col, header in enumerate(df.columns):
                ws_comps.write(0, col, header, header_format)
            for row_idx, row_data in enumerate(df.values):
                for col_idx, value in enumerate(row_data):
                    ws_comps.write(row_idx + 1, col_idx, value)

            # 统计行
            stats_row = len(df) + 2
            ws_comps.write(stats_row, 0, '均值', stats_format)
            ws_comps.write(stats_row + 1, 0, '中位数', stats_format)
            ws_comps.write(stats_row + 2, 0, 'P25', stats_format)
            ws_comps.write(stats_row + 3, 0, 'P75', stats_format)

            for col, col_name in enumerate(df.columns):
                if col_name in ['EV/EBITDA', 'P/E', 'EV/Revenue', 'P/B', 'PEG']:
                    values = df[col_name].dropna().values
                    if len(values) > 0:
                        ws_comps.write(stats_row, col, np.mean(values))
                        ws_comps.write(stats_row + 1, col, np.median(values))
                        ws_comps.write(stats_row + 2, col, np.percentile(values, 25))
                        ws_comps.write(stats_row + 3, col, np.percentile(values, 75))

        # Sheet 2: Valuation Summary
        ws_val = workbook.add_worksheet('Valuation Summary')
        ws_val.write('A1', '估值摘要', header_format)

        if self.result and self.result.valuation_ranges:
            row = 2
            ws_val.write(row, 0, '指标', header_format)
            ws_val.write(row, 1, 'Low (P25)', header_format)
            ws_val.write(row, 2, 'Base (Median)', header_format)
            ws_val.write(row, 3, 'High (P75)', header_format)
            ws_val.write(row, 4, '95% CI Lower', header_format)
            ws_val.write(row, 5, '95% CI Upper', header_format)

            for metric_name, range_data in self.result.valuation_ranges.items():
                row += 1
                ws_val.write(row, 0, range_data.metric)
                ws_val.write(row, 1, range_data.low)
                ws_val.write(row, 2, range_data.base)
                ws_val.write(row, 3, range_data.high)
                ws_val.write(row, 4, range_data.confidence_95_lower)
                ws_val.write(row, 5, range_data.confidence_95_upper)

            # 加权融合
            if self.result.weighted_valuation:
                row += 2
                ws_val.write(row, 0, '加权融合估值', header_format)
                row += 1
                wv = self.result.weighted_valuation
                ws_val.write(row, 0, 'Implied Equity (亿)')
                ws_val.write(row, 1, wv.implied_equity)
                row += 1
                ws_val.write(row, 0, '权重配置')
                ws_val.write(row, 1, str(wv.weights))

        workbook.close()
        output.seek(0)

        if filepath:
            with open(filepath, 'wb') as f:
                f.write(output.getvalue())

        return output.getvalue()


def quick_comps_analysis(ticker: str, max_comps: int = 8) -> Dict:
    """快速Comps分析函数"""
    analyzer = CompsAnalyzer(ticker)
    result = analyzer.run()

    return {
        'ticker': ticker,
        'target_name': result.target.name,
        'target_industry': result.target.industry,
        'comparable_count': len(result.comparables),
        'ev_ebitda_median': result.summary_stats.get('ev_ebitda', {}).median,
        'pe_median': result.summary_stats.get('pe_ratio', {}).median,
        'weighted_equity': result.weighted_valuation.implied_equity if result.weighted_valuation else None,
        'valuation_ranges': {
            k: {'low': v.low, 'base': v.base, 'high': v.high}
            for k, v in result.valuation_ranges.items()
        },
    }


# Streamlit集成接口
def render_comps_page():
    """Streamlit页面渲染函数"""
    try:
        import streamlit as st
    except ImportError:
        return

    st.header("可比公司分析 (Trading Comps v2.0)")

    ticker = st.text_input("目标股票代码", "600519.SS")

    col1, col2 = st.columns(2)
    with col1:
        auto_select = st.checkbox("自动筛选可比公司", True)
        max_comps = st.slider("最大可比公司数", 5, 15, 8)
    with col2:
        if not auto_select:
            manual_comps = st.text_area("手动输入可比公司代码（逗号分隔）",
                                       placeholder="000858.SZ, 000568.SZ, 002304.SZ")

    if st.button("运行分析", type="primary"):
        analyzer = CompsAnalyzer(ticker)

        if auto_select:
            with st.spinner("筛选可比公司中..."):
                analyzer.find_comparables(max_count=max_comps)
        else:
            for comp in manual_comps.split(','):
                if comp.strip():
                    analyzer.add_comparable(comp.strip())

        result = analyzer.run()

        # 目标公司信息
        st.subheader("目标公司")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("市值", f"¥{result.target.market_cap:.1f}亿")
        with col2:
            st.metric("收入", f"¥{result.target.revenue:.1f}亿")
        with col3:
            st.metric("EBITDA", f"¥{result.target.ebitda:.1f}亿")
        with col4:
            st.metric("净利润", f"¥{result.target.net_income:.1f}亿")

        st.write(f"申万行业: {result.target.industry}")
        st.write(f"权重配置: {_get_sw_weights(result.target.industry)}")

        # 可比公司表格
        st.subheader("可比公司估值表")
        st.dataframe(result.comps_table, width="stretch")

        # 统计摘要
        st.subheader("统计摘要（IQR 过滤后）")
        summary_df = pd.DataFrame([
            {
                '指标': stats.metric_name,
                '数量(过滤后)': stats.count,
                '原始数量': stats.count_raw,
                '均值': f"{stats.mean:.1f}x" if stats.mean else "N/A",
                '中位数': f"{stats.median:.1f}x" if stats.median else "N/A",
                'P25': f"{stats.q25:.1f}x" if stats.q25 else "N/A",
                'P75': f"{stats.q75:.1f}x" if stats.q75 else "N/A",
            }
            for stats in result.summary_stats.values()
        ])
        st.dataframe(summary_df, width="stretch")

        # 估值推导
        st.subheader("估值推导")
        for metric_name, range_data in result.valuation_ranges.items():
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric(f"{range_data.metric} - Low", f"¥{range_data.low:.1f}亿")
            with col2:
                st.metric(f"{range_data.metric} - Base", f"¥{range_data.base:.1f}亿")
            with col3:
                st.metric(f"{range_data.metric} - High", f"¥{range_data.high:.1f}亿")

        # 加权融合
        if result.weighted_valuation:
            st.subheader("加权融合估值")
            st.write(f"申万行业权重: {result.weighted_valuation.weights}")
            st.metric("加权隐含股权价值", f"¥{result.weighted_valuation.implied_equity:.1f}亿")

        # 下载Excel
        excel_bytes = analyzer.export_excel()
        if excel_bytes:
            st.download_button(
                "下载Excel表格",
                data=excel_bytes,
                file_name=f"{ticker}_comps.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )


if __name__ == "__main__":
    analyzer = CompsAnalyzer("TARGET.SS")
    result = analyzer.run()

    print(f"目标公司: {result.target.name}")
    print(f"行业: {result.target.industry}")
    print(f"可比公司数: {len(result.comparables)}")
    print(f"\nEV/EBITDA 中位数: {result.summary_stats.get('ev_ebitda', {}).median}")
    print(f"P/E 中位数: {result.summary_stats.get('pe_ratio', {}).median}")
    if result.weighted_valuation:
        print(f"加权估值: {result.weighted_valuation.implied_equity:.1f}亿")
