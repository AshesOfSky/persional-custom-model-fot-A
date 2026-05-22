"""
data_router.py — 智能数据路由，自动选择最优数据源
整合 yfinance 和 akshare，解决数据覆盖不全的问题
新增：本地缓存支持 + 华尔街化财报/行业/Beta 接口（B.0 升级）
"""

import pandas as pd
import numpy as np
from typing import Optional, Callable, Dict, List, Tuple
import logging

# 导入两个数据源模块
from modules.data_fetcher import fetch_all as yf_fetch_all, fetch_ohlcv as yf_fetch_ohlcv
from modules.data_fetcher_akshare import (
    fetch_unified as ak_fetch_unified,
    fetch_futures_ohlcv,
    fetch_etf_ohlcv,
    fetch_hk_stock_ohlcv,
    TICKER_MAPPING,
)
from modules.data_cache import get_cache, DataCache

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# A 股基准指数（沪深 300）/ 美股基准（S&P 500）
A_SHARE_BENCHMARK = "000300"   # 沪深 300（akshare 指数代码）
US_BENCHMARK = "^GSPC"         # S&P 500（yfinance 代码）

# 申万一级行业预置 Beta（Damodaran 风格兜底，用于数据源和回归都失败时）
SW_INDUSTRY_DEFAULT_BETA = {
    "食品饮料": 0.85, "家用电器": 0.90, "医药生物": 0.95,
    "计算机": 1.25, "传媒": 1.30, "通信": 1.20, "电子": 1.35,
    "银行": 0.75, "非银金融": 1.10, "房地产": 1.15,
    "钢铁": 1.20, "有色金属": 1.35, "煤炭": 1.25,
    "化工": 1.10, "建筑材料": 1.10, "建筑装饰": 1.05,
    "汽车": 1.15, "机械设备": 1.15, "电力设备": 1.20, "国防军工": 1.15,
    "交通运输": 1.00, "商贸零售": 1.00, "社会服务": 1.05,
    "农林牧渔": 0.95, "纺织服饰": 1.00, "轻工制造": 1.05,
    "公用事业": 0.80, "环保": 1.05, "美容护理": 1.00,
    "石油石化": 1.00, "综合": 1.10,
}


class DataRouter:
    """
    智能数据路由器
    根据代码类型自动选择最佳数据源
    新增：缓存优先策略
    """

    # 数据源优先级配置
    SOURCE_PRIORITY = {
        "A股个股": ["cache", "akshare", "yfinance"],
        "A股ETF": ["cache", "akshare", "yfinance"],  # ETF 也添加 yfinance 备选
        "A股指数": ["cache", "akshare", "yfinance"],
        "港股": ["cache", "akshare", "yfinance"],
        "美股": ["cache", "yfinance", "akshare"],
        "美股ETF": ["cache", "yfinance", "akshare"],
        "美股期货": ["cache", "yfinance"],
        "国内期货": ["cache", "akshare"],
        "外汇": ["cache", "akshare"],
    }

    def __init__(self, use_cache: bool = True, cache_hours: int = 24):
        self.source_stats = {"cache": 0, "yfinance": 0, "akshare": 0, "failed": 0}
        self.use_cache = use_cache
        self.cache = get_cache(cache_hours) if use_cache else None
        if self.cache:
            # 启动时清理过期缓存
            self.cache.clean_expired()

        # Beta 计算缓存（避免 Tornado 等敏感性分析重复触发网络请求）
        self._beta_cache: Dict[str, float] = {}
        # 限流保护：记录最近失败的时间戳
        self._rate_limited_until: float = 0.0
        # 申万行业缓存（避免敏感性分析循环重复请求）
        self._industry_cache: Dict[str, str] = {}

    def classify_ticker(self, ticker: str) -> str:
        """识别代码类型"""
        t = ticker.upper().strip()

        # 检查映射表
        if t in TICKER_MAPPING:
            data_type, _ = TICKER_MAPPING[t]
            if data_type == "etf":
                return "A股ETF"
            elif data_type == "futures":
                return "国内期货"
            elif data_type == "us_futures":
                return "美股期货"
            elif data_type == "fx":
                return "外汇"
            elif data_type == "index":
                return "A股指数"
            elif data_type == "us_etf":
                return "美股ETF"

        # 检查是否是纯数字（A股ETF或个股）
        if t.isdigit():
            # 6位数字代码
            if len(t) == 6:
                if t.startswith('0') or t.startswith('3'):
                    return "A股个股"  # 深市个股
                elif t.startswith('6'):
                    return "A股个股"  # 沪市个股
                elif t.startswith('1') or t.startswith('5'):
                    return "A股ETF"   # ETF基金（1开头是深市，5开头是沪市）
                elif t.startswith('2'):
                    return "A股个股"  # 深市B股（较少）
                elif t.startswith('9'):
                    return "A股个股"  # 沪市B股（较少）
            # 其他长度数字，可能是期货代码
            return "国内期货"

        # 美股期货特征判断 (以 =F 结尾)
        if t.endswith("=F"):
            return "美股期货"

        # 期货代码特征判断 (如 GC00Y, GC26H, CL25M 等期货月代码格式)
        import re
        # 匹配期货月代码格式：基础代码(2-4字母) + 年份数字(2位) + 月份代码
        # 如 GC00Y (黄金)、CL26H (原油) 等
        futures_month_pattern = r'^[A-Z]{2,4}\d{2}[A-Z]$'
        if re.match(futures_month_pattern, t):
            return "美股期货"

        # 根据后缀判断
        if ".SS" in t or ".SZ" in t:
            return "A股个股"
        elif ".HK" in t:
            return "港股"
        elif ".US" in t or ".NYSE" in t or ".NASDAQ" in t:
            return "美股"
        else:
            return "美股"

    def get_ohlcv(self, ticker: str, period: str = "1Y", timeframe: str = "日线", use_cache: bool = None) -> pd.DataFrame:
        """
        获取 OHLCV 数据，自动路由到最佳数据源
        优先从缓存读取，缓存未命中再从数据源获取
        支持多时间框架: 日线/周线/月线/小时线
        """
        if use_cache is None:
            use_cache = self.use_cache

        asset_type = self.classify_ticker(ticker)
        sources = self.SOURCE_PRIORITY.get(asset_type, ["cache", "yfinance"])

        # 构建缓存key（包含时间框架）
        cache_key = f"{ticker}_{timeframe}"

        # 首先尝试从缓存获取
        if use_cache and "cache" in sources and self.cache:
            cached_df = self.cache.get(cache_key, period)
            if cached_df is not None:
                self.source_stats["cache"] += 1
                logger.info(f"[{asset_type}] {ticker} ({timeframe}) 从缓存获取成功")
                return cached_df

        last_error = None

        # 缓存未命中，从数据源获取
        for source in sources:
            if source == "cache":
                continue  # 已尝试

            try:
                if asset_type == "美股期货":
                    # 美股期货直接使用 yfinance，但先映射月代码到连续合约
                    from modules.data_fetcher_akshare import fetch_us_futures_ohlcv, _map_futures_month_to_continuous
                    mapped_ticker = _map_futures_month_to_continuous(ticker)
                    if mapped_ticker is None:
                        raise ValueError(f"无法映射期货代码: {ticker}")
                    df = fetch_us_futures_ohlcv(mapped_ticker, period, timeframe)
                elif source == "akshare":
                    df = ak_fetch_unified(ticker, period, timeframe)
                else:
                    df = yf_fetch_ohlcv(ticker, period, timeframe)

                if not df.empty:
                    self.source_stats[source] += 1
                    logger.info(f"[{asset_type}] {ticker} ({timeframe}) 使用 {source} 获取成功")

                    # 存入缓存
                    if use_cache and self.cache:
                        self.cache.set(cache_key, period, df)

                    return df

            except Exception as e:
                last_error = e
                logger.warning(f"{source} 获取 {ticker} 失败: {e}")
                continue

        # 所有数据源都失败
        self.source_stats["failed"] += 1
        raise ValueError(f"无法获取 {ticker} 数据，已尝试: {sources}. 最后错误: {last_error}")

    def get_info(self, ticker: str) -> dict:
        """
        获取股票基本信息
        对于 akshare 数据源，返回模拟的 info 结构
        对于期货代码，返回空字典（期货没有基本面信息）
        """
        asset_type = self.classify_ticker(ticker)

        # 期货代码不获取基本面信息，直接返回空字典
        if asset_type in ["国内期货", "美股期货"]:
            logger.info(f"{ticker} 是期货代码，跳过基本面信息获取")
            return {}

        # 目前主要依赖 yfinance 获取基本信息
        # 可以后续扩展 akshare 的基本面数据
        from modules.data_fetcher import fetch_info

        try:
            return fetch_info(ticker)
        except Exception as e:
            logger.warning(f"获取 {ticker} 基本信息失败: {e}")
            return {}

    def get_all(self, ticker: str, period: str = "1Y", timeframe: str = "日线",
                include_statements: bool = True) -> dict:
        """
        统一入口：获取所有数据（含完整三表）
        Args:
            include_statements: 是否拉取三表；期货/外汇/ETF 等无财报标的可设为 False
        """
        ohlcv = self.get_ohlcv(ticker, period, timeframe)
        info = self.get_info(ticker)
        asset_type = self.classify_ticker(ticker)

        stmts = {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
        if include_statements:
            # 期货/外汇/ETF 无财报，直接跳过
            if asset_type not in ("国内期货", "美股期货", "外汇"):
                stmts = self.get_financial_statements(ticker)

        return {
            "ticker": ticker.upper().strip(),
            "market": asset_type,
            "period": period,
            "timeframe": timeframe,
            "ohlcv": ohlcv,
            "info": info,
            "income_stmt": stmts.get("income", pd.DataFrame()),
            "balance_sheet": stmts.get("balance", pd.DataFrame()),
            "cashflow": stmts.get("cashflow", pd.DataFrame()),
            "data_source": self._get_source_name(ticker),
        }

    def _get_source_name(self, ticker: str) -> str:
        """获取实际使用的数据源名称（用于调试）"""
        asset_type = self.classify_ticker(ticker)
        sources = self.SOURCE_PRIORITY.get(asset_type, ["yfinance"])
        return sources[0] if sources else "unknown"

    # ========================================================================
    # B.0 华尔街化升级 —— 财报/行业/Beta 数据接口
    # ========================================================================

    def get_financial_statements(self, ticker: str, report_type: str = "annual",
                                  limit: int = 5) -> Dict[str, pd.DataFrame]:
        """
        获取完整三表（标准化 DataFrame）
        逐表缓存：有数据 TTL 168h（7 天），空 DataFrame TTL 12h（允许网络恢复后重试）
        """
        from plugins.data_ingestion.financial_reports import fetch_statements

        result = {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
        period_key = f"{report_type}_{limit}"
        tables = ["income", "balance", "cashflow"]

        # 1. 尝试从缓存逐表读取
        tables_to_fetch = []
        for table in tables:
            if self.cache:
                cached = self.cache.get(ticker, period_key, data_type=f"statements_{table}")
                if cached is not None:
                    result[table] = cached
                    logger.info(f"财务报表缓存命中: {ticker} {table}")
                    continue
            tables_to_fetch.append(table)

        if not tables_to_fetch:
            return result

        # 2. 未命中的表走网络请求（整表拉取后拆分到各表缓存）
        try:
            fetched = fetch_statements(ticker, report_type=report_type, limit=limit)
        except Exception as e:
            logger.error(f"get_financial_statements failed for {ticker}: {e}")
            fetched = {}

        for table in tables_to_fetch:
            df = fetched.get(table, pd.DataFrame())
            result[table] = df
            if self.cache:
                # 有数据缓存 7 天，空表缓存 12 小时（允许网络恢复后重试）
                ttl = 168 if not df.empty else 12
                self.cache.set(ticker, period_key, df, data_type=f"statements_{table}", cache_hours=ttl)
                logger.info(f"财务报表缓存已更新: {ticker} {table} (TTL={ttl}h, shape={df.shape})")

        return result

    def get_income_statement(self, ticker: str, report_type: str = "annual",
                              limit: int = 5) -> pd.DataFrame:
        """获取利润表（标准化列名）"""
        stmts = self.get_financial_statements(ticker, report_type, limit)
        return stmts.get("income", pd.DataFrame())

    def get_balance_sheet(self, ticker: str, report_type: str = "annual",
                           limit: int = 5) -> pd.DataFrame:
        """获取资产负债表（标准化列名）"""
        stmts = self.get_financial_statements(ticker, report_type, limit)
        return stmts.get("balance", pd.DataFrame())

    def get_cash_flow(self, ticker: str, report_type: str = "annual",
                       limit: int = 5) -> pd.DataFrame:
        """获取现金流量表（标准化列名）"""
        stmts = self.get_financial_statements(ticker, report_type, limit)
        return stmts.get("cashflow", pd.DataFrame())

    def get_shares_outstanding(self, ticker: str) -> float:
        """
        获取总股本（亿股）
        A股：从资产负债表 'shares_outstanding'（实收资本/股本）推算，或 akshare 直接获取
        美股：yfinance info.sharesOutstanding
        """
        is_a_share = (
            ticker.endswith(".SS") or ticker.endswith(".SZ") or
            (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
        )
        try:
            if is_a_share:
                code = ticker.replace(".SS", "").replace(".SZ", "")
                # 优先从资产负债表拿股本（单位：元，需除以面值1元 = 股数）
                bs = self.get_balance_sheet(ticker, limit=1)
                if not bs.empty and "shares_outstanding" in bs.columns:
                    shares = bs["shares_outstanding"].iloc[-1]
                    if pd.notna(shares) and shares > 0:
                        # A 股股本字段通常是 "实收资本"，单位为元，面值1元 → 股数
                        return shares / 1e8  # 转为亿股
                # fallback: akshare 直接获取
                import akshare as ak
                df = ak.stock_individual_info_em(symbol=code)
                if df is not None and not df.empty:
                    total_shares = df[df["item"] == "总股本"]["value"].values
                    if len(total_shares) > 0:
                        return float(total_shares[0]) / 1e8  # 转为亿股
            else:
                # 美股 yfinance
                import yfinance as yf
                stock = yf.Ticker(ticker)
                info = stock.info
                shares = info.get("sharesOutstanding", 0)
                if shares:
                    return shares / 1e8  # 转为亿股
        except Exception as e:
            logger.warning(f"get_shares_outstanding failed for {ticker}: {e}")
        return 1.0  # 兜底 1 亿股

    def get_net_debt(self, ticker: str) -> float:
        """
        获取净债务 = 总债务 - 现金（亿元）
        """
        try:
            bs = self.get_balance_sheet(ticker, limit=1)
            if not bs.empty:
                total_debt = 0.0
                cash_val = 0.0
                if "total_debt" in bs.columns:
                    total_debt = bs["total_debt"].iloc[-1]
                # fallback: short + long
                elif "short_term_debt" in bs.columns or "long_term_debt" in bs.columns:
                    st = bs.get("short_term_debt", pd.Series([0])).iloc[-1]
                    lt = bs.get("long_term_debt", pd.Series([0])).iloc[-1]
                    total_debt = (st if pd.notna(st) else 0) + (lt if pd.notna(lt) else 0)
                if "cash" in bs.columns:
                    cash_val = bs["cash"].iloc[-1]
                if pd.notna(total_debt) and pd.notna(cash_val):
                    # A 股报表单位通常是元，转为亿元
                    is_a_share = (
                        ticker.endswith(".SS") or ticker.endswith(".SZ") or
                        (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
                    )
                    unit_div = 1e8 if is_a_share else 1e8  # 统一亿元
                    return (total_debt - cash_val) / unit_div
        except Exception as e:
            logger.warning(f"get_net_debt failed for {ticker}: {e}")
        return 0.0

    def get_beta(self, ticker: str, period_years: int = 3) -> float:
        """
        Beta 三级 fallback（含实例缓存 + 限流保护）：
        1. 数据源获取（yfinance/akshare）
        2. 对基准指数做 OLS 回归（A股=沪深300，美股=S&P500）
        3. 申万行业平均 Beta 兜底
        返回杠杆 Beta（ equity beta ）
        """
        # 检查实例缓存
        cache_key = f"{ticker}_{period_years}"
        if cache_key in self._beta_cache:
            return self._beta_cache[cache_key]

        # 限流保护：如果最近被限流，跳过网络请求直接 fallback
        import time
        if time.time() < self._rate_limited_until:
            logger.info(f"Rate limit protection active for {ticker}, skipping network Beta")
            return self._beta_fallback(ticker)

        is_a_share = (
            ticker.endswith(".SS") or ticker.endswith(".SZ") or
            (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
        )

        # Level 1: yfinance info.beta（仅美股）
        if not is_a_share:
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                beta = stock.info.get("beta")
                if beta is not None and beta > 0:
                    self._beta_cache[cache_key] = float(beta)
                    logger.info(f"Beta for {ticker} from yfinance info: {beta:.3f}")
                    return self._beta_cache[cache_key]
            except Exception:
                pass

        # Level 2: OLS 回归（A 股用沪深 300，美股用 SPY）
        try:
            benchmark = A_SHARE_BENCHMARK if is_a_share else "SPY"
            beta_est = self._calculate_beta_from_ohlcv(ticker, benchmark, period_years)
            if beta_est is not None and 0.2 <= beta_est <= 3.0:
                self._beta_cache[cache_key] = float(beta_est)
                logger.info(f"Beta for {ticker} from OLS regression: {beta_est:.3f}")
                return self._beta_cache[cache_key]
        except Exception as e:
            logger.warning(f"OLS Beta calculation failed for {ticker}: {e}")

        # Level 3 fallback
        beta = self._beta_fallback(ticker)
        self._beta_cache[cache_key] = beta
        return beta

    def _beta_fallback(self, ticker: str) -> float:
        """申万行业 Beta 兜底 → 1.0"""
        try:
            sw_industry = self.get_industry_sw(ticker)
            if sw_industry:
                beta_fallback = SW_INDUSTRY_DEFAULT_BETA.get(sw_industry)
                if beta_fallback is not None:
                    logger.info(f"Beta for {ticker} from SW industry fallback ({sw_industry}): {beta_fallback:.3f}")
                    return float(beta_fallback)
        except Exception:
            pass
        logger.warning(f"Beta for {ticker} falling back to 1.0")
        return 1.0

    def _calculate_beta_from_ohlcv(self, ticker: str, benchmark: str,
                                    period_years: int = 3) -> Optional[float]:
        """
        用日收益率做 OLS 回归计算 Beta（含限流检测）
        """
        import time
        try:
            # 获取股价数据（带限流检测）
            try:
                stock_df = self.get_ohlcv(ticker, period=f"{period_years}Y", timeframe="日线")
            except ValueError as e:
                if "Rate limited" in str(e) or "Too Many Requests" in str(e):
                    self._rate_limited_until = time.time() + 60  # 限流保护 60 秒
                raise

            try:
                bench_df = self.get_ohlcv(benchmark, period=f"{period_years}Y", timeframe="日线")
            except ValueError as e:
                if "Rate limited" in str(e) or "Too Many Requests" in str(e):
                    self._rate_limited_until = time.time() + 60
                raise

            if stock_df.empty or bench_df.empty:
                return None

            # 对齐日期
            common_dates = stock_df.index.intersection(bench_df.index)
            if len(common_dates) < 60:
                return None

            stock_close = stock_df.loc[common_dates, "close"]
            bench_close = bench_df.loc[common_dates, "close"]

            # 计算日收益率
            stock_ret = stock_close.pct_change().dropna()
            bench_ret = bench_close.pct_change().dropna()

            # 再对齐
            common = stock_ret.index.intersection(bench_ret.index)
            stock_ret = stock_ret.loc[common].values
            bench_ret = bench_ret.loc[common].values

            if len(stock_ret) < 60:
                return None

            # OLS: r_stock = alpha + beta * r_benchmark
            cov = np.cov(stock_ret, bench_ret)[0, 1]
            var_b = np.var(bench_ret)
            if var_b == 0:
                return None
            beta = cov / var_b

            # 合理性截断
            beta = max(0.2, min(3.0, beta))
            return beta
        except Exception as e:
            err_str = str(e)
            if "Rate limited" in err_str or "Too Many Requests" in err_str:
                self._rate_limited_until = time.time() + 60
                logger.warning(f"Rate limited detected for {ticker}, blocking network requests for 60s")
            else:
                logger.warning(f"Beta regression failed: {e}")
            return None

    def get_industry_sw(self, ticker: str) -> str:
        """
        获取申万一级行业分类名称
        使用 akshare stock_industry_category_cninfo
        实例级 memoize：成功结果写入 self._industry_cache，避免敏感性分析循环重复请求
        """
        t = ticker.upper().strip()
        if t in self._industry_cache:
            return self._industry_cache[t]

        is_a_share = (
            t.endswith(".SS") or t.endswith(".SZ") or
            (t.replace(".", "").isdigit() and len(t.replace(".", "")) == 6)
        )
        if not is_a_share:
            return ""  # 美股暂不返回申万分类

        code = t.replace(".SS", "").replace(".SZ", "")
        result = ""
        try:
            import akshare as ak
            # 申万行业分类
            df = ak.stock_industry_category_cninfo(symbol="申万行业分类")
            if df is not None and not df.empty:
                match = df[df["代码"].astype(str) == code]
                if not match.empty:
                    result = str(match["行业名称"].iloc[0])
        except Exception as e:
            logger.warning(f"get_industry_sw (cninfo) failed for {ticker}: {e}")

        if not result:
            try:
                import akshare as ak
                df = ak.stock_individual_info_em(symbol=code)
                if df is not None and not df.empty:
                    industry_row = df[df["item"] == "行业"]
                    if not industry_row.empty:
                        result = str(industry_row["value"].iloc[0])
            except Exception as e:
                logger.warning(f"get_industry_sw (em) failed for {ticker}: {e}")

        self._industry_cache[t] = result
        return result

    def get_peers_by_industry(self, ticker: str,
                               market_cap_range: Tuple[float, float] = (0.3, 3.0),
                               revenue_range: Tuple[float, float] = (0.2, 5.0),
                               max_count: int = 12) -> List[Dict]:
        """
        按申万行业筛选可比公司
        返回列表: [{ticker, name, market_cap, revenue, industry}, ...]
        """
        is_a_share = (
            ticker.endswith(".SS") or ticker.endswith(".SZ") or
            (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
        )
        if not is_a_share:
            return []  # 美股可比公司筛选暂不实现

        code = ticker.replace(".SS", "").replace(".SZ", "")
        try:
            import akshare as ak

            # 1. 获取目标公司信息
            sw_industry = self.get_industry_sw(ticker)
            if not sw_industry:
                logger.warning(f"Cannot find SW industry for {ticker}")
                return []

            # 2. 获取目标公司市值（从实时行情）
            target_info = ak.stock_individual_info_em(symbol=code)
            target_market_cap = 0.0
            if target_info is not None and not target_info.empty:
                mc_row = target_info[target_info["item"] == "总市值"]
                if not mc_row.empty:
                    target_market_cap = float(mc_row["value"].iloc[0])

            # 3. 拉取全市场 A 股实时行情
            spot_df = ak.stock_zh_a_spot_em()
            if spot_df is None or spot_df.empty:
                return []

            peers = []
            for _, row in spot_df.iterrows():
                try:
                    peer_code = str(row.get("代码", "")).strip()
                    peer_name = str(row.get("名称", ""))
                    peer_industry = str(row.get("所属行业", ""))
                    # 行业匹配：申万一级名称可能不完全一致，用包含关系
                    if sw_industry not in peer_industry and peer_industry not in sw_industry:
                        continue
                    if peer_code == code:
                        continue

                    # 市值筛选
                    mc_str = str(row.get("总市值", "0")).replace("-", "0")
                    peer_mc = float(mc_str) if mc_str else 0.0
                    if target_market_cap > 0 and peer_mc > 0:
                        ratio = peer_mc / target_market_cap
                        if not (market_cap_range[0] <= ratio <= market_cap_range[1]):
                            continue

                    peers.append({
                        "ticker": f"{peer_code}.SS" if peer_code.startswith("6") else f"{peer_code}.SZ",
                        "name": peer_name,
                        "market_cap": peer_mc,
                        "revenue": 0.0,  # 实时行情无收入，后续补
                        "industry": peer_industry,
                    })
                except Exception:
                    continue

            # 按市值接近程度排序取 Top N
            if target_market_cap > 0:
                peers.sort(key=lambda x: abs(1 - x["market_cap"] / target_market_cap))
            peers = peers[:max_count]
            return peers

        except Exception as e:
            logger.error(f"get_peers_by_industry failed for {ticker}: {e}")
            return []

    def get_capital_structure(self, ticker: str) -> Tuple[float, float]:
        """
        获取资本结构 (debt_ratio, equity_ratio)
        从最近一期资产负债表计算
        """
        try:
            bs = self.get_balance_sheet(ticker, limit=1)
            if not bs.empty:
                total_debt = 0.0
                equity = 0.0
                if "total_debt" in bs.columns:
                    total_debt = bs["total_debt"].iloc[-1]
                if "shareholders_equity" in bs.columns:
                    equity = bs["shareholders_equity"].iloc[-1]
                if pd.notna(total_debt) and pd.notna(equity) and (total_debt + equity) > 0:
                    # A 股报表单位是元，但 ratio 不受单位影响
                    total = total_debt + equity
                    return total_debt / total, equity / total
        except Exception as e:
            logger.warning(f"get_capital_structure failed for {ticker}: {e}")
        return 0.3, 0.7  # 默认

    def get_quote(self, ticker: str) -> Dict:
        """
        获取股票实时行情（供 Comps 使用）
        """
        is_a_share = (
            ticker.endswith(".SS") or ticker.endswith(".SZ") or
            (ticker.replace(".", "").isdigit() and len(ticker.replace(".", "")) == 6)
        )
        try:
            if is_a_share:
                code = ticker.replace(".SS", "").replace(".SZ", "")
                import akshare as ak
                df = ak.stock_individual_info_em(symbol=code)
                if df is not None and not df.empty:
                    items = dict(zip(df["item"], df["value"]))
                    return {
                        "name": items.get("股票简称", ""),
                        "industry": items.get("行业", ""),
                        "market_cap": float(items.get("总市值", 0) or 0),
                    }
            else:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                info = stock.info
                return {
                    "name": info.get("longName", info.get("shortName", "")),
                    "industry": info.get("industry", ""),
                    "market_cap": info.get("marketCap", 0) or 0,
                }
        except Exception as e:
            logger.warning(f"get_quote failed for {ticker}: {e}")
        return {"name": "", "industry": "", "market_cap": 0.0}

    def get_stats(self) -> dict:
        """获取数据源使用统计"""
        total = sum(self.source_stats.values())
        if total == 0:
            return {"status": "无数据请求记录"}

        stats = {
            "total_requests": total,
            "cache_usage": f"{self.source_stats.get('cache', 0) / total * 100:.1f}%",
            "yfinance_usage": f"{self.source_stats['yfinance'] / total * 100:.1f}%",
            "akshare_usage": f"{self.source_stats['akshare'] / total * 100:.1f}%",
            "failure_rate": f"{self.source_stats['failed'] / total * 100:.1f}%",
        }

        # 添加缓存系统统计
        if self.cache:
            cache_stats = self.cache.get_stats()
            stats["cache_records"] = cache_stats.get("total_records", 0)
            stats["cache_db_size_mb"] = cache_stats.get("db_size_mb", 0)

        return stats


# 全局路由器实例
_router = None


def get_router(use_cache: bool = True, cache_hours: int = 24) -> DataRouter:
    """获取路由器实例（单例模式）"""
    global _router
    if _router is None:
        _router = DataRouter(use_cache=use_cache, cache_hours=cache_hours)
    return _router


def fetch_all(ticker: str, period: str = "1Y", timeframe: str = "日线",
              use_cache: bool = True, include_statements: bool = True) -> dict:
    """统一入口：获取所有数据"""
    return get_router(use_cache=use_cache).get_all(
        ticker, period, timeframe, include_statements=include_statements
    )


def fetch_ohlcv(ticker: str, period: str = "1Y", timeframe: str = "日线", use_cache: bool = True) -> pd.DataFrame:
    """获取 OHLCV 数据"""
    return get_router(use_cache=use_cache).get_ohlcv(ticker, period, timeframe)


def get_router_stats() -> dict:
    """获取数据源统计信息"""
    return get_router().get_stats()


def clear_data_cache(ticker: str = None, period: str = None) -> int:
    """清除数据缓存"""
    from modules.data_cache import clear_cache
    return clear_cache(ticker, period)
