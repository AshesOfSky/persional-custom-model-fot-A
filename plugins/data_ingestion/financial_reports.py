"""
财报与公告抓取插件
对接SEC EDGAR和国内官方信披渠道
支持10-K/10-Q财报、招股说明书、重大公告
"""

import akshare as ak
import pandas as pd
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
import re

# 绕过代理直接访问中国金融数据源（Clash/v2ray 等代理会干扰 eastmoney/cninfo 连接）
import os as _os
_EASTMONEY_HOSTS = (
    "eastmoney.com,push2.eastmoney.com,push2his.eastmoney.com,"
    "datacenter-web.eastmoney.com,quote.eastmoney.com,"
    "emweb.securities.eastmoney.com,emrnweb.eastmoney.com,"
    "fund.eastmoney.com,cninfo.com.cn,sse.com.cn,szse.cn"
)
_existing = _os.environ.get("NO_PROXY", "") or _os.environ.get("no_proxy", "")
_merged = (_existing + "," + _EASTMONEY_HOSTS).strip(",") if _existing else _EASTMONEY_HOSTS
_os.environ["NO_PROXY"] = _merged
_os.environ["no_proxy"] = _merged

logger = logging.getLogger(__name__)


@dataclass
class FinancialReport:
    """财务报告数据结构"""
    ticker: str
    report_type: str  # 10-K, 10-Q, 20-F, annual, quarterly
    fiscal_year: int
    fiscal_period: str
    filing_date: datetime
    report_date: datetime
    revenue: Optional[float] = None
    net_income: Optional[float] = None
    eps: Optional[float] = None
    total_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    shareholders_equity: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    free_cash_flow: Optional[float] = None
    url: str = ""


@dataclass
class CompanyAnnouncement:
    """公司公告数据结构"""
    ticker: str
    title: str
    announcement_type: str
    publish_date: datetime
    content_summary: str = ""
    url: str = ""
    importance: str = "normal"  # normal, important, critical


class FinancialReportsPlugin:
    """
    财报与公告抓取插件
    支持中美市场财报数据获取
    """

    # 报告类型映射
    REPORT_TYPES = {
        "10-K": "年度报告",
        "10-Q": "季度报告",
        "20-F": "外国公司年报",
        "8-K": "重大事件报告",
        "annual": "年报",
        "quarterly": "季报",
    }

    def __init__(self):
        self._cache = {}

    def fetch_us_filing(
        self,
        ticker: str,
        report_type: str = "10-K",
        limit: int = 5
    ) -> List[FinancialReport]:
        """
        获取美股财报 (通过yfinance)

        Args:
            ticker: 股票代码
            report_type: 报告类型 (10-K, 10-Q)
            limit: 获取数量

        Returns:
            List[FinancialReport]: 财报列表
        """
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)

            reports = []

            if report_type in ["10-K", "annual"]:
                # 获取年度财报
                income_stmt = stock.income_stmt
                balance_sheet = stock.balance_sheet
                cash_flow = stock.cashflow

                if income_stmt is not None and not income_stmt.empty:
                    for col in income_stmt.columns[:limit]:
                        date = pd.to_datetime(col)
                        year = date.year

                        # 提取关键指标
                        revenue = income_stmt.loc.get("Total Revenue", pd.Series()).get(col)
                        net_income = income_stmt.loc.get("Net Income", pd.Series()).get(col)
                        eps = income_stmt.loc.get("Basic EPS", pd.Series()).get(col)

                        # 资产负债表
                        total_assets = balance_sheet.loc.get("Total Assets", pd.Series()).get(col) if not balance_sheet.empty else None
                        total_liab = balance_sheet.loc.get("Total Liabilities Net Minority Interest", pd.Series()).get(col) if not balance_sheet.empty else None
                        equity = balance_sheet.loc.get("Stockholders Equity", pd.Series()).get(col) if not balance_sheet.empty else None

                        # 现金流量表
                        op_cf = cash_flow.loc.get("Operating Cash Flow", pd.Series()).get(col) if not cash_flow.empty else None
                        capex = cash_flow.loc.get("Capital Expenditure", pd.Series()).get(col) if not cash_flow.empty else None

                        fcf = None
                        if op_cf is not None and capex is not None:
                            fcf = op_cf + capex  # capex通常是负数

                        report = FinancialReport(
                            ticker=ticker,
                            report_type="10-K",
                            fiscal_year=year,
                            fiscal_period="FY",
                            filing_date=date,
                            report_date=date,
                            revenue=revenue,
                            net_income=net_income,
                            eps=eps,
                            total_assets=total_assets,
                            total_liabilities=total_liab,
                            shareholders_equity=equity,
                            operating_cash_flow=op_cf,
                            free_cash_flow=fcf,
                        )
                        reports.append(report)

            elif report_type in ["10-Q", "quarterly"]:
                # 获取季度财报
                income_stmt = stock.quarterly_income_stmt
                balance_sheet = stock.quarterly_balance_sheet
                cash_flow = stock.quarterly_cashflow

                if income_stmt is not None and not income_stmt.empty:
                    for col in income_stmt.columns[:limit]:
                        date = pd.to_datetime(col)
                        year = date.year
                        quarter = (date.month - 1) // 3 + 1

                        revenue = income_stmt.loc.get("Total Revenue", pd.Series()).get(col)
                        net_income = income_stmt.loc.get("Net Income", pd.Series()).get(col)
                        eps = income_stmt.loc.get("Basic EPS", pd.Series()).get(col)

                        total_assets = balance_sheet.loc.get("Total Assets", pd.Series()).get(col) if not balance_sheet.empty else None
                        total_liab = balance_sheet.loc.get("Total Liabilities Net Minority Interest", pd.Series()).get(col) if not balance_sheet.empty else None
                        equity = balance_sheet.loc.get("Stockholders Equity", pd.Series()).get(col) if not balance_sheet.empty else None

                        op_cf = cash_flow.loc.get("Operating Cash Flow", pd.Series()).get(col) if not cash_flow.empty else None
                        capex = cash_flow.loc.get("Capital Expenditure", pd.Series()).get(col) if not cash_flow.empty else None

                        fcf = None
                        if op_cf is not None and capex is not None:
                            fcf = op_cf + capex

                        report = FinancialReport(
                            ticker=ticker,
                            report_type="10-Q",
                            fiscal_year=year,
                            fiscal_period=f"Q{quarter}",
                            filing_date=date,
                            report_date=date,
                            revenue=revenue,
                            net_income=net_income,
                            eps=eps,
                            total_assets=total_assets,
                            total_liabilities=total_liab,
                            shareholders_equity=equity,
                            operating_cash_flow=op_cf,
                            free_cash_flow=fcf,
                        )
                        reports.append(report)

            return reports

        except Exception as e:
            logger.error(f"Failed to fetch US filings for {ticker}: {e}")
            return []

    def fetch_a_share_financials(
        self,
        ticker: str,
        report_type: str = "annual"
    ) -> List[FinancialReport]:
        """
        获取A股财报

        Args:
            ticker: 股票代码 (如 600519 或 600519.SS)
            report_type: annual/quarterly

        Returns:
            List[FinancialReport]: 财报列表
        """
        code = ticker.replace('.SS', '').replace('.SZ', '')

        try:
            reports = []

            if report_type == "annual":
                # 获取主要财务指标
                df = ak.stock_financial_analysis_indicator(symbol=code)

                if df is not None and not df.empty:
                    for _, row in df.head(5).iterrows():
                        # 解析报告日期
                        report_date = pd.to_datetime(row.get('报告期', row.get('日期', datetime.now())))

                        report = FinancialReport(
                            ticker=ticker,
                            report_type="annual",
                            fiscal_year=report_date.year,
                            fiscal_period="FY",
                            filing_date=report_date,
                            report_date=report_date,
                            revenue=row.get('营业收入'),
                            net_income=row.get('净利润'),
                            eps=row.get('基本每股收益'),
                            total_assets=row.get('总资产'),
                            total_liabilities=row.get('总负债'),
                            shareholders_equity=row.get('股东权益'),
                        )
                        reports.append(report)

            return reports

        except Exception as e:
            logger.error(f"Failed to fetch A-share financials for {ticker}: {e}")
            return []

    def fetch_announcements(
        self,
        ticker: str,
        days: int = 30,
        announcement_type: Optional[str] = None
    ) -> List[CompanyAnnouncement]:
        """
        获取公司公告

        Args:
            ticker: 股票代码
            days: 获取最近多少天的公告
            announcement_type: 公告类型筛选

        Returns:
            List[CompanyAnnouncement]: 公告列表
        """
        code = ticker.replace('.SS', '').replace('.SZ', '')

        try:
            # 使用akshare获取公告
            df = ak.stock_notice_report(symbol=code)

            if df is None or df.empty:
                return []

            announcements = []

            for _, row in df.head(20).iterrows():
                try:
                    publish_date = pd.to_datetime(row.get('公告时间', datetime.now()))

                    # 检查是否在时间范围内
                    if (datetime.now() - publish_date).days > days:
                        continue

                    ann_type = row.get('公告类型', '')

                    # 类型筛选
                    if announcement_type and announcement_type not in ann_type:
                        continue

                    # 重要性判断
                    importance = "normal"
                    critical_keywords = ['重大', '重组', '并购', '退市', '处罚', '诉讼']
                    important_keywords = ['增持', '减持', '分红', '业绩预告']

                    title = row.get('公告标题', '')
                    if any(kw in title for kw in critical_keywords):
                        importance = "critical"
                    elif any(kw in title for kw in important_keywords):
                        importance = "important"

                    announcement = CompanyAnnouncement(
                        ticker=ticker,
                        title=title,
                        announcement_type=ann_type,
                        publish_date=publish_date,
                        content_summary=row.get('公告内容', '')[:200] + "..." if len(row.get('公告内容', '')) > 200 else row.get('公告内容', ''),
                        importance=importance,
                    )
                    announcements.append(announcement)

                except Exception as e:
                    logger.warning(f"Error parsing announcement: {e}")
                    continue

            return announcements

        except Exception as e:
            logger.error(f"Failed to fetch announcements for {ticker}: {e}")
            return []

    def analyze_financial_health(self, reports: List[FinancialReport]) -> Dict:
        """
        分析财务健康状况

        Returns:
            Dict: 健康度评分和关键指标
        """
        if not reports:
            return {"score": 50, "status": "数据不足"}

        latest = reports[0]

        metrics = {
            "score": 50,
            "status": "中性",
            "indicators": {},
        }

        # 盈利能力
        if latest.net_income and latest.revenue and latest.revenue > 0:
            net_margin = latest.net_income / latest.revenue
            metrics["indicators"]["net_margin"] = net_margin
            if net_margin > 0.2:
                metrics["score"] += 10
            elif net_margin < 0.05:
                metrics["score"] -= 10

        # 资产负债率
        if latest.total_assets and latest.total_liabilities and latest.total_assets > 0:
            debt_ratio = latest.total_liabilities / latest.total_assets
            metrics["indicators"]["debt_ratio"] = debt_ratio
            if debt_ratio < 0.4:
                metrics["score"] += 10
            elif debt_ratio > 0.7:
                metrics["score"] -= 15

        # ROE
        if latest.net_income and latest.shareholders_equity and latest.shareholders_equity > 0:
            roe = latest.net_income / latest.shareholders_equity
            metrics["indicators"]["roe"] = roe
            if roe > 0.15:
                metrics["score"] += 10
            elif roe < 0.05:
                metrics["score"] -= 10

        # FCF
        if latest.free_cash_flow and latest.free_cash_flow > 0:
            metrics["score"] += 5

        # 评分区间
        if metrics["score"] >= 70:
            metrics["status"] = "健康"
        elif metrics["score"] >= 50:
            metrics["status"] = "一般"
        else:
            metrics["status"] = "需关注"

        return metrics


# 便捷函数
def fetch_financial_report(
    ticker: str,
    report_type: str = "annual"
) -> List[FinancialReport]:
    """便捷函数：获取财务报告"""
    plugin = FinancialReportsPlugin()

    if ticker.isdigit() or '.SS' in ticker or '.SZ' in ticker:
        return plugin.fetch_a_share_financials(ticker, report_type)
    else:
        us_type = "10-K" if report_type == "annual" else "10-Q"
        return plugin.fetch_us_filing(ticker, us_type)


# ============================================================================
# 完整三表 DataFrame 接口 (B.0 华尔街化升级)
# 所有市场统一返回 {income, balance, cashflow} DataFrame, 标准化英文列名, 索引为报告期升序
# ============================================================================

# 统一财务科目 schema (DCF/Comps/3-Statement 消费方依赖)
STATEMENT_SCHEMA = {
    "income": [
        "revenue", "cost_of_revenue", "gross_profit",
        "sga_expenses", "rd_expenses", "operating_expenses",
        "operating_income", "ebit", "depreciation_amortization", "ebitda",
        "interest_expense", "pretax_income", "income_tax",
        "net_income", "minority_interest", "net_income_attributable",
        "eps_basic", "eps_diluted",
    ],
    "balance": [
        "cash", "accounts_receivable", "inventory", "current_assets",
        "ppe", "intangible_assets", "goodwill", "total_assets",
        "accounts_payable", "short_term_debt", "current_liabilities",
        "long_term_debt", "total_debt", "total_liabilities",
        "shareholders_equity", "minority_interest_bs", "retained_earnings",
        "shares_outstanding",
    ],
    "cashflow": [
        "operating_cash_flow", "capex", "investing_cash_flow",
        "debt_issuance", "debt_repayment", "dividends_paid",
        "financing_cash_flow", "net_change_in_cash", "free_cash_flow",
    ],
}

# akshare A 股字段映射 → 标准英文
# stock_*_sheet_by_report_em 自 2024 年起返回英文大写下划线字段名（实测 2026-04，eastmoney 接口）
AK_INCOME_MAP = {
    "revenue": ["TOTAL_OPERATE_INCOME", "OPERATE_INCOME"],
    "cost_of_revenue": ["OPERATE_COST", "TOTAL_OPERATE_COST"],
    "sga_expenses": ["SALE_EXPENSE", "MANAGE_EXPENSE"],
    "rd_expenses": ["RESEARCH_EXPENSE", "ME_RESEARCH_EXPENSE"],
    "operating_income": ["OPERATE_PROFIT"],
    "interest_expense": ["FE_INTEREST_EXPENSE", "FINANCE_EXPENSE"],
    "pretax_income": ["TOTAL_PROFIT"],
    "income_tax": ["INCOME_TAX"],
    "net_income": ["NETPROFIT"],
    "net_income_attributable": ["PARENT_NETPROFIT"],
    "minority_interest": ["MINORITY_INTEREST"],
    "eps_basic": ["BASIC_EPS"],
    "eps_diluted": ["DILUTED_EPS"],
}

AK_BALANCE_MAP = {
    "cash": ["MONETARYFUNDS"],
    "accounts_receivable": ["ACCOUNTS_RECE"],
    "inventory": ["INVENTORY"],
    "current_assets": ["TOTAL_CURRENT_ASSETS"],
    "ppe": ["FIXED_ASSET"],
    "intangible_assets": ["INTANGIBLE_ASSET"],
    "goodwill": ["GOODWILL"],
    "total_assets": ["TOTAL_ASSETS"],
    "accounts_payable": ["ACCOUNTS_PAYABLE"],
    "short_term_debt": ["SHORT_LOAN", "SHORT_BOND_PAYABLE"],
    "current_liabilities": ["TOTAL_CURRENT_LIAB"],
    "long_term_debt": ["LONG_LOAN", "BOND_PAYABLE"],
    "total_liabilities": ["TOTAL_LIABILITIES"],
    "shareholders_equity": ["TOTAL_EQUITY", "TOTAL_PARENT_EQUITY"],
    "minority_interest_bs": ["MINORITY_EQUITY"],
    "retained_earnings": ["UNASSIGN_RPOFIT"],
    "shares_outstanding": ["SHARE_CAPITAL"],
}

AK_CASHFLOW_MAP = {
    "operating_cash_flow": ["NETCASH_OPERATE"],
    "capex": ["CONSTRUCT_LONG_ASSET"],
    "investing_cash_flow": ["NETCASH_INVEST"],
    "debt_issuance": ["RECEIVE_LOAN_CASH", "ISSUE_BOND"],
    "debt_repayment": ["PAY_DEBT_CASH"],
    "dividends_paid": ["ASSIGN_DIVIDEND_PORFIT"],
    "financing_cash_flow": ["NETCASH_FINANCE"],
    "net_change_in_cash": ["CCE_ADD"],
}

# yfinance 美股字段映射
YF_INCOME_MAP = {
    "revenue": ["Total Revenue"],
    "cost_of_revenue": ["Cost Of Revenue"],
    "gross_profit": ["Gross Profit"],
    "sga_expenses": ["Selling General And Administration", "Selling General And Admin"],
    "rd_expenses": ["Research And Development"],
    "operating_income": ["Operating Income", "Total Operating Income As Reported"],
    "ebit": ["EBIT"],
    "ebitda": ["EBITDA", "Normalized EBITDA"],
    "depreciation_amortization": ["Reconciled Depreciation", "Depreciation And Amortization"],
    "interest_expense": ["Interest Expense"],
    "pretax_income": ["Pretax Income"],
    "income_tax": ["Tax Provision"],
    "net_income": ["Net Income", "Net Income Common Stockholders"],
    "minority_interest": ["Minority Interests"],
    "eps_basic": ["Basic EPS"],
    "eps_diluted": ["Diluted EPS"],
}

YF_BALANCE_MAP = {
    "cash": ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"],
    "accounts_receivable": ["Accounts Receivable", "Receivables"],
    "inventory": ["Inventory"],
    "current_assets": ["Current Assets"],
    "ppe": ["Net PPE", "Gross PPE"],
    "intangible_assets": ["Other Intangible Assets", "Goodwill And Other Intangible Assets"],
    "goodwill": ["Goodwill"],
    "total_assets": ["Total Assets"],
    "accounts_payable": ["Accounts Payable", "Payables"],
    "short_term_debt": ["Current Debt", "Short Term Debt"],
    "current_liabilities": ["Current Liabilities"],
    "long_term_debt": ["Long Term Debt"],
    "total_debt": ["Total Debt"],
    "total_liabilities": ["Total Liabilities Net Minority Interest", "Total Liabilities"],
    "shareholders_equity": ["Stockholders Equity", "Common Stock Equity"],
    "minority_interest_bs": ["Minority Interest"],
    "retained_earnings": ["Retained Earnings"],
    "shares_outstanding": ["Share Issued", "Ordinary Shares Number"],
}

YF_CASHFLOW_MAP = {
    "operating_cash_flow": ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"],
    "capex": ["Capital Expenditure"],
    "investing_cash_flow": ["Investing Cash Flow", "Cash Flow From Continuing Investing Activities"],
    "debt_issuance": ["Issuance Of Debt", "Long Term Debt Issuance"],
    "debt_repayment": ["Repayment Of Debt", "Long Term Debt Payments"],
    "dividends_paid": ["Cash Dividends Paid"],
    "financing_cash_flow": ["Financing Cash Flow", "Cash Flow From Continuing Financing Activities"],
    "net_change_in_cash": ["Changes In Cash", "Change In Cash"],
    "free_cash_flow": ["Free Cash Flow"],
}


def _safe_numeric(val) -> Optional[float]:
    """稳健转换为 float"""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _ak_pivot_and_map(df: pd.DataFrame, field_map: Dict[str, List[str]],
                      schema: List[str], date_col_candidates: List[str]) -> pd.DataFrame:
    """
    将 akshare 的长表（每行一个报告期 × 多列科目）标准化为
    索引=报告期升序、列=标准英文科目名 的 DataFrame
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=schema)

    # 定位报告期列
    date_col = next((c for c in date_col_candidates if c in df.columns), None)
    if date_col is None:
        # 尝试首列
        date_col = df.columns[0]

    df = df.copy()
    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col]).sort_values(date_col)

    out = pd.DataFrame(index=df[date_col].values, columns=schema, dtype=float)
    for std_name, candidates in field_map.items():
        matched = next((c for c in candidates if c in df.columns), None)
        if matched is not None:
            out[std_name] = pd.to_numeric(df[matched].values, errors="coerce")
    return out


def _yf_transpose_and_map(df: pd.DataFrame, field_map: Dict[str, List[str]],
                           schema: List[str]) -> pd.DataFrame:
    """
    yfinance 三表原始格式：行=科目名、列=报告期（最新在左）
    转置为 索引=报告期升序、列=标准英文科目名
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=schema)

    # 转置：行=报告期
    transposed = df.T.copy()
    transposed.index = pd.to_datetime(transposed.index, errors="coerce")
    transposed = transposed.sort_index()  # 升序：最新在尾

    out = pd.DataFrame(index=transposed.index, columns=schema, dtype=float)
    for std_name, candidates in field_map.items():
        matched = next((c for c in candidates if c in transposed.columns), None)
        if matched is not None:
            out[std_name] = pd.to_numeric(transposed[matched], errors="coerce")
    return out


def _derive_computed_fields(income: pd.DataFrame, balance: pd.DataFrame,
                             cashflow: pd.DataFrame) -> None:
    """就地补算派生字段：gross_profit / operating_expenses / ebit / ebitda / total_debt / free_cash_flow"""
    # gross_profit = revenue - cost_of_revenue
    if "gross_profit" in income.columns and income["gross_profit"].isna().all():
        if "revenue" in income and "cost_of_revenue" in income:
            income["gross_profit"] = income["revenue"] - income["cost_of_revenue"]

    # operating_expenses = sga + rd
    if "operating_expenses" in income.columns and income["operating_expenses"].isna().all():
        sga = income.get("sga_expenses", pd.Series(0, index=income.index)).fillna(0)
        rd = income.get("rd_expenses", pd.Series(0, index=income.index)).fillna(0)
        income["operating_expenses"] = sga + rd

    # ebit: 优先用 operating_income
    if "ebit" in income.columns and income["ebit"].isna().all():
        if "operating_income" in income:
            income["ebit"] = income["operating_income"]

    # ebitda = ebit + D&A（如果 CF 有 D&A 优先用 CF，否则 IS）
    if "ebitda" in income.columns and income["ebitda"].isna().all():
        da = income.get("depreciation_amortization")
        if da is None or da.isna().all():
            # 尝试从 CF 拿
            if "depreciation_amortization" in cashflow.columns:
                da = cashflow["depreciation_amortization"].reindex(income.index).fillna(0)
            else:
                da = pd.Series(0.0, index=income.index)
        if "ebit" in income:
            income["ebitda"] = income["ebit"].fillna(0) + da.fillna(0)

    # total_debt = short_term_debt + long_term_debt（若未提供）
    if "total_debt" in balance.columns and balance["total_debt"].isna().all():
        st = balance.get("short_term_debt", pd.Series(0, index=balance.index)).fillna(0)
        lt = balance.get("long_term_debt", pd.Series(0, index=balance.index)).fillna(0)
        balance["total_debt"] = st + lt

    # free_cash_flow = operating_cash_flow + capex（capex 在 yfinance 中为负数，akshare 为正 → 统一取绝对值后相减）
    if "free_cash_flow" in cashflow.columns and cashflow["free_cash_flow"].isna().all():
        ocf = cashflow.get("operating_cash_flow", pd.Series(0, index=cashflow.index)).fillna(0)
        cx = cashflow.get("capex", pd.Series(0, index=cashflow.index)).fillna(0)
        # 统一处理：capex 取绝对值减去
        cashflow["free_cash_flow"] = ocf - cx.abs()


def fetch_a_share_statements(ticker: str, report_type: str = "annual",
                              limit: int = 5) -> Dict[str, pd.DataFrame]:
    """
    获取 A 股完整三表（标准化 DataFrame，列为标准英文科目名，索引为报告期升序）

    Args:
        ticker: A 股代码（如 600519 或 600519.SS 或 300750.SZ）
        report_type: annual（年报）或 quarterly（季报）
        limit: 最近 N 个报告期

    Returns:
        {'income': DataFrame, 'balance': DataFrame, 'cashflow': DataFrame}
    """
    code = ticker.replace(".SS", "").replace(".SZ", "")
    # akshare stock_*_sheet_by_report_em 需要带市场前缀
    if code.startswith("6"):
        symbol_full = f"SH{code}"
    elif code.startswith(("0", "3")):
        symbol_full = f"SZ{code}"
    elif code.startswith(("4", "8")):
        symbol_full = f"BJ{code}"
    else:
        symbol_full = f"SH{code}"

    result = {
        "income": pd.DataFrame(columns=STATEMENT_SCHEMA["income"]),
        "balance": pd.DataFrame(columns=STATEMENT_SCHEMA["balance"]),
        "cashflow": pd.DataFrame(columns=STATEMENT_SCHEMA["cashflow"]),
    }

    date_candidates = ["REPORT_DATE", "NOTICE_DATE", "报告日期", "报告期"]

    try:
        income_raw = ak.stock_profit_sheet_by_report_em(symbol=symbol_full)
        result["income"] = _ak_pivot_and_map(
            income_raw, AK_INCOME_MAP, STATEMENT_SCHEMA["income"], date_candidates
        ).tail(limit * 4)  # 年报 + 季报混合，取更多行后过滤
    except Exception as e:
        logger.warning(f"akshare income statement failed for {ticker}: {e}")

    try:
        balance_raw = ak.stock_balance_sheet_by_report_em(symbol=symbol_full)
        result["balance"] = _ak_pivot_and_map(
            balance_raw, AK_BALANCE_MAP, STATEMENT_SCHEMA["balance"], date_candidates
        ).tail(limit * 4)
    except Exception as e:
        logger.warning(f"akshare balance sheet failed for {ticker}: {e}")

    try:
        cf_raw = ak.stock_cash_flow_sheet_by_report_em(symbol=symbol_full)
        result["cashflow"] = _ak_pivot_and_map(
            cf_raw, AK_CASHFLOW_MAP, STATEMENT_SCHEMA["cashflow"], date_candidates
        ).tail(limit * 4)
    except Exception as e:
        logger.warning(f"akshare cash flow failed for {ticker}: {e}")

    # 若指定年报，过滤 12/31 报告期
    if report_type == "annual":
        for key in ("income", "balance", "cashflow"):
            df = result[key]
            if not df.empty:
                annual_mask = [pd.Timestamp(d).month == 12 for d in df.index]
                result[key] = df[annual_mask].tail(limit)

    _derive_computed_fields(result["income"], result["balance"], result["cashflow"])
    return result


def fetch_us_statements(ticker: str, report_type: str = "annual",
                         limit: int = 5) -> Dict[str, pd.DataFrame]:
    """美股完整三表（yfinance 来源，标准化 schema）"""
    import yfinance as yf

    result = {
        "income": pd.DataFrame(columns=STATEMENT_SCHEMA["income"]),
        "balance": pd.DataFrame(columns=STATEMENT_SCHEMA["balance"]),
        "cashflow": pd.DataFrame(columns=STATEMENT_SCHEMA["cashflow"]),
    }

    try:
        stock = yf.Ticker(ticker)
        if report_type == "annual":
            income_raw = stock.income_stmt
            balance_raw = stock.balance_sheet
            cf_raw = stock.cashflow
        else:
            income_raw = stock.quarterly_income_stmt
            balance_raw = stock.quarterly_balance_sheet
            cf_raw = stock.quarterly_cashflow

        result["income"] = _yf_transpose_and_map(
            income_raw, YF_INCOME_MAP, STATEMENT_SCHEMA["income"]
        ).tail(limit)
        result["balance"] = _yf_transpose_and_map(
            balance_raw, YF_BALANCE_MAP, STATEMENT_SCHEMA["balance"]
        ).tail(limit)
        result["cashflow"] = _yf_transpose_and_map(
            cf_raw, YF_CASHFLOW_MAP, STATEMENT_SCHEMA["cashflow"]
        ).tail(limit)
    except Exception as e:
        logger.error(f"yfinance statements failed for {ticker}: {e}")

    _derive_computed_fields(result["income"], result["balance"], result["cashflow"])
    return result


def fetch_statements(ticker: str, report_type: str = "annual",
                     limit: int = 5) -> Dict[str, pd.DataFrame]:
    """统一入口：根据 ticker 自动路由到 A 股或美股接口"""
    t = ticker.upper().strip()
    is_a_share = (
        t.endswith(".SS") or t.endswith(".SZ") or
        (t.replace(".", "").isdigit() and len(t.replace(".", "")) == 6)
    )
    if is_a_share:
        return fetch_a_share_statements(t, report_type, limit)
    return fetch_us_statements(t, report_type, limit)
