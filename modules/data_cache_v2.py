"""
数据缓存层 V2

三级缓存架构:
- L1: 内存缓存 (Redis/本地dict) - 热点数据,秒级TTL
- L2: 本地磁盘 (SQLite) - 完整数据,小时级TTL
- L3: 时序数据库 (TimescaleDB) - 历史数据,永久存储

功能:
- 多级缓存查询
- 数据版本管理
- 增量更新
- 数据质量检查
- 缓存命中率监控
"""

import pandas as pd
import numpy as np
import sqlite3
import pickle
import hashlib
import json
import logging
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from datetime import datetime, timedelta
from pathlib import Path
import asyncio
from contextlib import contextmanager
import threading

logger = logging.getLogger(__name__)


class DataType(Enum):
    """数据类型"""
    DAILY_PRICE = "daily_price"
    INTRADAY_PRICE = "intraday_price"
    FINANCIAL_STATEMENT = "financial_statement"
    COMPANY_INFO = "company_info"
    NEWS = "news"
    MACRO_DATA = "macro_data"


class RefreshPolicy(Enum):
    """刷新策略"""
    AUTO = "auto"           # 自动决定
    FORCE = "force"         # 强制刷新
    CACHE_ONLY = "cache_only"  # 仅使用缓存


@dataclass
class CachedData:
    """缓存数据包装器"""
    key: str
    data: Any
    data_type: DataType
    version_id: Optional[str] = None
    source: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None
    access_count: int = 0
    last_accessed: Optional[datetime] = None
    metadata: Dict = field(default_factory=dict)

    def is_expired(self) -> bool:
        """检查是否过期"""
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at

    def touch(self):
        """更新访问时间"""
        self.access_count += 1
        self.last_accessed = datetime.now()


@dataclass
class DataVersion:
    """数据版本"""
    version_id: str
    ticker: str
    data_type: DataType
    source: str
    created_at: datetime
    data_hash: str
    description: str = ""
    metadata: Dict = field(default_factory=dict)
    is_active: bool = True


@dataclass
class QualityIssue:
    """质量问题"""
    type: str
    severity: str  # ERROR, WARNING, INFO
    message: str = ""
    details: Dict = field(default_factory=dict)
    dates: List = field(default_factory=list)


@dataclass
class QualityReport:
    """质量报告"""
    passed: bool
    issues: List[QualityIssue] = field(default_factory=list)

    def has_errors(self) -> bool:
        """是否有错误"""
        return any(i.severity == "ERROR" for i in self.issues)


@dataclass
class UpdateResult:
    """更新结果"""
    status: str  # SUCCESS, UP_TO_DATE, QUALITY_CHECK_FAILED, ERROR
    records_added: int = 0
    date_range: Optional[Tuple[datetime, datetime]] = None
    errors: List[str] = field(default_factory=list)


@dataclass
class CacheMetrics:
    """缓存指标"""
    total_requests: int = 0
    l1_hits: int = 0
    l2_hits: int = 0
    l3_hits: int = 0
    misses: int = 0

    @property
    def l1_hit_rate(self) -> float:
        return self.l1_hits / self.total_requests if self.total_requests > 0 else 0

    @property
    def overall_hit_rate(self) -> float:
        hits = self.l1_hits + self.l2_hits + self.l3_hits
        return hits / self.total_requests if self.total_requests > 0 else 0


# 缓存TTL配置 (秒)
CACHE_TTL_CONFIG = {
    DataType.DAILY_PRICE: {"l1": 300, "l2": 3600, "l3": None},
    DataType.INTRADAY_PRICE: {"l1": 60, "l2": 300, "l3": 86400},
    DataType.FINANCIAL_STATEMENT: {"l1": 86400, "l2": 604800, "l3": None},
    DataType.COMPANY_INFO: {"l1": 3600, "l2": 86400, "l3": None},
    DataType.NEWS: {"l1": 60, "l2": 600, "l3": 2592000},
    DataType.MACRO_DATA: {"l1": 3600, "l2": 86400, "l3": None},
}


class L1Cache:
    """一级缓存 - 内存缓存"""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url
        self.redis = None
        self.local_cache: Dict[str, CachedData] = {}
        self._lock = threading.RLock()

        # 尝试连接Redis
        if redis_url:
            try:
                import redis as redis_lib
                self.redis = redis_lib.Redis.from_url(redis_url, decode_responses=False)
                self.redis.ping()
                logger.info("L1Cache: Redis connected")
            except Exception as e:
                logger.warning(f"L1Cache: Redis connection failed, using local dict: {e}")
                self.redis = None

    def get(self, key: str) -> Optional[CachedData]:
        """获取缓存"""
        try:
            if self.redis:
                data = self.redis.get(f"l1:{key}")
                if data:
                    return pickle.loads(data)
            else:
                with self._lock:
                    if key in self.local_cache:
                        cached = self.local_cache[key]
                        if not cached.is_expired():
                            cached.touch()
                            return cached
                        else:
                            del self.local_cache[key]
            return None
        except Exception as e:
            logger.error(f"L1Cache get error: {e}")
            return None

    def set(self, key: str, data: CachedData, ttl: int = 300) -> None:
        """设置缓存"""
        try:
            data.expires_at = datetime.now() + timedelta(seconds=ttl)

            if self.redis:
                serialized = pickle.dumps(data)
                self.redis.setex(f"l1:{key}", ttl, serialized)
            else:
                with self._lock:
                    self.local_cache[key] = data
        except Exception as e:
            logger.error(f"L1Cache set error: {e}")

    def delete(self, key: str) -> None:
        """删除缓存"""
        try:
            if self.redis:
                self.redis.delete(f"l1:{key}")
            else:
                with self._lock:
                    self.local_cache.pop(key, None)
        except Exception as e:
            logger.error(f"L1Cache delete error: {e}")

    def clear(self) -> None:
        """清空缓存"""
        try:
            if self.redis:
                for key in self.redis.scan_iter(match="l1:*"):
                    self.redis.delete(key)
            else:
                with self._lock:
                    self.local_cache.clear()
        except Exception as e:
            logger.error(f"L1Cache clear error: {e}")


class L2Cache:
    """二级缓存 - 本地磁盘 (SQLite)"""

    def __init__(self, db_path: str = "cache/data_cache_v2.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_tables(self):
        """初始化表结构"""
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE NOT NULL,
                    data_type TEXT NOT NULL,
                    data BLOB NOT NULL,
                    data_hash TEXT,
                    version_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    access_count INTEGER DEFAULT 0,
                    last_accessed TIMESTAMP,
                    source TEXT,
                    metadata TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS data_versions (
                    version_id TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    data_type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT,
                    metadata TEXT,
                    is_active INTEGER DEFAULT 1
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS quality_checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    message TEXT,
                    details TEXT,
                    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 创建索引
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_key ON cache_entries(cache_key)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expires ON cache_entries(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_data_type ON cache_entries(data_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_versions_ticker ON data_versions(ticker, data_type)")

            conn.commit()

    def get(self, key: str) -> Optional[CachedData]:
        """获取缓存"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM cache_entries WHERE cache_key = ?",
                    (key,)
                )
                row = cursor.fetchone()

                if not row:
                    return None

                # 检查过期
                expires_at = row[7]
                if expires_at:
                    expires = datetime.fromisoformat(expires_at)
                    if datetime.now() > expires:
                        conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                        conn.commit()
                        return None

                # 反序列化数据
                data = pickle.loads(row[3])

                # 更新访问统计
                conn.execute(
                    """UPDATE cache_entries
                       SET access_count = access_count + 1,
                           last_accessed = CURRENT_TIMESTAMP
                       WHERE cache_key = ?""",
                    (key,)
                )
                conn.commit()

                return CachedData(
                    key=row[1],
                    data=data,
                    data_type=DataType(row[2]),
                    version_id=row[5],
                    source=row[11] or "",
                    created_at=datetime.fromisoformat(row[6]),
                    expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
                    access_count=row[8] + 1,
                    last_accessed=datetime.now(),
                    metadata=json.loads(row[12]) if row[12] else {},
                )
        except Exception as e:
            logger.error(f"L2Cache get error: {e}")
            return None

    def set(self, key: str, data: CachedData, ttl: int = 3600) -> None:
        """设置缓存"""
        try:
            serialized = pickle.dumps(data.data)
            data_hash = hashlib.md5(serialized).hexdigest()
            expires_at = datetime.now() + timedelta(seconds=ttl)
            metadata = json.dumps(data.metadata)

            with self._get_connection() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO cache_entries
                       (cache_key, data_type, data, data_hash, version_id,
                        expires_at, source, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (key, data.data_type.value, serialized, data_hash,
                     data.version_id, expires_at.isoformat(), data.source, metadata)
                )
                conn.commit()
        except Exception as e:
            logger.error(f"L2Cache set error: {e}")

    def delete(self, key: str) -> None:
        """删除缓存"""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM cache_entries WHERE cache_key = ?", (key,))
                conn.commit()
        except Exception as e:
            logger.error(f"L2Cache delete error: {e}")

    def cleanup_expired(self) -> int:
        """清理过期数据"""
        try:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM cache_entries WHERE expires_at < CURRENT_TIMESTAMP"
                )
                conn.commit()
                return cursor.rowcount
        except Exception as e:
            logger.error(f"L2Cache cleanup error: {e}")
            return 0


class L3Cache:
    """三级缓存 - 时序数据库 (TimescaleDB)"""

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url
        self.engine = None

        if db_url:
            try:
                from sqlalchemy import create_engine
                self.engine = create_engine(db_url)
                logger.info("L3Cache: TimescaleDB connected")
            except Exception as e:
                logger.warning(f"L3Cache: TimescaleDB connection failed: {e}")

    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self.engine is not None

    def get_time_series(
        self,
        ticker: str,
        metric: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Optional[pd.DataFrame]:
        """获取时序数据"""
        if not self.engine:
            return None

        try:
            query = """
                SELECT * FROM price_data
                WHERE ticker = %s AND time BETWEEN %s AND %s
                ORDER BY time
            """
            df = pd.read_sql(query, self.engine, params=(ticker, start_date, end_date))
            return df
        except Exception as e:
            logger.error(f"L3Cache get_time_series error: {e}")
            return None

    def store_time_series(self, ticker: str, data: pd.DataFrame) -> bool:
        """存储时序数据"""
        if not self.engine:
            return False

        try:
            data['ticker'] = ticker
            data.to_sql('price_data', self.engine, if_exists='append', index=False)
            return True
        except Exception as e:
            logger.error(f"L3Cache store_time_series error: {e}")
            return False


class VersionManager:
    """数据版本管理器"""

    def __init__(self, l2_cache: L2Cache):
        self.l2 = l2_cache

    def create_version(
        self,
        ticker: str,
        data_type: DataType,
        source: str,
        data_hash: str,
        description: str = "",
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        创建新版本
        版本号格式: YYYYMMDD-HHMMSS-{source}-{hash}
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_hash = data_hash[:6]
        version_id = f"{timestamp}-{source}-{short_hash}"

        try:
            with self.l2._get_connection() as conn:
                # 标记旧版本为非活跃
                conn.execute(
                    """UPDATE data_versions
                       SET is_active = 0
                       WHERE ticker = ? AND data_type = ?""",
                    (ticker, data_type.value)
                )

                # 插入新版本
                conn.execute(
                    """INSERT INTO data_versions
                       (version_id, ticker, data_type, source, description, metadata)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (version_id, ticker, data_type.value, source, description,
                     json.dumps(metadata or {}))
                )
                conn.commit()

            return version_id
        except Exception as e:
            logger.error(f"VersionManager create_version error: {e}")
            return ""

    def get_version(self, version_id: str) -> Optional[DataVersion]:
        """获取版本信息"""
        try:
            with self.l2._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM data_versions WHERE version_id = ?",
                    (version_id,)
                )
                row = cursor.fetchone()

                if row:
                    return DataVersion(
                        version_id=row[0],
                        ticker=row[1],
                        data_type=DataType(row[2]),
                        source=row[3],
                        created_at=datetime.fromisoformat(row[4]),
                        description=row[5] or "",
                        metadata=json.loads(row[6]) if row[6] else {},
                        is_active=bool(row[7]),
                        data_hash="",  # 从缓存条目获取
                    )
                return None
        except Exception as e:
            logger.error(f"VersionManager get_version error: {e}")
            return None

    def list_versions(self, ticker: str, data_type: DataType) -> List[DataVersion]:
        """列出历史版本"""
        try:
            with self.l2._get_connection() as conn:
                cursor = conn.execute(
                    """SELECT * FROM data_versions
                       WHERE ticker = ? AND data_type = ?
                       ORDER BY created_at DESC""",
                    (ticker, data_type.value)
                )
                versions = []
                for row in cursor.fetchall():
                    versions.append(DataVersion(
                        version_id=row[0],
                        ticker=row[1],
                        data_type=DataType(row[2]),
                        source=row[3],
                        created_at=datetime.fromisoformat(row[4]),
                        description=row[5] or "",
                        metadata=json.loads(row[6]) if row[6] else {},
                        is_active=bool(row[7]),
                        data_hash="",
                    ))
                return versions
        except Exception as e:
            logger.error(f"VersionManager list_versions error: {e}")
            return []


class DataQualityChecker:
    """数据质量检查器"""

    def check(self, data: pd.DataFrame, data_type: DataType) -> QualityReport:
        """执行数据质量检查"""
        if data_type == DataType.DAILY_PRICE:
            return self._check_price_data(data)
        elif data_type == DataType.FINANCIAL_STATEMENT:
            return self._check_financial_data(data)
        else:
            return QualityReport(passed=True)

    def _check_price_data(self, df: pd.DataFrame) -> QualityReport:
        """价格数据质量检查"""
        issues = []

        # 1. 缺失值检查
        if df.isnull().any().any():
            null_counts = df.isnull().sum()
            issues.append(QualityIssue(
                type="MISSING_VALUES",
                severity="WARNING",
                message=f"发现缺失值: {null_counts.to_dict()}",
            ))

        # 2. 价格跳空检查
        if 'close' in df.columns:
            df = df.copy()
            df['price_change'] = df['close'].pct_change()
            large_jumps = df[abs(df['price_change']) > 0.20]
            if len(large_jumps) > 0:
                issues.append(QualityIssue(
                    type="PRICE_JUMP",
                    severity="WARNING",
                    message=f"发现 {len(large_jumps)} 次超过20%的价格变动",
                    dates=large_jumps.index.tolist()[:5],
                ))

        # 3. 异常值检查
        for col in ['open', 'high', 'low', 'close']:
            if col in df.columns:
                outliers = self._detect_outliers_iqr(df[col])
                if len(outliers) > 0:
                    issues.append(QualityIssue(
                        type="OUTLIER",
                        severity="INFO",
                        message=f"{col} 列发现 {len(outliers)} 个异常值",
                    ))

        # 4. 日期重复检查
        if df.index.duplicated().sum() > 0:
            issues.append(QualityIssue(
                type="DUPLICATE_DATES",
                severity="ERROR",
                message=f"发现 {df.index.duplicated().sum()} 个重复日期",
            ))

        return QualityReport(
            passed=not any(i.severity == "ERROR" for i in issues),
            issues=issues,
        )

    def _check_financial_data(self, df: pd.DataFrame) -> QualityReport:
        """财务数据质量检查"""
        issues = []

        # 检查关键字段
        required_fields = ['revenue', 'net_income']
        for field in required_fields:
            if field not in df.columns:
                issues.append(QualityIssue(
                    type="MISSING_FIELD",
                    severity="ERROR",
                    message=f"缺少关键字段: {field}",
                ))

        return QualityReport(
            passed=len([i for i in issues if i.severity == "ERROR"]) == 0,
            issues=issues,
        )

    def _detect_outliers_iqr(self, series: pd.Series) -> List:
        """使用IQR方法检测异常值"""
        Q1 = series.quantile(0.25)
        Q3 = series.quantile(0.75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR
        return series[(series < lower_bound) | (series > upper_bound)].index.tolist()


class DataCacheV2:
    """
    数据缓存管理器 (主入口)
    三级缓存架构: L1(内存) -> L2(磁盘) -> L3(时序DB)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        sqlite_path: str = "cache/data_cache_v2.db",
        timescaledb_url: Optional[str] = None,
    ):
        self.l1_cache = L1Cache(redis_url)
        self.l2_cache = L2Cache(sqlite_path)
        self.l3_cache = L3Cache(timescaledb_url)
        self.version_manager = VersionManager(self.l2_cache)
        self.quality_checker = DataQualityChecker()
        self.metrics = CacheMetrics()

    def _generate_cache_key(
        self,
        data_type: DataType,
        ticker: str,
        **params
    ) -> str:
        """生成缓存键"""
        key = f"{data_type.value}:{ticker}"
        for param_name, param_value in sorted(params.items()):
            key += f":{param_name}_{param_value}"
        return key

    def get(
        self,
        key: str,
        data_type: DataType,
        refresh_policy: RefreshPolicy = RefreshPolicy.AUTO,
    ) -> Optional[CachedData]:
        """
        获取数据 (多级缓存查询)
        优先级: L1 -> L2 -> L3
        """
        if refresh_policy == RefreshPolicy.FORCE:
            return None

        self.metrics.total_requests += 1

        # L1: 内存缓存
        data = self.l1_cache.get(key)
        if data and not data.is_expired():
            self.metrics.l1_hits += 1
            data.touch()
            return data

        # L2: 本地磁盘
        data = self.l2_cache.get(key)
        if data and not data.is_expired():
            self.metrics.l2_hits += 1
            # 回填L1
            ttl = CACHE_TTL_CONFIG.get(data_type, {}).get("l1", 300)
            self.l1_cache.set(key, data, ttl)
            return data

        # L3: 时序数据库 (仅价格数据)
        if self.l3_cache.is_connected() and data_type in [DataType.DAILY_PRICE, DataType.INTRADAY_PRICE]:
            df = self.l3_cache.get_time_series(key)
            if df is not None and not df.empty:
                self.metrics.l3_hits += 1
                data = CachedData(key=key, data=df, data_type=data_type)
                # 回填L1, L2
                self._backfill_cache(key, data, data_type)
                return data

        self.metrics.misses += 1
        return None

    def set(
        self,
        key: str,
        data: Any,
        data_type: DataType,
        source: str = "",
        ttl: Optional[int] = None,
        version: Optional[str] = None,
    ) -> None:
        """设置缓存"""
        cached_data = CachedData(
            key=key,
            data=data,
            data_type=data_type,
            source=source,
            version_id=version,
        )

        config = CACHE_TTL_CONFIG.get(data_type, {})

        # L1
        l1_ttl = ttl or config.get("l1", 300)
        self.l1_cache.set(key, cached_data, l1_ttl)

        # L2
        l2_ttl = config.get("l2", 3600)
        self.l2_cache.set(key, cached_data, l2_ttl)

        # L3 (价格数据)
        if self.l3_cache.is_connected() and data_type in [DataType.DAILY_PRICE, DataType.INTRADAY_PRICE]:
            if isinstance(data, pd.DataFrame):
                ticker = key.split(":")[1] if ":" in key else key
                self.l3_cache.store_time_series(ticker, data)

    def _backfill_cache(self, key: str, data: CachedData, data_type: DataType) -> None:
        """回填缓存"""
        config = CACHE_TTL_CONFIG.get(data_type, {})
        self.l1_cache.set(key, data, config.get("l1", 300))
        self.l2_cache.set(key, data, config.get("l2", 3600))

    def invalidate(self, pattern: str) -> int:
        """使缓存失效"""
        # 简化实现: 清除所有L1缓存
        self.l1_cache.clear()
        return 1

    def refresh(self, key: str, data_type: DataType) -> Optional[CachedData]:
        """强制刷新数据"""
        self.invalidate(key)
        return self.get(key, data_type, RefreshPolicy.CACHE_ONLY)

    def cleanup_expired(self) -> int:
        """清理过期缓存"""
        return self.l2_cache.cleanup_expired()

    def get_metrics(self) -> Dict:
        """获取缓存指标"""
        return {
            "total_requests": self.metrics.total_requests,
            "l1_hit_rate": f"{self.metrics.l1_hit_rate:.2%}",
            "overall_hit_rate": f"{self.metrics.overall_hit_rate:.2%}",
            "l1_hits": self.metrics.l1_hits,
            "l2_hits": self.metrics.l2_hits,
            "l3_hits": self.metrics.l3_hits,
            "misses": self.metrics.misses,
        }


class IncrementalUpdater:
    """增量更新器"""

    def __init__(self, cache: DataCacheV2):
        self.cache = cache

    async def update_price_data(
        self,
        ticker: str,
        source: str = "akshare",
        fetch_func = None,
    ) -> UpdateResult:
        """
        增量更新价格数据
        """
        cache_key = self.cache._generate_cache_key(DataType.DAILY_PRICE, ticker)

        # 1. 查询本地最新日期
        cached = self.cache.get(cache_key, DataType.DAILY_PRICE)
        if cached and isinstance(cached.data, pd.DataFrame) and not cached.data.empty:
            latest_local = cached.data.index.max()
            start_date = latest_local + timedelta(days=1)
        else:
            start_date = datetime(2010, 1, 1)

        end_date = datetime.now()

        # 2. 如果数据已最新
        if start_date >= end_date:
            return UpdateResult(status="UP_TO_DATE", records_added=0)

        # 3. 从数据源获取增量数据
        if fetch_func is None:
            return UpdateResult(status="ERROR", errors=["No fetch function provided"])

        try:
            new_data = await fetch_func(ticker, start_date, end_date)

            if new_data is None or new_data.empty:
                return UpdateResult(status="UP_TO_DATE", records_added=0)

            # 4. 数据质量检查
            quality_report = self.cache.quality_checker.check(new_data, DataType.DAILY_PRICE)
            if quality_report.has_errors():
                return UpdateResult(
                    status="QUALITY_CHECK_FAILED",
                    errors=[i.message for i in quality_report.issues if i.severity == "ERROR"],
                )

            # 5. 合并并存储
            if cached and isinstance(cached.data, pd.DataFrame):
                merged_data = pd.concat([cached.data, new_data])
                merged_data = merged_data[~merged_data.index.duplicated(keep='last')]
            else:
                merged_data = new_data

            # 创建版本
            data_hash = hashlib.md5(pickle.dumps(merged_data)).hexdigest()
            version_id = self.cache.version_manager.create_version(
                ticker, DataType.DAILY_PRICE, source, data_hash
            )

            # 存储
            self.cache.set(
                key=cache_key,
                data=merged_data,
                data_type=DataType.DAILY_PRICE,
                source=source,
                version=version_id,
            )

            return UpdateResult(
                status="SUCCESS",
                records_added=len(new_data),
                date_range=(start_date, end_date),
            )

        except Exception as e:
            logger.error(f"Incremental update failed: {e}")
            return UpdateResult(status="ERROR", errors=[str(e)])


# 便捷函数
def get_cache_instance(
    redis_url: Optional[str] = None,
    sqlite_path: str = "cache/data_cache_v2.db",
    timescaledb_url: Optional[str] = None,
) -> DataCacheV2:
    """获取缓存实例 (单例模式)"""
    if not hasattr(get_cache_instance, "_instance"):
        get_cache_instance._instance = DataCacheV2(
            redis_url=redis_url,
            sqlite_path=sqlite_path,
            timescaledb_url=timescaledb_url,
        )
    return get_cache_instance._instance


# 测试代码
if __name__ == "__main__":
    # 创建测试缓存
    cache = DataCacheV2(sqlite_path="test_cache.db")

    # 测试数据
    test_df = pd.DataFrame({
        'open': [100, 101, 102],
        'close': [101, 102, 103],
        'volume': [1000, 2000, 3000],
    }, index=pd.date_range('2024-01-01', periods=3))

    # 设置缓存
    cache.set(
        key="daily_price:TEST",
        data=test_df,
        data_type=DataType.DAILY_PRICE,
        source="test",
    )

    # 获取缓存
    cached = cache.get("daily_price:TEST", DataType.DAILY_PRICE)
    if cached:
        print(f"Cached data:\n{cached.data}")
        print(f"\nCache metrics: {cache.get_metrics()}")
    else:
        print("Cache miss")
