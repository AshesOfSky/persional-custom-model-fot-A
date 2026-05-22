"""
fundamental.py — 基本面看板
PE / PB / PS / 股息率 / ROE / 净利率 / 营收增速 / 资产负债率
"""

import pandas as pd
import numpy as np


def _safe_get(info: dict, key: str, default=None):
    val = info.get(key, default)
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    return val


def get_valuation(info: dict) -> dict:
    """从 yfinance info 提取估值指标"""
    return {
        "PE":      _safe_get(info, "trailingPE"),
        "PB":      _safe_get(info, "priceToBook"),
        "PS":      _safe_get(info, "priceToSalesTrailing12Months"),
        "dividend_yield": _safe_get(info, "dividendYield"),
        "market_cap":     _safe_get(info, "marketCap"),
        "52w_high":       _safe_get(info, "fiftyTwoWeekHigh"),
        "52w_low":        _safe_get(info, "fiftyTwoWeekLow"),
        "current_price":  _safe_get(info, "currentPrice") or _safe_get(info, "regularMarketPrice"),
        "prev_close":     _safe_get(info, "previousClose"),
        "name":           _safe_get(info, "longName") or _safe_get(info, "shortName", "—"),
        "currency":       _safe_get(info, "currency", ""),
        "sector":         _safe_get(info, "sector", "—"),
        "industry":       _safe_get(info, "industry", "—"),
    }


def get_profitability(info: dict, income_stmt: pd.DataFrame) -> dict:
    """提取盈利能力指标"""
    roe          = _safe_get(info, "returnOnEquity")
    net_margin   = _safe_get(info, "profitMargins")
    gross_margin = _safe_get(info, "grossMargins")
    op_margin    = _safe_get(info, "operatingMargins")

    # 营收增速（最近两期）
    rev_growth = _safe_get(info, "revenueGrowth")
    if rev_growth is None and not income_stmt.empty:
        try:
            revenues = income_stmt.loc["Total Revenue"].dropna()
            if len(revenues) >= 2:
                rev_growth = (revenues.iloc[0] - revenues.iloc[1]) / abs(revenues.iloc[1])
        except Exception:
            pass

    return {
        "ROE":          roe,
        "net_margin":   net_margin,
        "gross_margin": gross_margin,
        "op_margin":    op_margin,
        "rev_growth":   rev_growth,
    }


def get_financial_health(info: dict, balance_sheet: pd.DataFrame) -> dict:
    """资产负债率等财务健康指标"""
    debt_ratio = None
    try:
        if not balance_sheet.empty:
            total_liab  = None
            total_assets = None
            for key in ["Total Liabilities Net Minority Interest", "Total Liabilities"]:
                if key in balance_sheet.index:
                    total_liab = balance_sheet.loc[key].iloc[0]
                    break
            for key in ["Total Assets"]:
                if key in balance_sheet.index:
                    total_assets = balance_sheet.loc[key].iloc[0]
                    break
            if total_liab and total_assets and total_assets != 0:
                debt_ratio = total_liab / total_assets
    except Exception:
        pass

    return {
        "debt_ratio":         debt_ratio,
        "total_cash":         _safe_get(info, "totalCash"),
        "total_debt":         _safe_get(info, "totalDebt"),
        "current_ratio":      _safe_get(info, "currentRatio"),
        "quick_ratio":        _safe_get(info, "quickRatio"),
    }


def calc_pe_percentile(pe_current, info: dict) -> float | None:
    """
    估算 PE 历史百分位（yfinance 不直接提供历史 PE，
    用 5年最低/最高 PE 估算分位，作为近似）
    """
    pe_5y_low  = _safe_get(info, "fiveYearAvgDividendYield")  # placeholder
    # yfinance 无直接 5年 PE 历史，返回 None
    return None


def build_fundamental_summary(info: dict,
                               income_stmt: pd.DataFrame,
                               balance_sheet: pd.DataFrame) -> dict:
    """整合所有基本面数据"""
    valuation   = get_valuation(info)
    profitability = get_profitability(info, income_stmt)
    health      = get_financial_health(info, balance_sheet)

    return {
        **valuation,
        **profitability,
        **health,
    }


def fmt_pct(val, decimals=1) -> str:
    if val is None:
        return "—"
    return f"{val * 100:.{decimals}f}%"


def fmt_num(val, decimals=2) -> str:
    if val is None:
        return "—"
    return f"{val:.{decimals}f}"


def fmt_large(val) -> str:
    """格式化大数（市值等）"""
    if val is None:
        return "—"
    if val >= 1e12:
        return f"{val/1e12:.2f}T"
    if val >= 1e8:
        return f"{val/1e8:.2f}亿"
    if val >= 1e6:
        return f"{val/1e6:.2f}M"
    return str(val)
