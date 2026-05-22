"""
MCP 客户端

实现 Model Context Protocol 客户端，用于连接和管理多个 MCP 服务器。
提供统一的数据获取接口，支持自动路由和故障转移。
"""

import json
import asyncio
import logging
import os
import subprocess
import time
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import pandas as pd
import threading

logger = logging.getLogger(__name__)


@dataclass
class ServerConfig:
    """MCP服务器配置"""
    command: str
    args: List[str]
    env: Dict[str, str]
    transport: str  # stdio, sse, http
    capabilities: Dict[str, List[str]]
    priority: int = 1
    markets: List[str] = field(default_factory=list)


@dataclass
class MCPConfig:
    """MCP配置"""
    version: str
    mcp_servers: Dict[str, ServerConfig]
    routing: Dict[str, Any]
    normalization: Dict[str, Dict]

    @classmethod
    def from_file(cls, path: str = ".mcp.json") -> "MCPConfig":
        """从文件加载配置"""
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        servers = {}
        for name, config in data.get("mcpServers", {}).items():
            servers[name] = ServerConfig(
                command=config["command"],
                args=config.get("args", []),
                env=config.get("env", {}),
                transport=config.get("transport", "stdio"),
                capabilities=config.get("capabilities", {}),
                priority=config.get("priority", 1),
                markets=config.get("markets", []),
            )

        return cls(
            version=data.get("version", "1.0"),
            mcp_servers=servers,
            routing=data.get("routing", {}),
            normalization=data.get("normalization", {}),
        )


@dataclass
class MCPResponse:
    """MCP响应"""
    success: bool
    data: Any = None
    source: str = ""
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class ServerHealth:
    """服务器健康状态"""
    status: str  # healthy, unhealthy, unknown
    latency_ms: float = 0.0
    last_check: Optional[datetime] = None
    error: str = ""
    available_tools: List[str] = field(default_factory=list)


class MCPServer:
    """MCP服务器连接"""

    def __init__(self, name: str, config: ServerConfig):
        self.name = name
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self.tools: List[str] = []
        self._lock = threading.Lock()
        self._health = ServerHealth(status="unknown")

    def connect(self) -> bool:
        """建立连接 (stdio模式)"""
        try:
            if self.config.transport == "stdio":
                env = {**os.environ, **self.config.env}
                self.process = subprocess.Popen(
                    [self.config.command] + self.config.args,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )

                # 发现可用工具
                self.tools = self.config.capabilities.get("tools", [])
                self._health.status = "healthy"
                logger.info(f"MCPServer {self.name}: Connected, tools={self.tools}")
                return True

            return False
        except Exception as e:
            logger.error(f"MCPServer {self.name}: Connection failed: {e}")
            self._health.status = "unhealthy"
            self._health.error = str(e)
            return False

    def disconnect(self):
        """断开连接"""
        if self.process:
            self.process.terminate()
            self.process = None

    def call(self, tool: str, params: Dict[str, Any]) -> Dict:
        """调用工具"""
        if not self.process:
            if not self.connect():
                raise ConnectionError(f"Server {self.name} not connected")

        if tool not in self.tools:
            raise ValueError(f"Tool {tool} not available in {self.name}")

        request = {
            "tool": tool,
            "params": params,
        }

        try:
            # 发送请求
            request_line = json.dumps(request) + "\n"
            self.process.stdin.write(request_line)
            self.process.stdin.flush()

            # 读取响应
            response_line = self.process.stdout.readline()
            response = json.loads(response_line)

            return response

        except Exception as e:
            logger.error(f"MCPServer {self.name}: Call failed: {e}")
            raise

    def health_check(self) -> ServerHealth:
        """健康检查"""
        try:
            start = time.time()

            # 简单ping检查
            if self.process and self.process.poll() is None:
                latency = (time.time() - start) * 1000
                self._health = ServerHealth(
                    status="healthy",
                    latency_ms=latency,
                    last_check=datetime.now(),
                    available_tools=self.tools,
                )
            else:
                self._health.status = "unhealthy"
                self._health.error = "Process not running"

        except Exception as e:
            self._health.status = "unhealthy"
            self._health.error = str(e)
            self._health.last_check = datetime.now()

        return self._health


class DataRouter:
    """数据路由器"""

    ROUTING_TABLE = {
        "get_daily_price": {
            "A股": ["akshare", "tushare"],
            "美股": ["yfinance"],
            "港股": ["akshare", "yfinance"],
            "期货": ["akshare"],
        },
        "get_financial_statement": {
            "A股": ["akshare", "tushare"],
            "美股": ["yfinance"],
            "港股": ["akshare"],
        },
        "get_company_info": {
            "A股": ["akshare"],
            "美股": ["yfinance"],
            "港股": ["akshare", "yfinance"],
        },
        "get_real_time_quote": {
            "A股": ["akshare"],
            "美股": ["yfinance"],
            "港股": ["akshare"],
        },
        "get_northbound_flow": {
            "A股": ["akshare"],
        },
    }

    @staticmethod
    def detect_market(ticker: str) -> str:
        """检测市场类型"""
        if ticker.endswith(".SS") or ticker.endswith(".SZ") or ticker.isdigit():
            return "A股"
        elif ticker.endswith(".HK"):
            return "港股"
        elif ticker.endswith("=F") or "GC" in ticker or "CL" in ticker:
            return "期货"
        else:
            return "美股"

    def get_candidate_sources(
        self,
        tool: str,
        ticker: str,
        preferred_source: Optional[str] = None,
    ) -> List[str]:
        """获取候选数据源列表"""
        market = self.detect_market(ticker)

        if preferred_source:
            return [preferred_source]

        routing = self.ROUTING_TABLE.get(tool, {})
        sources = routing.get(market, ["akshare"])

        return sources


class DataNormalizer:
    """数据标准化器"""

    STANDARD_PRICE_COLUMNS = {
        "date": "datetime",
        "open": "float",
        "high": "float",
        "low": "float",
        "close": "float",
        "volume": "int",
        "amount": "float",
        "adj_close": "float",
    }

    def __init__(self, config: Dict):
        self.config = config

    def normalize_price_data(
        self,
        data: pd.DataFrame,
        source: str,
    ) -> pd.DataFrame:
        """标准化价格数据"""
        mapping = self.config.get("price_columns", {}).get(source, {})

        # 重命名列
        df = data.rename(columns=mapping)

        # 确保所有标准列存在
        for col in self.STANDARD_PRICE_COLUMNS.keys():
            if col not in df.columns:
                df[col] = None

        # 类型转换
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df

    def normalize_financial_data(
        self,
        data: pd.DataFrame,
        source: str,
    ) -> pd.DataFrame:
        """标准化财务数据"""
        mapping = self.config.get("financial_columns", {}).get(source, {})
        return data.rename(columns=mapping)


class MCPClient:
    """
    MCP 客户端主类
    管理多个 MCP 服务器连接，提供统一的数据获取接口
    """

    def __init__(self, config_path: str = ".mcp.json"):
        self.config = MCPConfig.from_file(config_path)
        self.servers: Dict[str, MCPServer] = {}
        self.router = DataRouter()
        self.normalizer = DataNormalizer(self.config.normalization)
        self._init_servers()

    def _init_servers(self):
        """初始化所有 MCP 服务器连接"""
        for name, server_config in self.config.mcp_servers.items():
            self.servers[name] = MCPServer(name, server_config)
            logger.info(f"MCPClient: Registered server {name}")

    def call(
        self,
        tool: str,
        params: Dict[str, Any],
        preferred_source: Optional[str] = None,
    ) -> MCPResponse:
        """
        调用 MCP 工具

        流程:
        1. 路由到合适的数据源
        2. 执行调用
        3. 失败时自动 fallback
        """
        ticker = params.get("ticker", "")

        # 确定数据源
        if preferred_source and preferred_source in self.servers:
            sources = [preferred_source]
        else:
            sources = self.router.get_candidate_sources(tool, ticker, preferred_source)

        # 尝试各数据源
        for source in sources:
            if source not in self.servers:
                continue

            server = self.servers[source]
            start = time.time()

            try:
                result = server.call(tool, params)
                latency = (time.time() - start) * 1000

                if result.get("success"):
                    data = result.get("data")

                    # 标准化数据
                    if tool == "get_daily_price" and isinstance(data, list):
                        df = pd.DataFrame(data)
                        data = self.normalizer.normalize_price_data(df, source)

                    return MCPResponse(
                        success=True,
                        data=data,
                        source=source,
                        latency_ms=latency,
                    )
                else:
                    logger.warning(f"{source}: {result.get('error')}")

            except Exception as e:
                logger.warning(f"{source} 调用失败: {e}")
                continue

        return MCPResponse(
            success=False,
            error="All data sources failed",
        )

    def health_check(self) -> Dict[str, ServerHealth]:
        """检查所有服务器健康状态"""
        health = {}
        for name, server in self.servers.items():
            health[name] = server.health_check()
        return health

    def get_available_tools(self, server_name: Optional[str] = None) -> List[str]:
        """获取可用工具列表"""
        if server_name:
            server = self.servers.get(server_name)
            return server.tools if server else []

        all_tools = set()
        for server in self.servers.values():
            all_tools.update(server.tools)
        return list(all_tools)

    def close(self):
        """关闭所有连接"""
        for server in self.servers.values():
            server.disconnect()


# 兼容性包装器 - 保持旧接口
class DataFetcherCompat:
    """
    数据获取兼容层
    在迁移期间保持现有代码可用，内部使用新的 MCP 客户端
    """

    def __init__(self, mcp_client: Optional[MCPClient] = None):
        self.mcp_client = mcp_client or MCPClient()

    def get_daily_price(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        **kwargs
    ) -> Optional[pd.DataFrame]:
        """获取日线数据 (兼容旧接口)"""
        response = self.mcp_client.call(
            tool="get_daily_price",
            params={
                "ticker": ticker,
                "start_date": start_date,
                "end_date": end_date,
                **kwargs
            },
        )

        if response.success:
            return response.data
        else:
            logger.error(f"Failed to get daily price: {response.error}")
            return None

    def get_financial_statement(
        self,
        ticker: str,
        statement_type: str = "income",
        **kwargs
    ) -> Optional[pd.DataFrame]:
        """获取财务报表 (兼容旧接口)"""
        response = self.mcp_client.call(
            tool="get_financial_statement",
            params={
                "ticker": ticker,
                "statement_type": statement_type,
                **kwargs
            },
        )

        if response.success:
            return pd.DataFrame(response.data) if isinstance(response.data, list) else response.data
        return None

    def get_company_info(self, ticker: str) -> Optional[Dict]:
        """获取公司信息 (兼容旧接口)"""
        response = self.mcp_client.call(
            tool="get_company_info",
            params={"ticker": ticker},
        )

        if response.success:
            return response.data
        return None


# 便捷函数
def get_mcp_client() -> MCPClient:
    """获取 MCP 客户端实例 (单例)"""
    if not hasattr(get_mcp_client, "_instance"):
        get_mcp_client._instance = MCPClient()
    return get_mcp_client._instance


# 测试代码
if __name__ == "__main__":
    import os

    # 创建测试配置
    test_config = {
        "version": "1.0",
        "mcpServers": {
            "test": {
                "command": "python",
                "args": ["-c", "print('test')"],
                "env": {},
                "transport": "stdio",
                "capabilities": {"tools": ["test_tool"]},
            }
        },
        "routing": {"default_source": "test"},
        "normalization": {},
    }

    with open("test_mcp.json", "w") as f:
        json.dump(test_config, f)

    # 测试客户端
    client = MCPClient("test_mcp.json")
    print(f"Available tools: {client.get_available_tools()}")

    # 健康检查
    health = client.health_check()
    for name, status in health.items():
        print(f"{name}: {status.status}")

    # 清理
    os.remove("test_mcp.json")
