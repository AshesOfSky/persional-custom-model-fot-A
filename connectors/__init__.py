"""
MCP 连接器包

实现 Model Context Protocol 标准，提供统一的数据源接入接口。

模块:
- mcp_client: MCP 客户端主类，管理服务器连接和数据路由
- mcp_akshare_server: Akshare MCP 服务器 (A股/港股/期货)
- mcp_yfinance_server: YFinance MCP 服务器 (美股/港股)

使用示例:
    from connectors import MCPClient, DataFetcherCompat

    # 创建 MCP 客户端
    client = MCPClient(".mcp.json")

    # 调用数据获取
    response = client.call(
        tool="get_daily_price",
        params={"ticker": "AAPL", "start_date": "2024-01-01"},
    )

    if response.success:
        df = pd.DataFrame(response.data)
        print(f"从 {response.source} 获取数据")

    # 兼容旧接口
    fetcher = DataFetcherCompat(client)
    df = fetcher.get_daily_price("AAPL")
"""

from .mcp_client import (
    MCPClient,
    MCPConfig,
    ServerConfig,
    MCPResponse,
    ServerHealth,
    DataRouter,
    DataNormalizer,
    DataFetcherCompat,
    get_mcp_client,
)

__all__ = [
    "MCPClient",
    "MCPConfig",
    "ServerConfig",
    "MCPResponse",
    "ServerHealth",
    "DataRouter",
    "DataNormalizer",
    "DataFetcherCompat",
    "get_mcp_client",
]

__version__ = "1.0.0"
