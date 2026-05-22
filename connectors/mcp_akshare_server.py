"""
Akshare MCP 服务器

将 akshare SDK 封装为标准 MCP 接口
支持 A股/港股/期货数据获取
"""

import json
import sys
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stderr
)
logger = logging.getLogger(__name__)


class AkshareMCPServer:
    """Akshare MCP 服务器"""

    def __init__(self):
        self.tools = {
            "get_daily_price": self.get_daily_price,
            "get_intraday_price": self.get_intraday_price,
            "get_financial_statement": self.get_financial_statement,
            "get_company_info": self.get_company_info,
            "get_industry_list": self.get_industry_list,
            "get_macro_data": self.get_macro_data,
            "get_northbound_flow": self.get_northbound_flow,
            "get_sector_flow": self.get_sector_flow,
        }
        self._init_akshare()

    def _init_akshare(self):
        """初始化 akshare"""
        try:
            import akshare as ak
            self.ak = ak
            logger.info("Akshare initialized successfully")
        except ImportError:
            logger.error("akshare not installed. Run: pip install akshare")
            self.ak = None

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

    def _normalize_symbol(self, ticker: str) -> str:
        """标准化股票代码"""
        # 600519.SS -> 600519
        # 000001.SZ -> 000001
        if "." in ticker:
            return ticker.split(".")[0]
        return ticker

    def _is_futures(self, ticker: str) -> bool:
        """检查是否为期货代码"""
        futures_patterns = ["=F", "GC", "CL", "SI", "NG", "ES", "NQ", "ZB"]
        return any(pattern in ticker for pattern in futures_patterns)

    def get_daily_price(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjust: str = "qfq",
    ) -> List[Dict]:
        """获取日线数据"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        symbol = self._normalize_symbol(ticker)

        # 期货数据
        if self._is_futures(ticker):
            # 映射期货代码
            futures_map = {
                "GC=F": "黄金",
                "CL=F": "原油",
                "SI=F": "白银",
                "NG=F": "天然气",
            }
            commodity = futures_map.get(ticker, "黄金")
            df = self.ak.futures_zh_daily_sina(symbol=commodity)
        # 港股
        elif ticker.endswith(".HK"):
            df = self.ak.stock_hk_daily(symbol=symbol)
        # A股
        else:
            df = self.ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start_date or "19700101",
                end_date=end_date or datetime.now().strftime("%Y%m%d"),
                adjust=adjust,
            )

        # 标准化列名
        column_mapping = {
            "日期": "date",
            "Date": "date",
            "开盘": "open",
            "Open": "open",
            "收盘": "close",
            "Close": "close",
            "最高": "high",
            "High": "high",
            "最低": "low",
            "Low": "low",
            "成交量": "volume",
            "Volume": "volume",
            "成交额": "amount",
            "Amount": "amount",
        }

        df = df.rename(columns=column_mapping)

        # 确保日期格式正确
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        return df.to_dict("records")

    def get_intraday_price(
        self,
        ticker: str,
        period: str = "1min",
    ) -> List[Dict]:
        """获取分时数据"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        symbol = self._normalize_symbol(ticker)

        # 转换周期
        period_map = {
            "1min": "1",
            "5min": "5",
            "15min": "15",
            "30min": "30",
            "60min": "60",
        }

        ak_period = period_map.get(period, "1")

        df = self.ak.stock_zh_a_hist_min_em(
            symbol=symbol,
            period=ak_period,
            adjust="qfq",
        )

        return df.to_dict("records")

    def get_financial_statement(
        self,
        ticker: str,
        statement_type: str = "income",
        period: str = "annual",
    ) -> Dict:
        """获取财务报表"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        symbol = self._normalize_symbol(ticker)

        if statement_type == "income":
            df = self.ak.stock_financial_report_sina(stock=symbol, symbol="利润表")
        elif statement_type == "balance":
            df = self.ak.stock_financial_report_sina(stock=symbol, symbol="资产负债表")
        elif statement_type == "cashflow":
            df = self.ak.stock_financial_report_sina(stock=symbol, symbol="现金流量表")
        else:
            raise ValueError(f"Unknown statement type: {statement_type}")

        return {
            "statement_type": statement_type,
            "data": df.to_dict("records"),
        }

    def get_company_info(self, ticker: str) -> Dict:
        """获取公司信息"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        symbol = self._normalize_symbol(ticker)

        # 获取基本信息
        try:
            info = self.ak.stock_individual_info_em(symbol=symbol)
            info_dict = dict(zip(info["item"], info["value"]))

            return {
                "ticker": ticker,
                "symbol": symbol,
                "name": info_dict.get("股票简称", ""),
                "industry": info_dict.get("行业", ""),
                "market_cap": info_dict.get("总市值", 0),
                "pe_ttm": info_dict.get("市盈率-动态", None),
                "pb": info_dict.get("市净率", None),
                "roe": info_dict.get("净资产收益率", None),
                "source": "akshare",
            }
        except Exception as e:
            logger.error(f"Error getting company info: {e}")
            return {
                "ticker": ticker,
                "symbol": symbol,
                "name": "",
                "error": str(e),
            }

    def get_industry_list(self, industry: Optional[str] = None) -> List[Dict]:
        """获取行业列表或行业成分股"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        if industry:
            # 获取行业成分股
            df = self.ak.stock_board_industry_cons_em(symbol=industry)
            return df.to_dict("records")
        else:
            # 获取行业列表
            df = self.ak.stock_board_industry_name_em()
            return df.to_dict("records")

    def get_macro_data(self, indicator: str = "gdp") -> List[Dict]:
        """获取宏观数据"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        if indicator == "gdp":
            df = self.ak.macro_china_gdp()
        elif indicator == "cpi":
            df = self.ak.macro_china_cpi()
        elif indicator == "ppi":
            df = self.ak.macro_china_ppi()
        elif indicator == "pmi":
            df = self.ak.macro_china_pmi()
        else:
            raise ValueError(f"Unknown indicator: {indicator}")

        return df.to_dict("records")

    def get_northbound_flow(self, days: int = 30) -> List[Dict]:
        """获取北向资金流向"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        df = self.ak.stock_hsgt_hist_em(symbol="沪股通")
        df = df.head(days)

        return df.to_dict("records")

    def get_sector_flow(self) -> List[Dict]:
        """获取行业资金流向"""
        if not self.ak:
            raise RuntimeError("akshare not available")

        df = self.ak.stock_sector_fund_flow_rank(indicator="今日")
        return df.to_dict("records")

    def run(self):
        """运行服务器 (stdio 模式)"""
        logger.info("Akshare MCP Server started")

        while True:
            try:
                # 从 stdin 读取请求
                line = input()
                if not line:
                    continue

                request = json.loads(line)

                # 处理请求
                response = self.handle_request(request)

                # 输出响应
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

    server = AkshareMCPServer()
    server.run()
