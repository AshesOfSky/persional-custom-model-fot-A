"""
dragon_screener.py — 龙空龙战术面板
扫描A股市场，筛选过去一年涨停次数超过N次的"龙头股"
按板块分组展示，支持一键筛选

策略：使用 ak.stock_zt_pool_em(date) 获取每日涨停池
比逐股扫描5000+只股票高效得多（~250次API调用 vs ~5000次）
"""

import akshare as ak
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
import logging
import time
import json
import os
import sqlite3

logger = logging.getLogger(__name__)

# 涨停阈值
LIMIT_UP_THRESHOLD_MAIN = 9.8      # 主板涨停阈值 (%)
LIMIT_UP_THRESHOLD_GEM = 19.8      # 创业板/科创板涨停阈值 (%)

# 创业板/科创板代码前缀
GEM_PREFIXES = ("300", "301")       # 创业板
STAR_PREFIXES = ("688", "689")      # 科创板


def is_gem_or_star(code: str) -> bool:
    """判断是否创业板或科创板"""
    code = str(code).strip()
    return code.startswith(GEM_PREFIXES) or code.startswith(STAR_PREFIXES)


def _get_trading_dates(days: int = 250) -> List[str]:
    """
    获取最近N个交易日日期列表
    使用 akshare 的交易日历或回退到工作日估算
    """
    try:
        # 尝试使用 akshare 交易日历
        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            today = pd.Timestamp.now().normalize()
            recent = df[df["trade_date"] <= today].tail(days)
            return [d.strftime("%Y%m%d") for d in recent["trade_date"]]
    except Exception as e:
        logger.warning(f"获取交易日历失败: {e}")

    # 回退：使用工作日估算
    dates = []
    current = datetime.now()
    while len(dates) < days:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 周一到周五
            dates.append(current.strftime("%Y%m%d"))
    return dates


def scan_all_stocks_for_dragons(
    min_limit_up: int = 10,
    days: int = 250,
    progress_callback: Optional[Callable] = None,
) -> pd.DataFrame:
    """
    全市场涨停龙头扫描

    策略:
    1. 获取最近days个交易日的涨停池数据 (ak.stock_zt_pool_em)
    2. 按股票代码聚合涨停次数
    3. 过滤 >= min_limit_up 的股票
    4. 补充板块信息

    Returns: DataFrame with columns:
        代码, 名称, 板块, 涨停次数, 最大连板, 最近涨停日, 最新价, 流通市值
    """
    trading_dates = _get_trading_dates(days)
    if not trading_dates:
        logger.error("无法获取交易日期")
        return pd.DataFrame()

    # 收集所有涨停记录
    all_records = []
    total = len(trading_dates)
    failed_count = 0

    for idx, date_str in enumerate(trading_dates):
        if progress_callback:
            pct = int((idx + 1) / total * 80)  # 80% for data collection
            progress_callback(pct / 100, f"扫描涨停数据: {date_str} ({idx+1}/{total})")

        try:
            df = ak.stock_zt_pool_em(date=date_str)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get("代码", "")).strip()
                    name = str(row.get("名称", "")).strip()
                    if code and len(code) == 6:
                        all_records.append({
                            "代码": code,
                            "名称": name,
                            "涨停日期": date_str,
                            "连板数": int(row.get("连板数", 1)) if "连板数" in df.columns else 1,
                        })
        except Exception as e:
            failed_count += 1
            if failed_count > 10 and failed_count > idx * 0.3:
                logger.error(f"涨停数据获取失败率过高，中止扫描: {e}")
                break
            logger.debug(f"获取 {date_str} 涨停池失败: {e}")

        # API限流
        time.sleep(0.3)

    if not all_records:
        logger.warning("未获取到任何涨停记录")
        return pd.DataFrame()

    # 聚合统计
    records_df = pd.DataFrame(all_records)

    if progress_callback:
        progress_callback(0.85, "正在聚合统计...")

    # 按股票聚合
    agg = records_df.groupby(["代码", "名称"]).agg(
        涨停次数=("涨停日期", "count"),
        最近涨停日=("涨停日期", "max"),
        最大连板=("连板数", "max"),
    ).reset_index()

    # 过滤
    result = agg[agg["涨停次数"] >= min_limit_up].copy()
    result = result.sort_values("涨停次数", ascending=False).reset_index(drop=True)

    if result.empty:
        return result

    if progress_callback:
        progress_callback(0.90, "正在获取板块信息...")

    # 补充板块信息
    result["板块"] = "未知"
    result["最新价"] = 0.0
    result["流通市值"] = 0.0

    try:
        # 批量获取板块信息
        board_info = _get_board_mapping()
        for idx, row in result.iterrows():
            code = row["代码"]
            if code in board_info:
                result.at[idx, "板块"] = board_info[code]
    except Exception as e:
        logger.warning(f"获取板块信息失败: {e}")

    # 尝试获取最新价和市值
    try:
        spot_df = ak.stock_zh_a_spot_em()
        if spot_df is not None and not spot_df.empty:
            spot_df["代码"] = spot_df["代码"].astype(str)
            for idx, row in result.iterrows():
                match = spot_df[spot_df["代码"] == row["代码"]]
                if not match.empty:
                    m = match.iloc[0]
                    result.at[idx, "最新价"] = float(m.get("最新价", 0) or 0)
                    result.at[idx, "流通市值"] = float(m.get("流通市值", 0) or 0)
    except Exception as e:
        logger.warning(f"获取实时行情失败: {e}")

    if progress_callback:
        progress_callback(1.0, "扫描完成!")

    return result


def _get_board_mapping() -> Dict[str, str]:
    """
    获取股票→行业板块映射
    使用 akshare 板块成分股接口
    """
    mapping = {}
    try:
        # 获取所有行业板块
        boards = ak.stock_board_industry_name_em()
        if boards is None or boards.empty:
            return mapping

        # 遍历前30个主要板块获取成分股
        for _, board_row in boards.head(30).iterrows():
            board_name = str(board_row.get("板块名称", ""))
            if not board_name:
                continue
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
                if cons is not None and not cons.empty:
                    for _, stock in cons.iterrows():
                        code = str(stock.get("代码", "")).strip()
                        if code:
                            mapping[code] = board_name
                time.sleep(0.2)
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"获取板块映射失败: {e}")

    return mapping


def group_by_sector(df: pd.DataFrame) -> Dict[str, List[Dict]]:
    """按行业板块分组"""
    if df is None or df.empty:
        return {}

    grouped = {}
    for _, row in df.iterrows():
        sector = row.get("板块", "未知")
        if sector not in grouped:
            grouped[sector] = []
        grouped[sector].append(row.to_dict())

    # 按龙头数量排序
    grouped = dict(sorted(grouped.items(), key=lambda x: -len(x[1])))
    return grouped


def build_sector_heatmap_data(grouped: Dict) -> pd.DataFrame:
    """构建热力图数据"""
    rows = []
    for sector, stocks in grouped.items():
        avg_zt = sum(s.get("涨停次数", 0) for s in stocks) / len(stocks) if stocks else 0
        rows.append({
            "板块": sector,
            "龙头数量": len(stocks),
            "平均涨停": round(avg_zt, 1),
            "最强龙头": stocks[0].get("名称", "") if stocks else "",
        })
    return pd.DataFrame(rows)


class DragonScreenerCache:
    """龙头扫描结果缓存（SQLite）"""

    def __init__(self, cache_hours: int = 24):
        self.cache_hours = cache_hours
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        self.db_path = os.path.join(cache_dir, "dragon_cache.db")
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS dragon_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE,
                    data TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"初始化龙头缓存失败: {e}")

    def get_cached_result(self, min_zt: int = 10, days: int = 250) -> Optional[pd.DataFrame]:
        """获取缓存结果"""
        try:
            conn = sqlite3.connect(self.db_path)
            cache_key = f"dragon_{min_zt}_{days}"
            cursor = conn.execute(
                "SELECT data, created_at FROM dragon_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                created = datetime.fromisoformat(row[1])
                if (datetime.now() - created).total_seconds() < self.cache_hours * 3600:
                    return pd.read_json(row[0], orient="records")
        except Exception as e:
            logger.warning(f"读取龙头缓存失败: {e}")
        return None

    def save_result(self, df: pd.DataFrame, min_zt: int = 10, days: int = 250):
        """保存缓存结果"""
        try:
            conn = sqlite3.connect(self.db_path)
            cache_key = f"dragon_{min_zt}_{days}"
            data = df.to_json(orient="records", force_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO dragon_cache (cache_key, data, created_at) VALUES (?, ?, ?)",
                (cache_key, data, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存龙头缓存失败: {e}")
