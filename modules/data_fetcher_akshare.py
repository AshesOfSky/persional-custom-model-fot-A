"""
data_fetcher_akshare.py — 使用 akshare 获取 A股/期货/外汇数据
作为 yfinance 的补充数据源
"""

import akshare as ak
import pandas as pd
import time
import logging
from datetime import datetime, timedelta

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _retry_fetch(fetch_func, max_retries=3, delay=1):
    """带重试机制的数据获取"""
    for attempt in range(max_retries):
        try:
            return fetch_func()
        except Exception as e:
            if "ProxyError" in str(e) or "Max retries" in str(e):
                logger.warning(f"网络错误，第 {attempt + 1} 次重试...")
                if attempt < max_retries - 1:
                    time.sleep(delay * (attempt + 1))  # 递增延迟
                else:
                    raise
            else:
                raise


def fetch_a_share_ohlcv(code: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取 A股 历史行情（支持个股、指数、ETF）
    code: 如 "600519" 或 "518880"（ETF）
    timeframe: 日线/周线/月线
    """
    # 映射周期到 akshare 参数
    period_map_daily = {
        "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825,
    }
    period_map_weekly = {
        "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650,
    }
    period_map_monthly = {
        "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650, "MAX": 36500,
    }

    # 根据时间框架选择参数
    if timeframe == "周线":
        ak_period = "weekly"
        days = period_map_weekly.get(period, 365)
    elif timeframe == "月线":
        ak_period = "monthly"
        days = period_map_monthly.get(period, 1825)
    else:  # 日线
        ak_period = "daily"
        days = period_map_daily.get(period, 365)

    def _fetch():
        # 使用 akshare 获取数据
        df = ak.stock_zh_a_hist(
            symbol=code.replace(".SS", "").replace(".SZ", ""),
            period=ak_period,
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"  # 前复权
        )

        if df.empty:
            return pd.DataFrame()

        # 标准化列名以兼容现有代码
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

        return df

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        # akshare 失败，尝试使用 yfinance 作为备选
        logger.warning(f"akshare 获取 {code} ({timeframe}) 失败，尝试 yfinance: {e}")
        return _fetch_from_yfinance(code, period, timeframe)


def fetch_etf_ohlcv(code: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """获取 A股 ETF 数据（如黄金ETF 518880）"""
    return fetch_a_share_ohlcv(code, period, timeframe)


def _fetch_from_yfinance(code: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    使用 yfinance 获取 A股数据（作为 akshare 的备选）
    对于深市ETF，需要添加 .SZ 后缀
    对于沪市ETF/个股，需要添加 .SS 后缀
    """
    try:
        import yfinance as yf

        # 处理已有后缀的代码（修正错误的后缀）
        if '.SS' in code or '.SZ' in code:
            # 提取纯数字代码
            pure_code = code.replace('.SS', '').replace('.SZ', '')
            # 根据代码开头重新判断市场
            if pure_code.startswith('0') or pure_code.startswith('1') or pure_code.startswith('3'):
                yf_code = f"{pure_code}.SZ"  # 深市
            elif pure_code.startswith('6') or pure_code.startswith('5'):
                yf_code = f"{pure_code}.SS"  # 沪市
            else:
                yf_code = code  # 无法判断，保持原样
        # 判断市场并添加后缀
        elif code.isdigit() and len(code) == 6:
            if code.startswith('0') or code.startswith('1') or code.startswith('3'):
                yf_code = f"{code}.SZ"  # 深市
            elif code.startswith('6') or code.startswith('5'):
                yf_code = f"{code}.SS"  # 沪市
            else:
                yf_code = f"{code}.SS"  # 默认沪市
        else:
            yf_code = code

        # 根据时间框架选择周期和间隔
        if timeframe == "周线":
            tf_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y"}
            yf_period = tf_map.get(period, "1y")
            interval = "1wk"
        elif timeframe == "月线":
            tf_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"}
            yf_period = tf_map.get(period, "1y")
            interval = "1mo"
        else:  # 日线
            tf_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            yf_period = tf_map.get(period, "1y")
            interval = "1d"

        logger.info(f"使用 yfinance 获取 {yf_code} ({timeframe})")
        ticker = yf.Ticker(yf_code)
        df = ticker.history(period=yf_period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception as e:
        logger.error(f"yfinance 备选获取 {code} 也失败: {e}")
        raise ValueError(f"所有数据源获取 {code} 都失败")


def _fetch_us_from_yfinance_direct(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """直接使用 yfinance 获取美股数据"""
    try:
        import yfinance as yf

        # 根据时间框架选择周期和间隔
        if timeframe == "周线":
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y"}
            interval = "1wk"
        elif timeframe == "月线":
            period_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"}
            interval = "1mo"
        else:  # 日线
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1d"

        yf_period = period_map.get(period, "1y")

        logger.info(f"使用 yfinance 获取美股 {symbol} ({timeframe})")
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=yf_period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception as e:
        logger.error(f"yfinance 获取美股 {symbol} 失败: {e}")
        raise ValueError(f"获取美股 {symbol} 失败: {e}")


def fetch_index_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取 A股指数数据
    symbol: 如 "sh000001" (上证指数), "sz399001" (深证成指)
    timeframe: 日线/周线/月线
    """
    # 根据时间框架选择参数
    if timeframe == "周线":
        ak_period = "weekly"
        period_map = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650}
    elif timeframe == "月线":
        ak_period = "monthly"
        period_map = {"1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650, "MAX": 36500}
    else:  # 日线
        ak_period = "daily"
        period_map = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825}

    days = period_map.get(period, 365)

    def _fetch():
        df = ak.index_zh_a_hist(
            symbol=symbol.replace("sh", "").replace("sz", ""),
            period=ak_period,
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )

        if df.empty:
            return pd.DataFrame()

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

        return df

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        raise ValueError(f"获取指数 {symbol} 失败: {e}")


def fetch_futures_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取国内期货主力连续数据（黄金、白银等）
    symbol: 如 "AU0" (黄金主力连续), "AG0" (白银主力连续), "RB0" (螺纹钢主力连续)
    使用 akshare.futures_main_sina 接口
    timeframe: 日线/周线/月线 (注: 期货数据可能只支持日线)
    """
    # 期货暂只支持日线
    period_map = {
        "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825,
    }
    days = period_map.get(period, 365)

    def _fetch():
        # 使用 futures_main_sina 获取主力连续合约数据
        df = ak.futures_main_sina(
            symbol=symbol,
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
        )

        if df.empty:
            return pd.DataFrame()

        # 列名已经是英文，直接重命名
        df = df.rename(columns={
            "日期": "Date",
            "开盘价": "Open",
            "最高价": "High",
            "最低价": "Low",
            "收盘价": "Close",
            "成交量": "Volume",
        })

        # 确保数值列为 float 类型
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")

        # 只选择需要的列（期货还有持仓量等额外列，我们不需要）
        df = df[["Open", "High", "Low", "Close", "Volume"]]

        return df

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        raise ValueError(f"获取期货 {symbol} 失败: {e}")


def fetch_us_stock_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取美股数据（使用 akshare 的雅虎财经接口，失败时直接用 yfinance）
    timeframe: 日线/周线/月线
    """
    # 根据时间框架选择周期
    if timeframe == "周线":
        period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y"}
    elif timeframe == "月线":
        period_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"}
    else:  # 日线
        period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}

    yf_period = period_map.get(period, "1y")

    def _fetch():
        df = ak.stock_us_hist(
            symbol=symbol,
            period=yf_period,
            adjust="qfq"
        )

        if df.empty:
            return pd.DataFrame()

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

        return df

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        # 美股直接用 yfinance
        logger.warning(f"akshare 获取美股 {symbol} 失败，尝试 yfinance: {e}")
        return _fetch_us_from_yfinance_direct(symbol, period)


# 代码映射表：用户输入 -> 数据源识别
TICKER_MAPPING = {
    # A股 ETF (沪市5开头，深市1开头)
    "518880": ("etf", "518880"),      # 黄金 ETF
    "513050": ("etf", "513050"),      # 中概互联 ETF
    "510300": ("etf", "510300"),      # 沪深 300 ETF
    "510050": ("etf", "510050"),      # 上证 50 ETF
    "510500": ("etf", "510500"),      # 中证 500 ETF
    "512100": ("etf", "512100"),      # 中证 1000 ETF
    "512880": ("etf", "512880"),      # 证券 ETF
    "512690": ("etf", "512690"),      # 酒 ETF
    "512000": ("etf", "512000"),      # 券商 ETF
    "512480": ("etf", "512480"),      # 半导体 ETF
    "515030": ("etf", "515030"),      # 新能源车 ETF
    "515790": ("etf", "515790"),      # 光伏 ETF
    "512170": ("etf", "512170"),      # 医疗 ETF
    "512010": ("etf", "512010"),      # 医药 ETF
    "159915": ("etf", "159915"),      # 创业板 ETF
    "159952": ("etf", "159952"),      # 创业板 ETF (建信)
    "159949": ("etf", "159949"),      # 创业板 50 ETF
    "588000": ("etf", "588000"),      # 科创 50 ETF
    "588080": ("etf", "588080"),      # 科创 50 ETF (易方达)
    "159995": ("etf", "159995"),      # 芯片 ETF
    "159928": ("etf", "159928"),      # 消费 ETF
    "159825": ("etf", "159825"),      # 农业 ETF
    "159992": ("etf", "159992"),      # 创新药 ETF
    "159856": ("etf", "159856"),      # 地产 ETF
    "159766": ("etf", "159766"),      # 旅游 ETF
    "159819": ("etf", "159819"),      # 人工智能 ETF
    "159740": ("etf", "159740"),      # 恒生科技 ETF
    "159920": ("etf", "159920"),      # 恒生 ETF
    "159941": ("etf", "159941"),      # 纳指 ETF
    "159326": ("etf", "159326"),      # 能源 ETF
    "159337": ("etf", "159337"),      # 绿电 ETF

    # A股指数
    "SH000001": ("index", "000001"),  # 上证指数
    "SZ399001": ("index", "399001"),  # 深证成指
    "SH000300": ("index", "000300"),  # 沪深 300
    "SH000016": ("index", "000016"),  # 上证 50
    "SH000905": ("index", "000905"),  # 中证 500
    "SZ399006": ("index", "399006"),  # 创业板指
    "SH000688": ("index", "000688"),  # 科创 50
    "SZ399005": ("index", "399005"),  # 中小板指
    "SZ399673": ("index", "399673"),  # 创业板 50

    # 美股指数 ETF
    "SPY": ("us_etf", "SPY"),         # 标普 500 ETF
    "QQQ": ("us_etf", "QQQ"),         # 纳斯达克 100 ETF
    "DIA": ("us_etf", "DIA"),         # 道琼斯 ETF
    "IWM": ("us_etf", "IWM"),         # 罗素 2000 ETF
    "VOO": ("us_etf", "VOO"),         #  Vanguard 标普 500
    "VTI": ("us_etf", "VTI"),         #  Vanguard 全市场

    # 美股期货 (Yahoo Finance格式)
    # 贵金属期货
    "GC=F": ("us_futures", "GC=F"),   # COMEX黄金期货
    "SI=F": ("us_futures", "SI=F"),   # COMEX白银期货
    "HG=F": ("us_futures", "HG=F"),   # COMEX铜期货
    "GOLD": ("us_futures", "GC=F"),   # 黄金期货别名
    "SILVER": ("us_futures", "SI=F"), # 白银期货别名

    # 能源期货
    "CL=F": ("us_futures", "CL=F"),   # WTI原油期货
    "BZ=F": ("us_futures", "BZ=F"),   # 布伦特原油期货
    "NG=F": ("us_futures", "NG=F"),   # 天然气期货
    "HO=F": ("us_futures", "HO=F"),   # 取暖油期货
    "RB=F": ("us_futures", "RB=F"),   # RBOB汽油期货
    "OIL": ("us_futures", "CL=F"),    # 原油期货别名
    "WTI": ("us_futures", "CL=F"),    # WTI原油别名
    "BRENT": ("us_futures", "BZ=F"),  # 布伦特原油别名
    "NATGAS": ("us_futures", "NG=F"), # 天然气别名

    # 股指期货
    "ES=F": ("us_futures", "ES=F"),   # 标普500指数期货
    "NQ=F": ("us_futures", "NQ=F"),   # 纳斯达克100指数期货
    "YM=F": ("us_futures", "YM=F"),   # 道琼斯指数期货
    "RTY=F": ("us_futures", "RTY=F"), # 罗素2000指数期货
    "SPX500": ("us_futures", "ES=F"), # 标普500期货别名
    "NAS100": ("us_futures", "NQ=F"), # 纳指期货别名

    # 农产品期货
    "ZC=F": ("us_futures", "ZC=F"),   # 玉米期货
    "ZS=F": ("us_futures", "ZS=F"),   # 大豆期货
    "ZW=F": ("us_futures", "ZW=F"),   # 小麦期货
    "ZL=F": ("us_futures", "ZL=F"),   # 豆油期货
    "ZM=F": ("us_futures", "ZM=F"),   # 豆粕期货
    "ZC=F": ("us_futures", "ZC=F"),   # 玉米期货
    "CT=F": ("us_futures", "CT=F"),   # 棉花期货
    "KC=F": ("us_futures", "KC=F"),   # 咖啡期货
    "CC=F": ("us_futures", "CC=F"),   # 可可期货
    "SB=F": ("us_futures", "SB=F"),   # 糖期货
    "CORN": ("us_futures", "ZC=F"),   # 玉米别名
    "SOYBEAN": ("us_futures", "ZS=F"),# 大豆别名
    "WHEAT": ("us_futures", "ZW=F"),  # 小麦别名

    # 畜牧期货
    "LE=F": ("us_futures", "LE=F"),   # 活牛期货
    "HE=F": ("us_futures", "HE=F"),   # 瘦肉猪期货
    "GF=F": ("us_futures", "GF=F"),   # 育肥牛期货

    # 金融期货
    "ZB=F": ("us_futures", "ZB=F"),   # 长期国债期货
    "ZN=F": ("us_futures", "ZN=F"),   # 10年期国债期货
    "ZF=F": ("us_futures", "ZF=F"),   # 5年期国债期货
    "ZT=F": ("us_futures", "ZT=F"),   # 2年期国债期货

    # 期货（主力连续）- 国内期货
    # 贵金属
    "GCMAIN": ("futures", "AU0"),     # 黄金主力连续
    "XAUUSD": ("futures", "AU0"),     # 黄金美元映射到国内黄金
    "AU0": ("futures", "AU0"),        # 黄金主力连续
    "AG0": ("futures", "AG0"),        # 白银主力连续
    "XAGUSD": ("futures", "AG0"),     # 白银美元映射到国内白银
    "CU0": ("futures", "CU0"),        # 铜主力连续
    "AL0": ("futures", "AL0"),        # 铝主力连续
    "ZN0": ("futures", "ZN0"),        # 锌主力连续
    "NI0": ("futures", "NI0"),        # 镍主力连续
    "SN0": ("futures", "SN0"),        # 锡主力连续
    "PB0": ("futures", "PB0"),        # 铅主力连续

    # 能源化工
    "CL0": ("futures", "SC0"),        # 原油主力连续（映射到国内上海原油）
    "SC0": ("futures", "SC0"),        # 上海原油主力连续
    "BU0": ("futures", "BU0"),        # 沥青主力连续
    "L0": ("futures", "L0"),          # 塑料主力连续
    "PP0": ("futures", "PP0"),        # 聚丙烯主力连续
    "PVC0": ("futures", "V0"),        # PVC主力连续
    "TA0": ("futures", "TA0"),        # PTA主力连续
    "MA0": ("futures", "MA0"),        # 甲醇主力连续
    "EG0": ("futures", "EG0"),        # 乙二醇主力连续
    "EB0": ("futures", "EB0"),        # 苯乙烯主力连续
    "RU0": ("futures", "RU0"),        # 橡胶主力连续

    # 黑色系
    "SLMAIN": ("futures", "RB0"),     # 螺纹钢主力连续
    "RB0": ("futures", "RB0"),        # 螺纹钢主力连续
    "HC0": ("futures", "HC0"),        # 热卷主力连续
    "I0": ("futures", "I0"),          # 铁矿石主力连续
    "J0": ("futures", "J0"),          # 焦炭主力连续
    "JM0": ("futures", "JM0"),        # 焦煤主力连续
    "FG0": ("futures", "FG0"),        # 玻璃主力连续
    "SA0": ("futures", "SA0"),        # 纯碱主力连续

    # 农产品
    "M0": ("futures", "M0"),          # 豆粕主力连续
    "RM0": ("futures", "RM0"),        # 菜粕主力连续
    "C0": ("futures", "C0"),          # 玉米主力连续
    "CS0": ("futures", "CS0"),        # 淀粉主力连续
    "A0": ("futures", "A0"),          # 豆一主力连续
    "B0": ("futures", "B0"),          # 豆二主力连续
    "CF0": ("futures", "CF0"),        # 棉花主力连续
    "SR0": ("futures", "SR0"),        # 白糖主力连续
    "Y0": ("futures", "Y0"),          # 豆油主力连续
    "P0": ("futures", "P0"),          # 棕榈油主力连续
    "OI0": ("futures", "OI0"),        # 菜油主力连续
    "LH0": ("futures", "LH0"),        # 生猪主力连续
    "AP0": ("futures", "AP0"),        # 苹果主力连续
    "CJ0": ("futures", "CJ0"),        # 红枣主力连续

    # 金融期货
    "IF0": ("futures", "IF0"),        # 沪深 300 股指
    "IC0": ("futures", "IC0"),        # 中证 500 股指
    "IH0": ("futures", "IH0"),        # 上证 50 股指
    "IM0": ("futures", "IM0"),        # 中证 1000 股指

    # 外汇（主要货币对）
    "EURUSD": ("fx", "EURUSD"),       # 欧元美元
    "GBPUSD": ("fx", "GBPUSD"),       # 英镑美元
    "USDJPY": ("fx", "USDJPY"),       # 美元日元
    "AUDUSD": ("fx", "AUDUSD"),       # 澳元美元
    "USDCAD": ("fx", "USDCAD"),       # 美元加元
    "USDCHF": ("fx", "USDCHF"),       # 美元瑞郎
    "NZDUSD": ("fx", "NZDUSD"),       # 纽元美元
    "USDCNY": ("fx", "USDCNY"),       # 美元人民币
    "EURCNY": ("fx", "EURCNY"),       # 欧元人民币
    "XAUUSD": ("fx", "XAUUSD"),       # 黄金美元
    "XAGUSD": ("fx", "XAGUSD"),       # 白银美元
}


def fetch_us_futures_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取美股期货数据（通过yfinance）
    symbol: 如 "GC=F" (黄金), "CL=F" (原油), "ES=F" (标普500期货)
    """
    try:
        import yfinance as yf

        # 根据时间框架选择周期和间隔
        if timeframe == "周线":
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y"}
            interval = "1wk"
        elif timeframe == "月线":
            period_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"}
            interval = "1mo"
        else:  # 日线
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1d"

        yf_period = period_map.get(period, "1y")

        logger.info(f"使用 yfinance 获取美股期货 {symbol} ({timeframe})")
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=yf_period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception as e:
        logger.error(f"yfinance 获取美股期货 {symbol} 失败: {e}")
        raise ValueError(f"获取美股期货 {symbol} 失败: {e}")


# 期货月代码到连续合约的映射
FUTURES_MONTH_TO_CONTINUOUS = {
    # 黄金
    "GC": "GC=F",
    # 白银
    "SI": "SI=F",
    # 铜
    "HG": "HG=F",
    # 原油
    "CL": "CL=F",
    # 布伦特原油
    "BZ": "BZ=F",
    # 天然气
    "NG": "NG=F",
    # 取暖油
    "HO": "HO=F",
    # 汽油
    "RB": "RB=F",
    # 标普500
    "ES": "ES=F",
    # 纳指
    "NQ": "NQ=F",
    # 道指
    "YM": "YM=F",
    # 罗素2000
    "RTY": "RTY=F",
    # 玉米
    "ZC": "ZC=F",
    # 大豆
    "ZS": "ZS=F",
    # 小麦
    "ZW": "ZW=F",
    # 豆油
    "ZL": "ZL=F",
    # 豆粕
    "ZM": "ZM=F",
    # 活牛
    "LE": "LE=F",
    # 瘦肉猪
    "HE": "HE=F",
    # 长期国债
    "ZB": "ZB=F",
    # 10年期国债
    "ZN": "ZN=F",
}


def _map_futures_month_to_continuous(ticker: str) -> str:
    """
    将期货月代码（如 GC00Y, GC26H）映射到连续合约（如 GC=F）
    格式：基础代码(2-4字母) + 年份(2位) + 月份代码(1字母)
    """
    import re
    # 匹配期货月代码格式
    match = re.match(r'^([A-Z]{2,4})(\d{2})([A-Z])$', ticker.upper())
    if match:
        base_code = match.group(1)
        if base_code in FUTURES_MONTH_TO_CONTINUOUS:
            return FUTURES_MONTH_TO_CONTINUOUS[base_code]
    return ticker


def fetch_unified(ticker: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    统一入口：根据代码类型自动选择数据源
    支持多时间框架: 日线/周线/月线/小时线
    """
    ticker_upper = ticker.upper().strip()

    # 检查是否是期货月代码格式，如果是则映射到连续合约
    ticker_upper = _map_futures_month_to_continuous(ticker_upper)

    # 检查映射表
    if ticker_upper in TICKER_MAPPING:
        data_type, code = TICKER_MAPPING[ticker_upper]

        if data_type == "etf":
            return fetch_etf_ohlcv(code, period, timeframe)
        elif data_type == "futures":
            return fetch_futures_ohlcv(code, period, timeframe)
        elif data_type == "us_futures":
            return fetch_us_futures_ohlcv(code, period, timeframe)
        elif data_type == "fx":
            return fetch_fx_ohlcv(code, period, timeframe)
        elif data_type == "index":
            return fetch_index_ohlcv(code, period, timeframe)
        elif data_type == "us_etf":
            return fetch_us_stock_ohlcv(code, period, timeframe)

    # 根据代码特征判断市场
    # 纯数字代码（6位）- A股/ETF
    if ticker_upper.isdigit() and len(ticker_upper) == 6:
        if ticker_upper.startswith('1') or ticker_upper.startswith('5'):
            # ETF基金（1开头是深市ETF，5开头是沪市ETF）
            return fetch_etf_ohlcv(ticker_upper, period, timeframe)
        else:
            # A股个股（0开头深市，6开头沪市）
            return fetch_a_share_ohlcv(ticker_upper, period, timeframe)

    # 美股期货特征判断 (以 =F 结尾)
    if ticker_upper.endswith("=F"):
        return fetch_us_futures_ohlcv(ticker_upper, period, timeframe)

    # 带后缀的代码
    if ".SS" in ticker_upper or ".SZ" in ticker_upper:
        return fetch_a_share_ohlcv(ticker_upper, period, timeframe)
    elif ".HK" in ticker_upper:
        # 港股仍需使用 yfinance 或 akshare 的港股接口
        return fetch_hk_stock_ohlcv(ticker_upper, period, timeframe)
    else:
        # 美股优先使用 akshare
        return fetch_us_stock_ohlcv(ticker_upper, period, timeframe)


def fetch_fx_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取外汇数据
    支持主要货币对：EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, USDCNY 等
    timeframe: 日线/周线/月线 (注: 外汇数据可能只支持日线)
    """
    # 根据时间框架选择周期
    if timeframe == "周线":
        ak_period = "weekly"
    elif timeframe == "月线":
        ak_period = "monthly"
    else:  # 日线
        ak_period = "daily"

    def _fetch():
        # 使用 akshare 的外汇接口
        df = ak.currency_hist(
            symbol=symbol,
            period=ak_period
        )

        if df.empty:
            return pd.DataFrame()

        df = df.rename(columns={
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
        })
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date")

        # 确保数值列为 float 类型
        for col in ["Open", "High", "Low", "Close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 外汇数据可能没有成交量，设为 0
        if "Volume" not in df.columns:
            df["Volume"] = 0

        return df[["Open", "High", "Low", "Close", "Volume"]]

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        # 如果 akshare 失败，尝试使用 yfinance 作为后备
        logger.warning(f"akshare 获取外汇 {symbol} 失败，尝试 yfinance: {e}")
        return _fetch_fx_from_yfinance(symbol, period)


def _fetch_fx_from_yfinance(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """使用 yfinance 获取外汇数据"""
    try:
        import yfinance as yf

        # 根据时间框架选择周期和间隔
        if timeframe == "周线":
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1wk"
        elif timeframe == "月线":
            period_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1mo"
        else:  # 日线
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1d"

        yf_period = period_map.get(period, "1y")

        logger.info(f"使用 yfinance 获取外汇 {symbol} ({timeframe})")

        # 构建 yfinance 外汇代码 (如 EURUSD=X)
        yf_symbol = f"{symbol}=X"
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=yf_period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception as e:
        logger.error(f"yfinance 获取外汇 {symbol} 失败: {e}")
        raise ValueError(f"获取外汇 {symbol} 失败: {e}")


def fetch_hk_stock_ohlcv(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """
    获取港股数据
    timeframe: 日线/周线/月线
    """
    code = symbol.replace(".HK", "").zfill(5)

    # 根据时间框架选择参数
    if timeframe == "周线":
        ak_period = "weekly"
        period_map = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650}
    elif timeframe == "月线":
        ak_period = "monthly"
        period_map = {"1Y": 365, "2Y": 730, "5Y": 1825, "10Y": 3650, "MAX": 36500}
    else:  # 日线
        ak_period = "daily"
        period_map = {"1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "5Y": 1825}

    days = period_map.get(period, 365)

    def _fetch():
        df = ak.stock_hk_hist(
            symbol=code,
            period=ak_period,
            start_date=(datetime.now() - timedelta(days=days)).strftime("%Y%m%d"),
            end_date=datetime.now().strftime("%Y%m%d"),
            adjust="qfq"
        )

        if df.empty:
            return pd.DataFrame()

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

        return df

    try:
        return _retry_fetch(_fetch)
    except Exception as e:
        # 港股使用 yfinance 作为备选 (如 0700.HK 格式)
        logger.warning(f"akshare 获取港股 {symbol} 失败，尝试 yfinance: {e}")
        return _fetch_hk_from_yfinance(symbol, period)


def _fetch_hk_from_yfinance(symbol: str, period: str = "1Y", timeframe: str = "日线") -> pd.DataFrame:
    """使用 yfinance 获取港股数据"""
    try:
        import yfinance as yf

        # 根据时间框架选择周期和间隔
        if timeframe == "周线":
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y"}
            interval = "1wk"
        elif timeframe == "月线":
            period_map = {"1Y": "1y", "2Y": "2y", "5Y": "5y", "10Y": "10y", "MAX": "max"}
            interval = "1mo"
        else:  # 日线
            period_map = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y", "2Y": "2y", "5Y": "5y"}
            interval = "1d"

        yf_period = period_map.get(period, "1y")

        logger.info(f"使用 yfinance 获取港股 {symbol} ({timeframe})")
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=yf_period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # 标准化列名
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        return df

    except Exception as e:
        logger.error(f"yfinance 获取港股 {symbol} 失败: {e}")
        raise ValueError(f"获取港股 {symbol} 失败: {e}")
