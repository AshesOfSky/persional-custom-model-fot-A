"""
数据接入层 - 行情数据获取插件
对接Yahoo Finance, Tushare等量化接口
支持A股、港股、美股、期货、ETF等多种交易品种
"""

import yfinance as yf
import akshare as ak
import pandas as pd
from typing import Dict, List, Optional, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


@dataclass
class OHLCVData:
    """OHLCV数据结构"""
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: datetime


@dataclass
class MarketDataConfig:
    """市场数据配置"""
    # 代理设置
    use_proxy: bool = False
    proxy_url: str = ""

    # 重试设置
    max_retries: int = 3
    retry_delay: float = 1.0

    # 超时设置
    connect_timeout: int = 10
    read_timeout: int = 30

    # 缓存设置
    cache_enabled: bool = True
    cache_ttl: int = 300  # 5分钟


class MarketDataPlugin:
    """
    行情数据获取插件
    统一接口获取多市场数据
    """

    # 市场代码映射
    MARKET_MAPPING = {
        # A股
        "A股": {
            "suffix": ".SS",
            "data_source": "akshare",
        },
        "港股": {
            "suffix": ".HK",
            "data_source": "yfinance",
        },
        "美股": {
            "suffix": "",
            "data_source": "yfinance",
        },
        "美股期货": {
            "suffix": "=F",
            "data_source": "yfinance",
        },
    }

    def __init__(self, config: MarketDataConfig = None):
        self.config = config or MarketDataConfig()
        self._session = self._create_session()
        self._cache = {}

        # 设置代理
        if self.config.use_proxy and self.config.proxy_url:
            import os
            os.environ['HTTP_PROXY'] = self.config.proxy_url
            os.environ['HTTPS_PROXY'] = self.config.proxy_url

    def _create_session(self) -> requests.Session:
        """创建带重试机制的HTTP会话"""
        session = requests.Session()

        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.retry_delay,
            status_forcelist=[429, 500, 502, 503, 504],
        )

        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        return session

    def _get_cache_key(self, ticker: str, period: str, interval: str) -> str:
        """生成缓存键"""
        return f"{ticker}_{period}_{interval}"

    def _get_from_cache(self, key: str) -> Optional[pd.DataFrame]:
        """从缓存获取数据"""
        if not self.config.cache_enabled:
            return None

        if key in self._cache:
            data, timestamp = self._cache[key]
            if (datetime.now() - timestamp).seconds < self.config.cache_ttl:
                logger.info(f"Cache hit: {key}")
                return data
            else:
                del self._cache[key]
        return None

    def _save_to_cache(self, key: str, data: pd.DataFrame):
        """保存数据到缓存"""
        if self.config.cache_enabled:
            self._cache[key] = (data, datetime.now())

    def detect_market(self, ticker: str) -> str:
        """识别代码所属市场"""
        t = ticker.upper().strip()

        # 检查后缀
        if t.endswith('.SS') or t.endswith('.SZ'):
            return "A股"
        elif t.endswith('.HK'):
            return "港股"
        elif t.endswith('=F'):
            return "美股期货"
        elif t.isdigit() and len(t) == 6:
            # A股纯数字代码
            return "A股"
        else:
            return "美股"

    def fetch_ohlcv(
        self,
        ticker: str,
        period: str = "1Y",
        interval: str = "1d",
        market: str = None
    ) -> pd.DataFrame:
        """
        获取OHLCV数据

        Args:
            ticker: 股票代码
            period: 时间周期 (1M, 3M, 6M, 1Y, 2Y, 5Y)
            interval: 时间间隔 (1d, 1wk, 1mo, 1h)
            market: 市场类型 (自动检测 if None)

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
        """
        # 检查缓存
        cache_key = self._get_cache_key(ticker, period, interval)
        cached_data = self._get_from_cache(cache_key)
        if cached_data is not None:
            return cached_data

        # 识别市场
        if market is None:
            market = self.detect_market(ticker)

        logger.info(f"Fetching {ticker} ({market}) data: {period} / {interval}")

        # 根据市场选择数据源
        try:
            if market == "A股":
                df = self._fetch_a_share(ticker, period, interval)
            elif market == "美股期货":
                df = self._fetch_us_futures(ticker, period, interval)
            else:
                df = self._fetch_yfinance(ticker, period, interval)

            # 保存到缓存
            self._save_to_cache(cache_key, df)
            return df

        except Exception as e:
            logger.error(f"Failed to fetch {ticker}: {e}")
            raise

    def _fetch_yfinance(
        self,
        ticker: str,
        period: str,
        interval: str
    ) -> pd.DataFrame:
        """通过yfinance获取数据"""
        # 周期映射
        period_map = {
            "1M": "1mo", "3M": "3mo", "6M": "6mo",
            "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"
        }
        yf_period = period_map.get(period, "1y")

        # 尝试多次获取
        for attempt in range(self.config.max_retries):
            try:
                stock = yf.Ticker(ticker)
                df = stock.history(period=yf_period, interval=interval)

                if df.empty:
                    raise ValueError(f"No data returned for {ticker}")

                # 标准化列名
                df = df[["Open", "High", "Low", "Close", "Volume"]]
                df = df.dropna()

                logger.info(f"Successfully fetched {len(df)} rows for {ticker}")
                return df

            except Exception as e:
                logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < self.config.max_retries - 1:
                    time.sleep(self.config.retry_delay * (attempt + 1))
                else:
                    raise

    def _fetch_us_futures(
        self,
        ticker: str,
        period: str,
        interval: str
    ) -> pd.DataFrame:
        """获取美股期货数据"""
        # 期货代码直接使用yfinance
        return self._fetch_yfinance(ticker, period, interval)

    def _fetch_a_share(
        self,
        ticker: str,
        period: str,
        interval: str
    ) -> pd.DataFrame:
        """通过akshare获取A股数据"""
        # 去除后缀
        code = ticker.replace('.SS', '').replace('.SZ', '')

        # 计算起始日期
        period_days = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365,
                       "2Y": 730, "5Y": 1825, "10Y": 3650}
        days = period_days.get(period, 365)

        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end_date = datetime.now().strftime("%Y%m%d")

        # 转换周期
        period_map = {"1d": "daily", "1wk": "weekly", "1mo": "monthly"}
        ak_period = period_map.get(interval, "daily")

        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period=ak_period,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )

            if df.empty:
                raise ValueError(f"No data returned for {ticker}")

            # 标准化列名
            df = df.rename(columns={
                "日期": "Date",
                "开盘": "Open",
                "最高": "High",
                "最低": "Low",
                "收盘": "Close",
                "成交量": "Volume",
            })
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date")
            df = df[["Open", "High", "Low", "Close", "Volume"]]

            logger.info(f"Successfully fetched {len(df)} rows for {ticker}")
            return df

        except Exception as e:
            logger.error(f"akshare failed for {ticker}: {e}")
            # 尝试使用yfinance作为备选
            logger.info(f"Trying yfinance as fallback for {ticker}")
            return self._fetch_yfinance(ticker, period, interval)

    def fetch_multi_tickers(
        self,
        tickers: List[str],
        period: str = "1Y",
        interval: str = "1d"
    ) -> Dict[str, pd.DataFrame]:
        """
        批量获取多个品种数据

        Returns:
            Dict: {ticker: DataFrame}
        """
        results = {}
        for ticker in tickers:
            try:
                df = self.fetch_ohlcv(ticker, period, interval)
                results[ticker] = df
            except Exception as e:
                logger.error(f"Failed to fetch {ticker}: {e}")
                results[ticker] = pd.DataFrame()
        return results

    def get_ticker_info(self, ticker: str) -> Dict:
        """获取品种基本信息"""
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            # 提取关键信息
            return {
                "name": info.get("longName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "market_cap": info.get("marketCap", None),
                "pe": info.get("trailingPE", None),
                "pb": info.get("priceToBook", None),
                "dividend_yield": info.get("dividendYield", None),
                "52w_high": info.get("fiftyTwoWeekHigh", None),
                "52w_low": info.get("fiftyTwoWeekLow", None),
                "avg_volume": info.get("averageVolume", None),
                "currency": info.get("currency", "USD"),
            }
        except Exception as e:
            logger.error(f"Failed to get info for {ticker}: {e}")
            return {}


# 便捷函数
def fetch_market_data(
    ticker: str,
    period: str = "1Y",
    interval: str = "1d"
) -> pd.DataFrame:
    """便捷函数：获取市场数据"""
    plugin = MarketDataPlugin()
    return plugin.fetch_ohlcv(ticker, period, interval)
