"""
run_backtest_v3.py — v3 策略回测可执行入口

用法：
    # 单只
    python run_backtest_v3.py --ticker 600519.SS --start 2022-01-01 --end 2025-12-31

    # 批量（沪深300 + 中证500 + 科创50 全跑，自动分板块用对应阈值）
    python run_backtest_v3.py --batch --output backtest_v3_results.xlsx

    # 关闭某些 v3 改进做对比（A/B test）
    python run_backtest_v3.py --batch --no-drawdown-breaker --no-market-filter

依赖：
    - 已实现的 modules.strategy_v3.TrendStrengthV3Strategy
    - modules.backtest.BacktestEngine（含 v3 标志）
    - 数据接口：modules.data_router.fetch_all 或直接 akshare/yfinance
"""

import os
import sys
import argparse
import logging
from datetime import datetime
from typing import List, Optional
import pandas as pd

# 让脚本在仓库根目录直接跑
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.technical import add_all_indicators
from modules.backtest import BacktestEngine
from modules.strategy_v3 import TrendStrengthV3Strategy
from modules.stock_search import get_board

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s | %(message)s")
log = logging.getLogger("backtest_v3")


# ─── 数据获取 ────────────────────────────────────────────────────────

def fetch_ohlcv(ticker: str, start: str, end: str) -> Optional[pd.DataFrame]:
    """
    获取标的 OHLCV。优先 akshare（A股），失败回退 yfinance（美股/港股）
    """
    code = ticker.replace(".SS", "").replace(".SZ", "").replace(".BJ", "").upper()

    # A 股：akshare
    if code.isdigit() and len(code) == 6:
        try:
            import akshare as ak
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start.replace("-", ""), end_date=end.replace("-", ""),
                adjust="qfq",
            )
            if df is None or df.empty:
                return None
            df = df.rename(columns={
                "日期": "Date", "开盘": "Open", "收盘": "Close",
                "最高": "High", "最低": "Low", "成交量": "Volume",
            })
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date").sort_index()
            return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        except Exception as e:
            log.warning(f"akshare fetch failed for {code}: {e}")
            return None

    # 美股/港股：yfinance
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=False)
        if df is None or df.empty:
            return None
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        log.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


def fetch_benchmark(start: str, end: str) -> Optional[pd.DataFrame]:
    """沪深 300 作为市场环境基准"""
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily(symbol="sh000300")
        df = df.rename(columns={"date": "Date", "close": "Close",
                                  "open": "Open", "high": "High",
                                  "low": "Low", "volume": "Volume"})
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        df = df[(df.index >= start) & (df.index <= end)]
        return df
    except Exception as e:
        log.warning(f"benchmark fetch failed: {e}")
        return None


# ─── 单只回测 ───────────────────────────────────────────────────────

def backtest_one(ticker: str, start: str, end: str,
                 benchmark_df: pd.DataFrame = None,
                 stock_name: str = "",
                 v3_flags: dict = None) -> Optional[dict]:
    """
    单标的 v3 回测，返回汇总字典
    """
    v3_flags = v3_flags or {}

    df = fetch_ohlcv(ticker, start, end)
    if df is None or len(df) < 200:
        log.warning(f"{ticker}: 数据不足，跳过")
        return None

    # 计算所有指标
    df = add_all_indicators(df)

    # 板块识别
    board = get_board(ticker, stock_name)
    if board == "ST":
        log.info(f"{ticker}: ST 标的，禁入")
        return None

    # 构造 v3 策略
    strategy = TrendStrengthV3Strategy(
        board=board,
        ticker=ticker,
        benchmark_df=benchmark_df,
        use_market_filter=v3_flags.get("market_filter", True),
        use_consecutive_loss_cooldown=v3_flags.get("loss_cooldown", True),
        use_breakeven_stop=v3_flags.get("breakeven_stop", True),
    )

    # 构造引擎（带 v3 防御层）
    engine = BacktestEngine(
        initial_capital=100_000,
        commission_rate=0.001,
        slippage=0.001,
        use_atr_stop=True,
        board=board,
        market_cap_yi=v3_flags.get("market_cap_yi"),
        use_drawdown_breaker=v3_flags.get("drawdown_breaker", True),
        drawdown_limit=v3_flags.get("drawdown_limit", 0.20),
        cooldown_bars=v3_flags.get("cooldown_bars", 20),
        use_gap_protection=v3_flags.get("gap_protection", True),
    )

    result = engine.run(df, strategy, ticker=ticker, period=f"{start}~{end}")

    return {
        "ticker":         ticker,
        "name":           stock_name or ticker,
        "board":          board,
        "total_return":   result.total_return,
        "total_trades":   result.total_trades,
        "win_rate":       result.win_rate,
        "avg_profit":     result.avg_profit,
        "avg_loss":       result.avg_loss,
        "profit_factor":  result.profit_factor,
        "max_drawdown":   result.max_drawdown_pct,
        "sharpe":         result.sharpe_ratio,
        "final_capital":  result.final_capital,
    }


# ─── 批量入口 ───────────────────────────────────────────────────────

def get_universe(universe_key: str) -> List[tuple]:
    """返回 [(ticker, name), ...]"""
    import akshare as ak
    if universe_key == "沪深300":
        cons = ak.index_stock_cons(symbol="000300")
    elif universe_key == "中证500":
        cons = ak.index_stock_cons(symbol="000905")
    elif universe_key == "科创50":
        cons = ak.index_stock_cons(symbol="000688")
    else:
        raise ValueError(f"未知 universe: {universe_key}")
    return [(str(r["品种代码"]), str(r["品种名称"])) for _, r in cons.iterrows()]


def run_batch(universes: List[str], start: str, end: str,
              output: str, v3_flags: dict):
    """批量回测并导出 Excel"""
    print(f"\n=== v3 批量回测 ===")
    print(f"  时间窗口: {start} ~ {end}")
    print(f"  标的池:   {', '.join(universes)}")
    print(f"  v3 防御层: {v3_flags}\n")

    benchmark_df = fetch_benchmark(start, end)
    if benchmark_df is None:
        print("⚠️  沪深 300 基准获取失败，市场过滤将禁用")

    all_results = []
    for u in universes:
        try:
            stocks = get_universe(u)
        except Exception as e:
            print(f"❌ {u} 成分股获取失败: {e}")
            continue
        print(f"\n--- {u} ({len(stocks)} 只) ---")

        for idx, (code, name) in enumerate(stocks):
            if idx % 50 == 0:
                print(f"  进度 {idx}/{len(stocks)}")
            try:
                # 构造带后缀的 ticker
                if code.startswith("6"):
                    ticker = f"{code}.SS"
                elif code.startswith(("0", "3", "1", "2")):
                    ticker = f"{code}.SZ"
                else:
                    ticker = code
                r = backtest_one(ticker, start, end,
                                  benchmark_df=benchmark_df,
                                  stock_name=name,
                                  v3_flags=v3_flags)
                if r is not None:
                    r["所属指数"] = u
                    all_results.append(r)
            except Exception as e:
                log.warning(f"{code} 回测失败: {e}")

    if not all_results:
        print("\n❌ 无有效结果")
        return

    df = pd.DataFrame(all_results)
    df["total_return"] = df["total_return"] * 100
    df["win_rate"] = df["win_rate"] * 100
    df["max_drawdown"] = df["max_drawdown"] * 100

    # 汇总
    print("\n=== 汇总 ===")
    summary = df.groupby("所属指数").agg(
        股票数=("ticker", "count"),
        平均收益率_pct=("total_return", "mean"),
        中位收益率_pct=("total_return", "median"),
        正收益占比_pct=("total_return", lambda x: (x > 0).sum() / len(x) * 100),
        平均胜率_pct=("win_rate", "mean"),
        平均最大回撤_pct=("max_drawdown", "mean"),
        平均夏普=("sharpe", "mean"),
    ).round(2)
    print(summary.to_string())

    # 写出 Excel
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="汇总")
        df.to_excel(writer, sheet_name="全部详细结果", index=False)
        for u in universes:
            sub = df[df["所属指数"] == u]
            if not sub.empty:
                sub.to_excel(writer, sheet_name=f"{u}_明细", index=False)
    print(f"\n✅ 结果已写入: {output}")


# ─── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="v3 策略回测")
    parser.add_argument("--ticker", help="单只标的（如 600519.SS）")
    parser.add_argument("--batch", action="store_true", help="批量跑全部三大指数")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output", default="backtest_v3_results.xlsx")

    # v3 特性开关（用于 A/B 对比）
    parser.add_argument("--no-drawdown-breaker", action="store_true")
    parser.add_argument("--no-market-filter", action="store_true")
    parser.add_argument("--no-loss-cooldown", action="store_true")
    parser.add_argument("--no-breakeven-stop", action="store_true")
    parser.add_argument("--no-gap-protection", action="store_true")
    parser.add_argument("--drawdown-limit", type=float, default=0.20)
    parser.add_argument("--cooldown-bars", type=int, default=20)

    args = parser.parse_args()

    v3_flags = {
        "drawdown_breaker": not args.no_drawdown_breaker,
        "market_filter":    not args.no_market_filter,
        "loss_cooldown":    not args.no_loss_cooldown,
        "breakeven_stop":   not args.no_breakeven_stop,
        "gap_protection":   not args.no_gap_protection,
        "drawdown_limit":   args.drawdown_limit,
        "cooldown_bars":    args.cooldown_bars,
    }

    if args.batch:
        run_batch(["沪深300", "中证500", "科创50"], args.start, args.end,
                  args.output, v3_flags)
    elif args.ticker:
        bench = fetch_benchmark(args.start, args.end)
        r = backtest_one(args.ticker, args.start, args.end,
                          benchmark_df=bench, v3_flags=v3_flags)
        if r:
            print("\n=== v3 单标的回测结果 ===")
            for k, v in r.items():
                if isinstance(v, float):
                    print(f"  {k:18s}: {v:.4f}")
                else:
                    print(f"  {k:18s}: {v}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
