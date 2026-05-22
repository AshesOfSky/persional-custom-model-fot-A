"""
data_cache.py — 本地数据缓存系统
使用SQLite存储历史数据，减少重复下载
"""

import os
import sqlite3
import pickle
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 缓存目录
CACHE_DIR = Path(__file__).parent.parent / "cache"
CACHE_DB = CACHE_DIR / "data_cache.db"

# 默认缓存过期时间（小时）
DEFAULT_CACHE_HOURS = 24


class DataCache:
    """数据缓存管理器"""

    def __init__(self, cache_hours: int = DEFAULT_CACHE_HOURS):
        self.cache_hours = cache_hours
        self._ensure_cache_dir()
        self._init_db()

    def _ensure_cache_dir(self):
        """确保缓存目录存在"""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _init_db(self):
        """初始化SQLite数据库"""
        with sqlite3.connect(CACHE_DB) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_data (
                    key TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    period TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    data BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    last_hit TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ticker_period
                ON cache_data(ticker, period)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expires
                ON cache_data(expires_at)
            """)
            conn.commit()

    def _make_key(self, ticker: str, period: str, data_type: str = "ohlcv") -> str:
        """生成缓存键"""
        key_str = f"{ticker.upper()}:{period}:{data_type}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, ticker: str, period: str, data_type: str = "ohlcv") -> pd.DataFrame | None:
        """
        从缓存获取数据
        返回DataFrame或None（如果缓存不存在或已过期）
        """
        key = self._make_key(ticker, period, data_type)

        with sqlite3.connect(CACHE_DB) as conn:
            cursor = conn.execute(
                "SELECT data, expires_at FROM cache_data WHERE key = ?",
                (key,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            data_blob, expires_at = row
            expires = datetime.fromisoformat(expires_at)

            # 检查是否过期
            if datetime.now() > expires:
                logger.info(f"缓存已过期: {ticker} {period}")
                conn.execute("DELETE FROM cache_data WHERE key = ?", (key,))
                conn.commit()
                return None

            # 更新命中统计
            conn.execute(
                """UPDATE cache_data
                   SET hit_count = hit_count + 1, last_hit = ?
                   WHERE key = ?""",
                (datetime.now().isoformat(), key)
            )
            conn.commit()

            # 反序列化数据
            try:
                df = pickle.loads(data_blob)
                logger.info(f"缓存命中: {ticker} {period}")
                return df
            except Exception as e:
                logger.error(f"缓存数据反序列化失败: {e}")
                return None

    def set(self, ticker: str, period: str, data: pd.DataFrame,
            data_type: str = "ohlcv", cache_hours: int = None) -> bool:
        """
        将数据存入缓存
        """
        if cache_hours is None:
            cache_hours = self.cache_hours

        key = self._make_key(ticker, period, data_type)
        expires_at = datetime.now() + timedelta(hours=cache_hours)

        try:
            data_blob = pickle.dumps(data)

            with sqlite3.connect(CACHE_DB) as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO cache_data
                       (key, ticker, period, data_type, data, expires_at, hit_count, last_hit)
                       VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
                    (key, ticker.upper(), period, data_type, data_blob,
                     expires_at.isoformat(), datetime.now().isoformat())
                )
                conn.commit()

            logger.info(f"缓存已更新: {ticker} {period} (过期: {expires_at})")
            return True

        except Exception as e:
            logger.error(f"缓存写入失败: {e}")
            return False

    def invalidate(self, ticker: str = None, period: str = None) -> int:
        """
        使缓存失效
        如果ticker为None，清除所有缓存
        返回清除的记录数
        """
        with sqlite3.connect(CACHE_DB) as conn:
            if ticker is None:
                cursor = conn.execute("DELETE FROM cache_data")
            elif period is None:
                cursor = conn.execute(
                    "DELETE FROM cache_data WHERE ticker = ?",
                    (ticker.upper(),)
                )
            else:
                cursor = conn.execute(
                    "DELETE FROM cache_data WHERE ticker = ? AND period = ?",
                    (ticker.upper(), period)
                )
            conn.commit()
            deleted = cursor.rowcount

        logger.info(f"缓存已清除: {deleted} 条记录")
        return deleted

    def clean_expired(self) -> int:
        """
        清理过期缓存
        返回清理的记录数
        """
        with sqlite3.connect(CACHE_DB) as conn:
            cursor = conn.execute(
                "DELETE FROM cache_data WHERE expires_at < ?",
                (datetime.now().isoformat(),)
            )
            conn.commit()
            deleted = cursor.rowcount

        if deleted > 0:
            logger.info(f"清理过期缓存: {deleted} 条记录")
        return deleted

    def get_stats(self) -> dict:
        """获取缓存统计信息"""
        with sqlite3.connect(CACHE_DB) as conn:
            # 总记录数
            cursor = conn.execute("SELECT COUNT(*) FROM cache_data")
            total = cursor.fetchone()[0]

            # 过期记录数
            cursor = conn.execute(
                "SELECT COUNT(*) FROM cache_data WHERE expires_at < ?",
                (datetime.now().isoformat(),)
            )
            expired = cursor.fetchone()[0]

            # 总命中次数
            cursor = conn.execute("SELECT SUM(hit_count) FROM cache_data")
            total_hits = cursor.fetchone()[0] or 0

            # 数据库大小
            db_size = CACHE_DB.stat().st_size if CACHE_DB.exists() else 0

            # 按ticker分组统计
            cursor = conn.execute("""
                SELECT ticker, COUNT(*) as count, SUM(hit_count) as hits
                FROM cache_data
                GROUP BY ticker
                ORDER BY hits DESC
                LIMIT 10
            """)
            top_tickers = [
                {"ticker": row[0], "count": row[1], "hits": row[2]}
                for row in cursor.fetchall()
            ]

        return {
            "total_records": total,
            "expired_records": expired,
            "total_hits": total_hits,
            "db_size_mb": round(db_size / (1024 * 1024), 2),
            "cache_file": str(CACHE_DB),
            "top_tickers": top_tickers,
        }

    def get_cache_info(self, ticker: str, period: str, data_type: str = "ohlcv") -> dict:
        """获取特定缓存的详细信息"""
        key = self._make_key(ticker, period, data_type)

        with sqlite3.connect(CACHE_DB) as conn:
            cursor = conn.execute(
                """SELECT created_at, expires_at, hit_count, last_hit
                   FROM cache_data WHERE key = ?""",
                (key,)
            )
            row = cursor.fetchone()

            if not row:
                return {"exists": False}

            created_at, expires_at, hit_count, last_hit = row
            expires = datetime.fromisoformat(expires_at)
            is_expired = datetime.now() > expires

            return {
                "exists": True,
                "is_expired": is_expired,
                "created_at": created_at,
                "expires_at": expires_at,
                "hit_count": hit_count,
                "last_hit": last_hit,
            }


# 全局缓存实例
_cache_instance = None


def get_cache(cache_hours: int = DEFAULT_CACHE_HOURS) -> DataCache:
    """获取缓存实例（单例模式）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DataCache(cache_hours)
    return _cache_instance


def cached_fetch(fetch_func):
    """
    装饰器：为数据获取函数添加缓存
    使用示例:
    @cached_fetch
    def fetch_data(ticker, period):
        return yf.download(ticker, period=period)
    """
    def wrapper(ticker: str, period: str, *args, **kwargs):
        cache = get_cache()
        cache.clean_expired()

        # 尝试从缓存获取
        cached_data = cache.get(ticker, period)
        if cached_data is not None:
            return cached_data

        # 缓存未命中，获取数据
        data = fetch_func(ticker, period, *args, **kwargs)

        # 存入缓存
        if data is not None and not data.empty:
            cache.set(ticker, period, data)

        return data

    return wrapper


# 便捷函数
def clear_cache(ticker: str = None, period: str = None) -> int:
    """清空缓存"""
    return get_cache().invalidate(ticker, period)


def get_cache_stats() -> dict:
    """获取缓存统计"""
    return get_cache().get_stats()


def show_cache_status():
    """打印缓存状态"""
    stats = get_cache_stats()
    print(f"缓存状态:")
    print(f"  总记录数: {stats['total_records']}")
    print(f"  过期记录: {stats['expired_records']}")
    print(f"  总命中次数: {stats['total_hits']}")
    print(f"  数据库大小: {stats['db_size_mb']} MB")
    print(f"  缓存文件: {stats['cache_file']}")
    if stats['top_tickers']:
        print(f"  热门标的:")
        for t in stats['top_tickers'][:5]:
            print(f"    {t['ticker']}: {t['count']}条记录, {t['hits']}次命中")
