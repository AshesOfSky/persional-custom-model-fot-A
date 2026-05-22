# MCP 连接器架构详细设计

> 优先级: Sprint 2 (P1)
> 配置文件: `.mcp.json`
> 模块路径: `connectors/` (新增目录)

---

## 1. 功能概述

MCP (Model Context Protocol) 是 Anthropic 推出的标准化协议，用于连接 AI 助手与外部数据源。本设计将现有数据获取逻辑重构为标准 MCP 架构，实现数据源的即插即用。

### 1.1 核心目标
- 标准化数据接口（统一 akshare / yfinance / tushare 调用方式）
- 数据源解耦（支持动态添加/切换数据源）
- 提升可维护性（统一错误处理、重试、监控）
- 对接生态（兼容外部 MCP 服务器）

### 1.2 当前 vs 目标架构

| 方面 | 当前架构 | 目标 MCP 架构 |
|------|----------|---------------|
| 数据源调用 | 硬编码在 data_fetcher_*.py | 标准化 MCP 客户端调用 |
| 新增数据源 | 修改代码 | 修改配置文件 |
| 错误处理 | 各模块独立 | 统一 MCP 错误处理 |
| 监控 | 分散 | 统一的 MCP 连接监控 |

---

## 2. 架构设计

### 2.1 MCP 架构概览

```
┌─────────────────────────────────────────────────────────────────┐
│                      应用层 (Application)                        │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐ │
│  │ DCF模型 │  │ Comps   │  │ 回测    │  │ 其他模块            │ │
│  └────┬────┘  └────┬────┘  └────┬────┘  └──────────┬──────────┘ │
└───────┼────────────┼────────────┼──────────────────┼────────────┘
        │            │            │                  │
        └────────────┴────────────┴──────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                 MCP 客户端层 (MCP Client Layer)                   │
│  ┌─────────────────┐  ┌───────────────┐  ┌───────────────────┐  │
│  │ MCPClient       │  │ DataRouter    │  │ ConnectionPool    │  │
│  │ - call()        │  │ - route()     │  │ - health_check()  │  │
│  │ - discover()    │  │ - fallback()  │  │ - retry()         │  │
│  └─────────────────┘  └───────────────┘  └───────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
           ▼               ▼               ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│  MCP服务器 1     │ │ MCP服务器 2 │ │ MCP服务器 3     │
│  (akshare-bridge)│ │ (yfinance)  │ │ (tushare)       │
└─────────────────┘ └─────────────┘ └─────────────────┘
         │                 │                 │
         ▼                 ▼                 ▼
┌─────────────────┐ ┌─────────────┐ ┌─────────────────┐
│  akshare SDK    │ │ yfinance    │ │ tushare SDK     │
│  (A股数据)      │ │ (美股数据)  │ │ (增强数据)      │
└─────────────────┘ └─────────────┘ └─────────────────┘
```

### 2.2 核心组件

```python
class MCPClient:
    """MCP 客户端主类"""

    def __init__(self, config_path: str = ".mcp.json"):
        self.config = self._load_config(config_path)
        self.servers: Dict[str, MCPServer] = {}
        self.router = DataRouter()
        self._init_servers()

    def _load_config(self, path: str) -> MCPConfig:
        """加载 MCP 配置文件"""
        with open(path) as f:
            return MCPConfig(**json.load(f))

    def _init_servers(self):
        """初始化所有 MCP 服务器连接"""
        for name, server_config in self.config.mcpServers.items():
            self.servers[name] = MCPServer(name, server_config)

    async def call(
        self,
        tool: str,
        params: Dict,
        preferred_source: Optional[str] = None,
    ) -> MCPResponse:
        """
        调用 MCP 工具

        流程:
        1. 路由到合适的数据源
        2. 执行调用
        3. 失败时自动 fallback
        """
        # 确定数据源
        if preferred_source and preferred_source in self.servers:
            sources = [preferred_source]
        else:
            sources = self.router.get_candidate_sources(tool, params)

        # 尝试各数据源
        for source in sources:
            try:
                server = self.servers[source]
                result = await server.call(tool, params)
                return MCPResponse(
                    success=True,
                    data=result,
                    source=source,
                )
            except Exception as e:
                logger.warning(f"{source} 调用失败: {e}")
                continue

        return MCPResponse(
            success=False,
            error="All data sources failed",
        )

    async def health_check(self) -> Dict[str, ServerHealth]:
        """检查所有服务器健康状态"""
        health = {}
        for name, server in self.servers.items():
            health[name] = await server.health_check()
        return health


class MCPServer:
    """MCP 服务器连接"""

    def __init__(self, name: str, config: ServerConfig):
        self.name = name
        self.config = config
        self.session = None
        self.tools: List[str] = []

    async def connect(self):
        """建立连接"""
        if self.config.transport == "stdio":
            self.session = await self._connect_stdio()
        elif self.config.transport == "sse":
            self.session = await self._connect_sse()

        # 获取可用工具列表
        self.tools = await self._discover_tools()

    async def call(self, tool: str, params: Dict) -> Any:
        """调用工具"""
        if not self.session:
            await self.connect()

        if tool not in self.tools:
            raise UnsupportedToolError(f"{self.name} 不支持 {tool}")

        return await self.session.call(tool, params)

    async def health_check(self) -> ServerHealth:
        """健康检查"""
        try:
            start = time.time()
            await self._ping()
            latency = time.time() - start
            return ServerHealth(
                status="healthy",
                latency_ms=latency * 1000,
                last_check=datetime.now(),
            )
        except Exception as e:
            return ServerHealth(
                status="unhealthy",
                error=str(e),
                last_check=datetime.now(),
            )


class DataRouter:
    """数据路由器 - 决定从哪个数据源获取数据"""

    ROUTING_TABLE = {
        "get_daily_price": {
            "A股": ["akshare", "tushare"],
            "美股": ["yfinance", "akshare"],
            "港股": ["akshare", "yfinance"],
        },
        "get_financial_statement": {
            "A股": ["akshare", "tushare"],
            "美股": ["yfinance"],
        },
        "get_company_info": {
            "A股": ["akshare"],
            "美股": ["yfinance"],
        },
        "get_real_time_quote": {
            "A股": ["akshare"],
            "美股": ["yfinance"],
        },
    }

    def get_candidate_sources(self, tool: str, params: Dict) -> List[str]:
        """获取候选数据源列表"""
        ticker = params.get("ticker", "")
        market = self._detect_market(ticker)

        routing = self.ROUTING_TABLE.get(tool, {})
        return routing.get(market, ["akshare"])

    def _detect_market(self, ticker: str) -> str:
        """检测市场类型"""
        if ticker.endswith(".SS") or ticker.endswith(".SZ") or ticker.isdigit():
            return "A股"
        elif ticker.endswith(".HK"):
            return "港股"
        else:
            return "美股"
```

---

## 3. 配置文件

### 3.1 `.mcp.json` 配置

```json
{
  "version": "1.0",
  "mcpServers": {
    "akshare": {
      "command": "python",
      "args": ["-m", "mcp_akshare_server"],
      "env": {
        "AKSHARE_TIMEOUT": "30"
      },
      "transport": "stdio",
      "capabilities": {
        "tools": [
          "get_daily_price",
          "get_intraday_price",
          "get_financial_statement",
          "get_company_info",
          "get_industry_list",
          "get_macro_data"
        ]
      },
      "priority": 1
    },
    "yfinance": {
      "command": "python",
      "args": ["-m", "mcp_yfinance_server"],
      "env": {
        "YF_TIMEOUT": "30"
      },
      "transport": "stdio",
      "capabilities": {
        "tools": [
          "get_daily_price",
          "get_financial_statement",
          "get_company_info",
          "get_options_chain"
        ]
      },
      "priority": 2
    },
    "tushare": {
      "command": "python",
      "args": ["-m", "mcp_tushare_server"],
      "env": {
        "TUSHARE_TOKEN": "${TUSHARE_TOKEN}"
      },
      "transport": "stdio",
      "capabilities": {
        "tools": [
          "get_daily_price",
          "get_financial_statement",
          "get_profit_forecast",
          "get_institutional_holdings"
        ]
      },
      "priority": 3
    }
  },
  "routing": {
    "default_source": "akshare",
    "fallback_enabled": true,
    "timeout_seconds": 30
  }
}
```

### 3.2 MCP 服务器实现示例

```python
# connectors/mcp_akshare_server.py

"""
Akshare MCP 服务器
将 akshare SDK 封装为标准 MCP 接口
"""

import asyncio
import json
import akshare as ak
from typing import Any, Dict, List


class AkshareMCPServer:
    """Akshare MCP 服务器"""

    def __init__(self):
        self.tools = {
            "get_daily_price": self.get_daily_price,
            "get_financial_statement": self.get_financial_statement,
            "get_company_info": self.get_company_info,
            "get_industry_list": self.get_industry_list,
        }

    async def handle_request(self, request: Dict) -> Dict:
        """处理 MCP 请求"""
        tool = request.get("tool")
        params = request.get("params", {})

        if tool not in self.tools:
            return {
                "error": f"Unknown tool: {tool}",
                "available_tools": list(self.tools.keys()),
            }

        try:
            result = await self.tools[tool](**params)
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_daily_price(
        self,
        ticker: str,
        start_date: str = None,
        end_date: str = None,
        adjust: str = "qfq",  # 前复权
    ) -> List[Dict]:
        """获取日线数据"""
        # 转换 ticker 格式
        symbol = self._normalize_symbol(ticker)

        # 调用 akshare
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )

        # 转换为标准格式
        return df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }).to_dict("records")

    async def get_financial_statement(
        self,
        ticker: str,
        statement_type: str = "income",  # income/balance/cashflow
        period: str = "annual",  # annual/quarterly
    ) -> Dict:
        """获取财务报表"""
        symbol = self._normalize_symbol(ticker)

        if statement_type == "income":
            df = ak.stock_financial_report_sina(
                stock=symbol,
                symbol="利润表"
            )
        elif statement_type == "balance":
            df = ak.stock_financial_report_sina(
                stock=symbol,
                symbol="资产负债表"
            )
        else:
            df = ak.stock_financial_report_sina(
                stock=symbol,
                symbol="现金流量表"
            )

        return {
            "statement_type": statement_type,
            "data": df.to_dict("records"),
        }

    async def get_company_info(self, ticker: str) -> Dict:
        """获取公司信息"""
        symbol = self._normalize_symbol(ticker)

        # 获取基本信息
        info = ak.stock_individual_info_em(symbol=symbol)

        return {
            "ticker": ticker,
            "name": info.get("股票简称"),
            "industry": info.get("行业"),
            "market_cap": info.get("总市值"),
            "pe_ttm": info.get("市盈率"),
            "pb": info.get("市净率"),
        }

    def _normalize_symbol(self, ticker: str) -> str:
        """标准化股票代码"""
        # 600519.SS -> 600519
        # 000001.SZ -> 000001
        return ticker.split(".")[0]

    async def run(self):
        """运行服务器 (stdio 模式)"""
        while True:
            try:
                # 从 stdin 读取请求
                line = await asyncio.get_event_loop().run_in_executor(
                    None, input
                )
                request = json.loads(line)

                # 处理请求
                response = await self.handle_request(request)

                # 输出响应
                print(json.dumps(response), flush=True)

            except json.JSONDecodeError:
                print(json.dumps({"error": "Invalid JSON"}), flush=True)
            except Exception as e:
                print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    server = AkshareMCPServer()
    asyncio.run(server.run())
```

---

## 4. 数据标准化

### 4.1 统一数据格式

```python
# 标准化输出格式

class StandardDataFormat:
    """标准数据格式定义"""

    # 价格数据
    PRICE_SCHEMA = {
        "date": "datetime",       # 日期
        "open": "float",          # 开盘价
        "high": "float",          # 最高价
        "low": "float",           # 最低价
        "close": "float",         # 收盘价
        "volume": "int",          # 成交量
        "amount": "float",        # 成交额
        "adj_close": "float",     # 复权收盘价
    }

    # 财务数据
    FINANCIAL_SCHEMA = {
        "period": "str",          # 报告期
        "revenue": "float",       # 营业收入
        "cogs": "float",          # 营业成本
        "gross_profit": "float",  # 毛利润
        "operating_expenses": "float",  # 运营费用
        "ebitda": "float",        # EBITDA
        "ebit": "float",          # EBIT
        "net_income": "float",    # 净利润
        "eps": "float",           # 每股收益
    }

    # 公司信息
    COMPANY_INFO_SCHEMA = {
        "ticker": "str",
        "name": "str",
        "exchange": "str",
        "industry": "str",
        "sector": "str",
        "market_cap": "float",
        "employees": "int",
        "website": "str",
    }


class DataNormalizer:
    """数据标准化器"""

    def normalize_price_data(
        self,
        data: pd.DataFrame,
        source: str,
    ) -> pd.DataFrame:
        """标准化价格数据"""
        column_mapping = self._get_price_mapping(source)

        # 重命名列
        df = data.rename(columns=column_mapping)

        # 确保所有标准列存在
        for col in StandardDataFormat.PRICE_SCHEMA.keys():
            if col not in df.columns:
                df[col] = None

        # 类型转换
        df["date"] = pd.to_datetime(df["date"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def _get_price_mapping(self, source: str) -> Dict[str, str]:
        """获取列名映射"""
        mappings = {
            "akshare": {
                "日期": "date",
                "开盘": "open",
                "收盘": "close",
                "最高": "high",
                "最低": "low",
                "成交量": "volume",
                "成交额": "amount",
            },
            "yfinance": {
                "Date": "date",
                "Open": "open",
                "Close": "close",
                "High": "high",
                "Low": "low",
                "Volume": "volume",
            },
        }
        return mappings.get(source, {})
```

---

## 5. 迁移策略

### 5.1 逐步迁移计划

```python
# 兼容性包装器 - 允许逐步迁移

class DataFetcherCompat:
    """
    数据获取兼容层

    在迁移期间保持现有代码可用，
    内部使用新的 MCP 客户端
    """

    def __init__(self):
        self.mcp_client = MCPClient()
        self.legacy_fetcher = None  # 旧的获取器，备用

    async def get_daily_price(self, ticker: str, **kwargs) -> pd.DataFrame:
        """
        获取日线数据 (兼容旧接口)
        """
        try:
            # 尝试使用 MCP
            response = await self.mcp_client.call(
                tool="get_daily_price",
                params={"ticker": ticker, **kwargs},
            )

            if response.success:
                return pd.DataFrame(response.data)

        except Exception as e:
            logger.warning(f"MCP 调用失败，使用备用: {e}")

        # 备用：使用旧的获取器
        if self.legacy_fetcher:
            return await self.legacy_fetcher.get_daily_price(ticker, **kwargs)

        raise DataFetchError("All data sources failed")

    async def get_financial_statement(self, ticker: str, **kwargs) -> pd.DataFrame:
        """获取财务报表 (兼容旧接口)"""
        response = await self.mcp_client.call(
            tool="get_financial_statement",
            params={"ticker": ticker, **kwargs},
        )
        return pd.DataFrame(response.data) if response.success else None
```

### 5.2 迁移检查清单

| 阶段 | 任务 | 状态 |
|------|------|------|
| 1 | 创建 MCP 服务器实现 | 待完成 |
| 2 | 创建 MCP 客户端 | 待完成 |
| 3 | 添加兼容性包装器 | 待完成 |
| 4 | 测试所有数据源 | 待完成 |
| 5 | 更新现有模块调用 | 待完成 |
| 6 | 移除旧代码 | 待完成 |

---

## 6. 接口定义

### 6.1 Python API

```python
from connectors.mcp_client import MCPClient

# 初始化 MCP 客户端
client = MCPClient(".mcp.json")

# 获取日线数据 (自动路由到最佳数据源)
response = await client.call(
    tool="get_daily_price",
    params={
        "ticker": "600519.SS",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    },
)

if response.success:
    df = pd.DataFrame(response.data)
    print(f"从 {response.source} 获取数据")
else:
    print(f"错误: {response.error}")

# 指定数据源
response = await client.call(
    tool="get_daily_price",
    params={"ticker": "AAPL"},
    preferred_source="yfinance",
)

# 健康检查
health = await client.health_check()
for source, status in health.items():
    print(f"{source}: {status.status} ({status.latency_ms:.0f}ms)")
```

### 6.2 工具列表

```python
# MCP 工具定义

MCP_TOOLS = {
    "get_daily_price": {
        "description": "获取股票日线数据",
        "parameters": {
            "ticker": {"type": "string", "required": True},
            "start_date": {"type": "string", "format": "YYYY-MM-DD"},
            "end_date": {"type": "string", "format": "YYYY-MM-DD"},
            "adjust": {"type": "string", "enum": ["qfq", "hfq", "none"], "default": "qfq"},
        },
        "returns": {
            "type": "array",
            "items": {
                "date": "string",
                "open": "number",
                "high": "number",
                "low": "number",
                "close": "number",
                "volume": "integer",
            },
        },
    },

    "get_financial_statement": {
        "description": "获取财务报表",
        "parameters": {
            "ticker": {"type": "string", "required": True},
            "statement_type": {"type": "string", "enum": ["income", "balance", "cashflow"]},
            "period": {"type": "string", "enum": ["annual", "quarterly"]},
        },
    },

    "get_company_info": {
        "description": "获取公司基本信息",
        "parameters": {
            "ticker": {"type": "string", "required": True},
        },
    },

    "get_real_time_quote": {
        "description": "获取实时行情",
        "parameters": {
            "ticker": {"type": "string", "required": True},
        },
    },
}
```

---

*文档版本: v1.0*
*创建日期: 2026-03-05*
