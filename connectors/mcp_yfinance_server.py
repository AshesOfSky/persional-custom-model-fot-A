"""
YFinance MCP 服务器

将 yfinance 封装为标准 MCP 接口
支持美股/港股数据获取
"""

import json
import sys
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class YFinanceMCPServer:
    """YFinance MCP 服务器"""

    def __init__(self):
        self.tools = {
            "get_daily_price": self.get_daily_price,
            "get_financial_statement": self.get_financial_statement,
            "get_company_info": self.get_company_info,
            "get_real_time_quote": self.get_real_time_quote,
            "get_options_chain": self.get_options_chain,
        }
        self._init_yfinance()

    def _init_yfinance(self):
        """初始化 yfinance"""
        try:
            import yfinance as yf
            self.yf = yf
            logger.info("YFinance initialized successfully")
        except ImportError:
            logger.error("yfinance not installed. Run: pip install yfinance")
            self.yf = None

    def handle_request(self, request: Dict) -> Dict:
        """处理 MCP 请求"""
        tool = request.get("tool")
        params = request.get("params", {})

        logger.info(f"Handling request: tool={tool}, params={params}")

        if tool not in self.tools:
            return {
                "success": False,
                "error": f"Unknown tool: {tool}",
                "available_tools": list(self.tools.keys()),
            }

        try:
            result = self.tools[tool](**params)
            return {"success": True, "data": result}
        except Exception as e:
            logger.error(f"Error handling request: {e}")
            return {"success": False, "error": str(e)}

    def _get_ticker(self, ticker: str) -> Any:
        """获取 yfinance Ticker 对象"""
        if not self.yf:
            raise RuntimeError("yfinance not available")

        # 转换期货代码
        if ticker == "GC=F":
            ticker = "GC=F"
        elif ticker == "CL=F":
            ticker = "CL=F"

        return self.yf.Ticker(ticker)

    def get_daily_price(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        period: str = "1y",
    ) -> List[Dict]:
        """获取日线数据"""
        yt = self._get_ticker(ticker)

        # 解析日期
        if start_date and end_date:
            df = yt.history(start=start_date, end=end_date)
        else:
            df = yt.history(period=period)

        # 重置索引，将日期变为列
        df = df.reset_index()

        # 标准化列名
        df.columns = [col.lower().replace(" ", "_") for col in df.columns]

        # 处理日期格式
        if "date" in df.columns:
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")

        return df.to_dict("records")

    def get_financial_statement(
        self,
        ticker: str,
        statement_type: str = "income",
    ) -> Dict:
        """获取财务报表"""
        yt = self._get_ticker(ticker)

        if statement_type == "income":
            df = yt.financials
        elif statement_type == "quarterly_income":
            df = yt.quarterly_financials
        elif statement_type == "balance":
            df = yt.balance_sheet
        elif statement_type == "cashflow":
            df = yt.cashflow
        else:
            raise ValueError(f"Unknown statement type: {statement_type}")

        # 转置，使日期成为行
        df = df.T.reset_index()
        df.columns = [str(col).lower().replace(" ", "_") for col in df.columns]

        return {
            "statement_type": statement_type,
            "data": df.to_dict("records"),
        }

    def get_company_info(self, ticker: str) -> Dict:
        """获取公司信息"""
        yt = self._get_ticker(ticker)
        info = yt.info

        return {
            "ticker": ticker,
            "symbol": info.get("symbol", ticker),
            "name": info.get("longName", info.get("shortName", "")),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "market_cap": info.get("marketCap", 0),
            "pe_ttm": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "pb": info.get("priceToBook"),
            "dividend_yield": info.get("dividendYield"),
            "beta": info.get("beta"),
            "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
            "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            "average_volume": info.get("averageVolume"),
            "website": info.get("website", ""),
            "source": "yfinance",
        }

    def get_real_time_quote(self, ticker: str) -> Dict:
        """获取实时行情"""
        yt = self._get_ticker(ticker)

        # 获取最新数据
        hist = yt.history(period="1d")
        info = yt.info

        if hist.empty:
            return {"error": "No data available"}

        latest = hist.iloc[-1]

        return {
            "ticker": ticker,
            "timestamp": datetime.now().isoformat(),
            "price": latest["Close"],
            "change": latest["Close"] - info.get("previousClose", latest["Close"]),
            "change_percent": (latest["Close"] / info.get("previousClose", latest["Close"]) - 1) * 100 if info.get("previousClose") else 0,
            "volume": latest["Volume"],
            "high": latest["High"],
            "low": latest["Low"],
            "open": latest["Open"],
            "market_state": "REGULAR",  # 简化处理
        }

    def get_options_chain(
        self,
        ticker: str,
        expiration_date: Optional[str] = None,
    ) -> Dict:
        """获取期权链"""
        yt = self._get_ticker(ticker)

        # 获取到期日列表
        expirations = yt.options

        if not expirations:
            return {"error": "No options available"}

        # 选择到期日
        if expiration_date and expiration_date in expirations:
            selected_date = expiration_date
        else:
            selected_date = expirations[0]

        # 获取期权链
        chain = yt.option_chain(selected_date)

        return {
            "ticker": ticker,
            "expiration_date": selected_date,
            "available_expirations": expirations,
            "calls": chain.calls.to_dict("records"),
            "puts": chain.puts.to_dict("records"),
        }

    def run(self):
        """运行服务器 (stdio 模式)"""
        logger.info("YFinance MCP Server started")

        while True:
            try:
                line = input()
                if not line:
                    continue

                request = json.loads(line)
                response = self.handle_request(request)
                print(json.dumps(response, ensure_ascii=False), flush=True)

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON: {e}")
                print(json.dumps({"error": "Invalid JSON"}), flush=True)
            except EOFError:
                logger.info("EOF received, shutting down")
                break
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    import pandas as pd

    server = YFinanceMCPServer()
    server.run()
