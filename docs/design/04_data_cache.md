# 数据缓存层升级详细设计

> 优先级: Sprint 2 (P1)
> 模块路径: `modules/data_cache_v2.py` (升级替换现有 `data_cache.py`)

---

## 1. 功能概述

数据缓存层是系统的数据基础设施，负责统一管理数据获取、存储、版本控制和增量更新，解决当前直接调用API导致的性能问题和数据一致性挑战。

### 1.1 当前痛点
- 每次查询都直接调用API，响应慢
- 没有数据版本管理，财报修正后历史数据不一致
- 缺乏增量更新机制，重复获取大量数据
- 数据质量问题难以及时发现

### 1.2 升级目标
- **高性能**: 本地缓存 + 增量更新，响应时间 < 100ms
- **一致性**: 数据版本管理，支持历史回溯
- **可靠性**: 数据质量监控，异常自动检测
- **可扩展**: 支持多数据源统一接口

---

## 2. 架构设计

### 2.1 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                      应用层 (App Layer)                          │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │
│  │ DCF模型 │  │ Comps   │  │ 回测    │  │ 其他模块            │ │
│  └────┬────┘  └────┬────┘  └────┬────┘  └──────────┬──────────┘ │
└───────┼────────────┼────────────┼──────────────────┼────────────┘
        │            │            │                  │
        └────────────┴────────────┴──────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    缓存管理层 (Cache Manager)                     │
│  ┌─────────────────┐  ┌───────────────┐  ┌───────────────────┐  │
│  │ DataCache       │  │ CachePolicy   │  │ CacheInvalidation │  │
│  │ - get()         │  │ - TTL设置     │  │ - 定时清理        │  │
│  │ - set()         │  │ - 优先级      │  │ - 事件触发        │  │
│  │ - invalidate()  │  │ - 存储策略    │  │ - 手动刷新        │  │
│  └─────────────────┘  └───────────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
           ▼               ▼               ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│  内存缓存 (L1)   │ │ 本地磁盘(L2)│ │  时序数据库(L3) │
│  Redis/Memcached │ │  SQLite     │ │ TimescaleDB     │
│  - 热点数据      │ │  - 完整数据 │ │  - 历史数据     │
│  - 秒级TTL       │ │  - 小时级TTL│ │  - 永久存储     │
└─────────────────┘ └─────────────┘ └─────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    数据源层 (Data Sources)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ akshare  │  │ yfinance │  │ tushare  │  │ 其他MCP服务器    │ │
│  │ (A股)    │  │ (美股)   │  │ (增强)   │  │                  │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 类图

```python
class DataCacheV2:
    """数据缓存管理器 (主入口)"""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        sqlite_path: str = "cache/data_cache_v2.db",
        timescaledb_url: Optional[str] = None,
    ):
        self.l1_cache = L1Cache(redis_url)           # 内存缓存
        self.l2_cache = L2Cache(sqlite_path)         # 本地磁盘
        self.l3_cache = L3Cache(timescaledb_url)     # 时序数据库
        self.version_manager = VersionManager()
        self.quality_checker = DataQualityChecker()

    async def get(
        self,
        key: str,
        data_type: DataType,
        refresh_policy: RefreshPolicy = RefreshPolicy.AUTO,
    ) -> CachedData:
        """
        获取数据 (多级缓存查询)

        策略:
        1. 查询L1内存缓存
        2. 未命中查询L2本地磁盘
        3. 未命中查询L3时序数据库
        4. 均未命中从数据源获取
        """
        pass

    async def set(
        self,
        key: str,
        data: Any,
        data_type: DataType,
        ttl: Optional[int] = None,
        version: Optional[str] = None,
    ) -> None:
        """设置缓存"""
        pass

    async def invalidate(self, pattern: str) -> int:
        """使缓存失效"""
        pass

    async def refresh(self, key: str) -> CachedData:
        """强制刷新数据"""
        pass


class L1Cache:
    """一级缓存 - 内存 (Redis)"""

    def __init__(self, redis_url: Optional[str] = None):
        self.redis = redis.Redis.from_url(redis_url) if redis_url else None
        self.local_cache = {}  # 本地dict作为fallback

    async def get(self, key: str) -> Optional[CachedData]:
        pass

    async def set(self, key: str, data: CachedData, ttl: int = 300) -> None:
        """默认TTL 5分钟"""
        pass


class L2Cache:
    """二级缓存 - 本地磁盘 (SQLite)"""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self._init_tables()

    def _init_tables(self):
        """初始化表结构"""
        pass

    def get(self, key: str) -> Optional[CachedData]:
        pass

    def set(self, key: str, data: CachedData, ttl: int = 3600) -> None:
        """默认TTL 1小时"""
        pass

    def cleanup_expired(self) -> int:
        """清理过期数据"""
        pass


class L3Cache:
    """三级缓存 - 时序数据库 (TimescaleDB)"""

    def __init__(self, db_url: Optional[str] = None):
        self.engine = create_engine(db_url) if db_url else None

    def get_time_series(
        self,
        ticker: str,
        metric: str,
        start_date: datetime,
        end_date: datetime,
    ) -> pd.DataFrame:
        """获取时序数据"""
        pass

    def store_time_series(
        self,
        ticker: str,
        metric: str,
        data: pd.DataFrame,
    ) -> None:
        """存储时序数据"""
        pass


class VersionManager:
    """数据版本管理器"""

    def create_version(
        self,
        data_type: DataType,
        source: str,
        metadata: Dict,
    ) -> str:
        """
        创建新版本

        版本号格式: YYYYMMDD-HHMMSS-{source}-{hash}
        示例: 20260305-143022-akshare-a1b2c3
        """
        pass

    def get_version(self, version_id: str) -> DataVersion:
        """获取版本信息"""
        pass

    def list_versions(
        self,
        ticker: str,
        data_type: DataType,
    ) -> List[DataVersion]:
        """列出历史版本"""
        pass

    def rollback(self, ticker: str, version_id: str) -> bool:
        """回滚到指定版本"""
        pass


class DataQualityChecker:
    """数据质量检查器"""

    def check(self, data: pd.DataFrame, checks: List[QualityCheck]) -> QualityReport:
        """执行数据质量检查"""
        pass

    def detect_outliers(
        self,
        data: pd.Series,
        method: str = "iqr",
    ) -> List[int]:
        """异常值检测"""
        pass

    def check_continuity(
        self,
        data: pd.DataFrame,
        date_col: str = "date",
    ) -> ContinuityReport:
        """检查数据连续性"""
        pass


class IncrementalUpdater:
    """增量更新器"""

    def __init__(self, cache: DataCacheV2):
        self.cache = cache

    async def update_price_data(
        self,
        ticker: str,
        source: str,
    ) -> UpdateResult:
        """
        增量更新价格数据

        策略:
        1. 查询本地最新日期
        2. 从数据源获取增量数据
        3. 合并并存储
        """
        pass

    async def update_financial_data(
        self,
        ticker: str,
    ) -> UpdateResult:
        """增量更新财务数据"""
        pass
```

---

## 3. 数据库设计

### 3.1 SQLite 表结构 (L2缓存)

```sql
-- 缓存主表
CREATE TABLE cache_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT UNIQUE NOT NULL,      -- 缓存键: "daily_price:600519.SS"
    data_type TEXT NOT NULL,              -- 数据类型
    data BLOB NOT NULL,                   -- 序列化数据 (pickle/parquet)
    data_hash TEXT,                       -- 数据哈希 (用于去重)
    version_id TEXT,                      -- 版本ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,                 -- 过期时间
    access_count INTEGER DEFAULT 0,       -- 访问次数
    last_accessed TIMESTAMP,              -- 最后访问时间
    source TEXT,                          -- 数据来源
    metadata JSON                         -- 额外元数据
);

CREATE INDEX idx_cache_key ON cache_entries(cache_key);
CREATE INDEX idx_expires ON cache_entries(expires_at);
CREATE INDEX idx_data_type ON cache_entries(data_type);

-- 版本管理表
CREATE TABLE data_versions (
    version_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    data_type TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    description TEXT,
    metadata JSON,
    is_active BOOLEAN DEFAULT 1
);

CREATE INDEX idx_versions_ticker ON data_versions(ticker, data_type);

-- 数据质量日志
CREATE TABLE quality_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key TEXT NOT NULL,
    check_type TEXT NOT NULL,
    status TEXT NOT NULL,                 -- PASS / WARNING / ERROR
    message TEXT,
    details JSON,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 TimescaleDB 表结构 (L3时序缓存)

```sql
-- 价格数据 hypertable
CREATE TABLE price_data (
    time TIMESTAMPTZ NOT NULL,
    ticker TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume BIGINT,
    adj_close DOUBLE PRECISION,
    source TEXT,
    version_id TEXT
);

-- 创建 hypertable (按时间分区)
SELECT create_hypertable('price_data', 'time');

-- 创建索引
CREATE INDEX idx_price_ticker ON price_data(ticker, time DESC);

-- 财务指标 hypertable
CREATE TABLE financial_metrics (
    time TIMESTAMPTZ NOT NULL,
    ticker TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION,
    period_type TEXT,                     -- annual / quarterly
    report_date DATE,
    source TEXT,
    version_id TEXT
);

SELECT create_hypertable('financial_metrics', 'time');
CREATE INDEX idx_fin_metric ON financial_metrics(ticker, metric_name, time DESC);
```

---

## 4. 核心算法

### 4.1 多级缓存查询

```python
async def get_with_tiered_cache(
    self,
    cache_key: str,
    data_type: DataType,
) -> Optional[CachedData]:
    """
    多级缓存查询

    优先级: L1 (内存) -> L2 (磁盘) -> L3 (时序DB) -> Source
    """
    # L1: 内存缓存 (最快, TTL短)
    if self.l1_cache:
        data = await self.l1_cache.get(cache_key)
        if data and not data.is_expired():
            return data

    # L2: 本地磁盘 (较快, TTL中等)
    data = self.l2_cache.get(cache_key)
    if data and not data.is_expired():
        # 回填L1
        await self.l1_cache.set(cache_key, data)
        return data

    # L3: 时序数据库 (较慢, 永久存储)
    if self.l3_cache and data_type in TIME_SERIES_TYPES:
        data = self.l3_cache.get(cache_key)
        if data:
            # 回填L1, L2
            await self.l1_cache.set(cache_key, data)
            self.l2_cache.set(cache_key, data)
            return data

    return None
```

### 4.2 增量更新

```python
async def incremental_update_prices(
    self,
    ticker: str,
    source: str = "akshare",
) -> UpdateResult:
    """
    增量更新价格数据
    """
    cache_key = f"daily_price:{ticker}"

    # 1. 查询本地最新日期
    latest_local = await self.get_latest_date(cache_key)

    # 2. 计算需要获取的日期范围
    if latest_local:
        start_date = latest_local + timedelta(days=1)
    else:
        start_date = datetime(2010, 1, 1)  # 默认起始日期

    end_date = datetime.now()

    # 3. 如果数据已最新,直接返回
    if start_date >= end_date:
        return UpdateResult(
            status="UP_TO_DATE",
            records_added=0,
        )

    # 4. 从数据源获取增量数据
    new_data = await self.fetch_from_source(
        ticker=ticker,
        data_type=DataType.DAILY_PRICE,
        start_date=start_date,
        end_date=end_date,
        source=source,
    )

    # 5. 数据质量检查
    quality_report = self.quality_checker.check(new_data)
    if quality_report.has_errors():
        return UpdateResult(
            status="QUALITY_CHECK_FAILED",
            errors=quality_report.errors,
        )

    # 6. 合并并存储
    await self.merge_and_store(cache_key, new_data)

    return UpdateResult(
        status="SUCCESS",
        records_added=len(new_data),
        date_range=(start_date, end_date),
    )
```

### 4.3 数据质量检查

```python
def check_price_data_quality(self, df: pd.DataFrame) -> QualityReport:
    """
    价格数据质量检查

    检查项:
    1. 缺失值检查
    2. 价格跳空检查 (除权除息未调整)
    3. 异常值检查 (涨跌幅超过20%)
    4. 连续性检查 (是否有断档)
    5. 日期重复检查
    """
    issues = []

    # 1. 缺失值检查
    if df.isnull().any().any():
        null_counts = df.isnull().sum()
        issues.append(QualityIssue(
            type="MISSING_VALUES",
            severity="WARNING",
            details=null_counts.to_dict(),
        ))

    # 2. 价格跳空检查
    df['price_change'] = df['close'].pct_change()
    large_jumps = df[abs(df['price_change']) > 0.20]
    if len(large_jumps) > 0:
        issues.append(QualityIssue(
            type="PRICE_JUMP",
            severity="WARNING",
            message=f"发现 {len(large_jumps)} 次超过20%的价格变动，可能是除权除息未调整",
            dates=large_jumps.index.tolist(),
        ))

    # 3. 异常值检查 (IQR方法)
    for col in ['open', 'high', 'low', 'close']:
        outliers = self.detect_outliers_iqr(df[col])
        if outliers:
            issues.append(QualityIssue(
                type="OUTLIER",
                severity="INFO",
                message=f"{col} 列发现 {len(outliers)} 个异常值",
            ))

    # 4. 连续性检查
    expected_dates = pd.date_range(start=df.index.min(), end=df.index.max(), freq='B')
    missing_dates = expected_dates.difference(df.index)
    if len(missing_dates) > 0:
        issues.append(QualityIssue(
            type="MISSING_DATES",
            severity="INFO",
            message=f"缺少 {len(missing_dates)} 个交易日的数据",
            dates=missing_dates.tolist()[:10],  # 只显示前10个
        ))

    # 5. 日期重复检查
    duplicates = df.index.duplicated().sum()
    if duplicates > 0:
        issues.append(QualityIssue(
            type="DUPLICATE_DATES",
            severity="ERROR",
            message=f"发现 {duplicates} 个重复日期",
        ))

    return QualityReport(
        passed=len([i for i in issues if i.severity == "ERROR"]) == 0,
        issues=issues,
    )
```

### 4.4 版本管理

```python
def create_version(
    self,
    ticker: str,
    data_type: DataType,
    source: str,
    data_hash: str,
) -> str:
    """
    创建新版本
    """
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    short_hash = data_hash[:6]
    version_id = f"{timestamp}-{source}-{short_hash}"

    version = DataVersion(
        version_id=version_id,
        ticker=ticker,
        data_type=data_type,
        source=source,
        created_at=datetime.now(),
        data_hash=data_hash,
    )

    # 存储版本信息
    self._store_version(version)

    # 标记旧版本为非活跃
    self._deactivate_old_versions(ticker, data_type, version_id)

    return version_id
```

---

## 5. 缓存策略

### 5.1 TTL策略

```python
CACHE_TTL_CONFIG = {
    # 数据类型 -> TTL (秒)
    DataType.DAILY_PRICE: {
        "l1": 300,      # 5分钟
        "l2": 3600,     # 1小时
        "l3": None,     # 永久
    },
    DataType.INTRADAY_PRICE: {
        "l1": 60,       # 1分钟
        "l2": 300,      # 5分钟
        "l3": 86400,    # 1天
    },
    DataType.FINANCIAL_STATEMENT: {
        "l1": 86400,    # 1天 (财报不常更新)
        "l2": 604800,   # 1周
        "l3": None,
    },
    DataType.COMPANY_INFO: {
        "l1": 3600,
        "l2": 86400,
        "l3": None,
    },
    DataType.NEWS: {
        "l1": 60,
        "l2": 600,
        "l3": 2592000,  # 30天
    },
}
```

### 5.2 缓存键命名规范

```python
def generate_cache_key(
    data_type: DataType,
    ticker: str,
    **params
) -> str:
    """
    生成缓存键

    格式: {data_type}:{ticker}:{param1}_{value1}:{param2}_{value2}

    示例:
    - daily_price:600519.SS
    - daily_price:600519.SS:start_20240101:end_20240301
    - financial:600519.SS:period_annual:year_2023
    - news:600519.SS:limit_50
    """
    key = f"{data_type.value}:{ticker}"

    for param_name, param_value in sorted(params.items()):
        key += f":{param_name}_{param_value}"

    return key
```

---

## 6. 接口定义

### 6.1 Python API

```python
from modules.data_cache_v2 import DataCacheV2, DataType, RefreshPolicy

# 初始化缓存
cache = DataCacheV2(
    redis_url="redis://localhost:6379",
    sqlite_path="cache/data_cache.db",
    timescaledb_url="postgresql://user:pass@localhost:5432/finance",
)

# 获取数据 (自动处理多级缓存)
async def get_stock_price(ticker: str) -> pd.DataFrame:
    cache_key = f"daily_price:{ticker}"

    # 尝试从缓存获取
    cached = await cache.get(cache_key, DataType.DAILY_PRICE)
    if cached:
        return cached.data

    # 缓存未命中,从数据源获取
    data = await fetch_from_akshare(ticker)

    # 存储到缓存
    await cache.set(
        key=cache_key,
        data=data,
        data_type=DataType.DAILY_PRICE,
        ttl=CACHE_TTL_CONFIG[DataType.DAILY_PRICE]["l2"],
    )

    return data

# 增量更新
result = await cache.incremental_update_prices("600519.SS")
print(f"新增 {result.records_added} 条记录")

# 强制刷新
data = await cache.refresh("daily_price:600519.SS")

# 版本回滚
await cache.version_manager.rollback("600519.SS", "20260301-120000-akshare-a1b2c3")

# 数据质量检查
report = cache.quality_checker.check(data)
for issue in report.issues:
    print(f"[{issue.severity}] {issue.type}: {issue.message}")
```

### 6.2 后台任务

```python
# 定时任务配置 (使用 APScheduler)
SCHEDULED_JOBS = [
    {
        "id": "incremental_price_update",
        "func": "update_all_price_data",
        "trigger": "cron",
        "hour": 16,  # 每天收盘后
        "minute": 30,
    },
    {
        "id": "cleanup_expired_cache",
        "func": "cleanup_expired",
        "trigger": "cron",
        "hour": 2,   # 每天凌晨
    },
    {
        "id": "quality_check",
        "func": "run_quality_checks",
        "trigger": "cron",
        "hour": 3,
    },
]

async def update_all_price_data():
    """更新所有股票的价格数据"""
    tickers = get_watchlist_tickers()

    for ticker in tickers:
        try:
            result = await cache.incremental_update_prices(ticker)
            logger.info(f"{ticker}: {result.status}")
        except Exception as e:
            logger.error(f"{ticker} 更新失败: {e}")
```

---

## 7. 监控与运维

### 7.1 缓存命中率监控

```python
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
```

### 7.2 健康检查

```python
async def health_check() -> HealthStatus:
    """
    系统健康检查
    """
    checks = {
        "l1_cache": await cache.l1_cache.ping(),
        "l2_cache": cache.l2_cache.is_connected(),
        "l3_cache": cache.l3_cache.is_connected() if cache.l3_cache else None,
        "disk_space": check_disk_space(),
        "hit_rate": cache.metrics.overall_hit_rate > 0.8,
    }

    return HealthStatus(
        healthy=all(c for c in checks.values() if c is not None),
        checks=checks,
    )
```

---

## 8. 部署配置

### 8.1 配置文件

```yaml
# config/cache.yaml
cache:
  l1:
    enabled: true
    type: redis
    url: ${REDIS_URL:-redis://localhost:6379}
    default_ttl: 300

  l2:
    enabled: true
    type: sqlite
    path: cache/data_cache.db
    default_ttl: 3600
    max_size_mb: 1024

  l3:
    enabled: true
    type: timescaledb
    url: ${TIMESCALEDB_URL}
    default_ttl: null  # 永久

  quality_check:
    enabled: true
    on_write: true     # 写入时检查
    on_read: false     # 读取时不检查

  incremental_update:
    enabled: true
    auto_update: true
    update_schedule: "0 30 16 * * *"  # 每天16:30
```

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
