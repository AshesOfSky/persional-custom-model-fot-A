"""
market_sentiment.py — 市场情绪指标引擎
VIX恐慌指数、北向资金、融资融券余额
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


# ─── VIX 恐慌指数 ────────────────────────────────────────────────────────────

def _fetch_vix() -> Dict:
    """获取VIX恐慌指数"""
    try:
        import yfinance as yf
        vix = yf.download("^VIX", period="5d", progress=False)
        if vix.empty:
            return {"available": False}

        # 处理多级列索引
        if isinstance(vix.columns, pd.MultiIndex):
            vix.columns = vix.columns.get_level_values(0)

        current = float(vix["Close"].iloc[-1])
        prev = float(vix["Close"].iloc[-2]) if len(vix) >= 2 else current
        change = current - prev
        change_pct = change / prev * 100 if prev else 0

        if current > 30:
            level = "恐慌"
            interp = f"VIX={current:.1f}，市场极度恐慌，可能存在恐慌性抛售，逆向思维者可关注抄底机会"
        elif current > 20:
            level = "焦虑"
            interp = f"VIX={current:.1f}，市场情绪偏紧张，波动率偏高，需控制仓位"
        elif current > 12:
            level = "正常"
            interp = f"VIX={current:.1f}，市场情绪正常，波动率适中"
        else:
            level = "贪婪"
            interp = f"VIX={current:.1f}，市场极度乐观/自满，需警惕潜在的波动率扩大"

        return {
            "available": True,
            "value": round(current, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "level": level,
            "interpretation": interp,
        }
    except Exception as e:
        logger.warning(f"获取VIX失败: {e}")
        return {"available": False, "error": str(e)}


# ─── 北向资金 ────────────────────────────────────────────────────────────────

def _fetch_northbound_flow() -> Dict:
    """获取北向资金（沪深港通）净流入数据"""
    try:
        import akshare as ak

        # 尝试获取北向资金数据
        try:
            df = ak.stock_hsgt_north_net_flow_in_em()
        except Exception:
            try:
                df = ak.stock_em_hsgt_north_net_flow_in()
            except Exception:
                return {"available": False, "error": "akshare北向资金接口不可用"}

        if df is None or df.empty:
            return {"available": False}

        # 标准化列名
        df.columns = [str(c).strip() for c in df.columns]

        # 查找净流入列
        flow_col = None
        for col in df.columns:
            if "净流入" in col or "净买" in col or "north" in col.lower():
                flow_col = col
                break

        if flow_col is None and len(df.columns) >= 2:
            flow_col = df.columns[1]  # 通常第二列是数据

        if flow_col is None:
            return {"available": False, "error": "未找到净流入数据列"}

        df[flow_col] = pd.to_numeric(df[flow_col], errors="coerce")
        df = df.dropna(subset=[flow_col])

        if df.empty:
            return {"available": False}

        # 最近数据（亿元）
        today_net = float(df[flow_col].iloc[-1])
        net_5d = float(df[flow_col].tail(5).sum()) if len(df) >= 5 else today_net
        net_20d = float(df[flow_col].tail(20).sum()) if len(df) >= 20 else net_5d

        # 趋势判断
        if net_5d > 50:
            trend = "持续大幅流入"
        elif net_5d > 0:
            trend = "小幅净流入"
        elif net_5d > -50:
            trend = "小幅净流出"
        else:
            trend = "持续大幅流出"

        interp = f"北向资金今日净{'流入' if today_net > 0 else '流出'}{abs(today_net):.1f}亿，" \
                 f"近5日累计净{'流入' if net_5d > 0 else '流出'}{abs(net_5d):.1f}亿。{trend}。"

        if net_5d > 100:
            interp += "外资持续加仓，看好A股后市。"
        elif net_5d < -100:
            interp += "外资持续减仓，对A股偏谨慎。"

        return {
            "available": True,
            "today_net": round(today_net, 2),
            "5d_net": round(net_5d, 2),
            "20d_net": round(net_20d, 2),
            "trend": trend,
            "interpretation": interp,
        }
    except Exception as e:
        logger.warning(f"获取北向资金失败: {e}")
        return {"available": False, "error": str(e)}


# ─── 融资融券 ────────────────────────────────────────────────────────────────

def _fetch_margin_data() -> Dict:
    """获取两融（融资融券）数据"""
    try:
        import akshare as ak

        try:
            df = ak.stock_margin_sse(start_date=(datetime.now() - timedelta(days=30)).strftime('%Y%m%d'))
        except Exception:
            try:
                df = ak.stock_margin_detail_szse()
            except Exception:
                return {"available": False, "error": "akshare融资融券接口不可用"}

        if df is None or df.empty:
            return {"available": False}

        df.columns = [str(c).strip() for c in df.columns]

        # 查找融资余额列
        balance_col = None
        for col in df.columns:
            if "融资余额" in col or "余额" in col:
                balance_col = col
                break

        if balance_col is None and len(df.columns) >= 3:
            balance_col = df.columns[2]

        if balance_col is None:
            return {"available": False, "error": "未找到融资余额列"}

        df[balance_col] = pd.to_numeric(df[balance_col], errors="coerce")
        df = df.dropna(subset=[balance_col])

        if df.empty:
            return {"available": False}

        current_balance = float(df[balance_col].iloc[-1])
        prev_balance = float(df[balance_col].iloc[-2]) if len(df) >= 2 else current_balance
        change_pct = (current_balance - prev_balance) / prev_balance * 100 if prev_balance else 0

        # 亿元换算
        if current_balance > 1e10:
            display_balance = current_balance / 1e8
            unit = "亿元"
        else:
            display_balance = current_balance
            unit = "元"

        if change_pct > 1:
            interp = f"融资余额{display_balance:.0f}{unit}，日增{change_pct:.2f}%，资金杠杆意愿增强"
        elif change_pct < -1:
            interp = f"融资余额{display_balance:.0f}{unit}，日减{abs(change_pct):.2f}%，资金杠杆意愿减弱"
        else:
            interp = f"融资余额{display_balance:.0f}{unit}，变动不大，市场杠杆水平平稳"

        return {
            "available": True,
            "total_balance": round(display_balance, 2),
            "unit": unit,
            "change_pct": round(change_pct, 2),
            "interpretation": interp,
        }
    except Exception as e:
        logger.warning(f"获取融资融券失败: {e}")
        return {"available": False, "error": str(e)}


# ─── 综合情绪 ────────────────────────────────────────────────────────────────

def _combine_sentiment(vix: Dict, northbound: Dict, margin: Dict) -> Dict:
    """综合市场情绪评分"""
    score = 0
    count = 0

    # VIX: 高VIX = 恐惧(负分), 低VIX = 贪婪(正分)
    if vix.get("available"):
        v = vix["value"]
        if v > 30:
            score -= 40
        elif v > 20:
            score -= 15
        elif v < 12:
            score += 20
        else:
            score += 5
        count += 1

    # 北向资金: 流入=正分
    if northbound.get("available"):
        net_5d = northbound.get("5d_net", 0)
        if net_5d > 100:
            score += 35
        elif net_5d > 30:
            score += 15
        elif net_5d < -100:
            score -= 35
        elif net_5d < -30:
            score -= 15
        count += 1

    # 融资融券: 增加=正分（杠杆意愿强）
    if margin.get("available"):
        chg = margin.get("change_pct", 0)
        if chg > 2:
            score += 15
        elif chg > 0:
            score += 5
        elif chg < -2:
            score -= 15
        elif chg < 0:
            score -= 5
        count += 1

    avg_score = score / count if count > 0 else 0

    if avg_score > 30:
        sentiment = "极度贪婪"
    elif avg_score > 10:
        sentiment = "偏贪婪"
    elif avg_score > -10:
        sentiment = "中性"
    elif avg_score > -30:
        sentiment = "偏恐慌"
    else:
        sentiment = "极度恐慌"

    return {
        "combined_sentiment": sentiment,
        "sentiment_score": round(avg_score, 1),
    }


# ─── 主函数 ──────────────────────────────────────────────────────────────────

def fetch_market_sentiment(ticker: str = "", market: str = "A股") -> Dict:
    """
    获取市场情绪综合数据

    参数:
        ticker: 标的代码（用于判断市场）
        market: 市场类型（A股/美股/港股/期货）

    返回:
        VIX + 北向资金 + 融资融券 + 综合情绪
    """
    # VIX（全市场通用）
    vix = _fetch_vix()

    # 北向资金和融资融券仅对A股有意义
    northbound = {"available": False}
    margin_data = {"available": False}

    if market in ("A股", "A股个股", "A股ETF", "A股指数") or \
       (ticker and (ticker.endswith(".SS") or ticker.endswith(".SZ") or
        ticker.startswith("6") or ticker.startswith("0") or ticker.startswith("3"))):
        northbound = _fetch_northbound_flow()
        margin_data = _fetch_margin_data()

    # 综合情绪
    combined = _combine_sentiment(vix, northbound, margin_data)

    return {
        "vix": vix,
        "northbound_flow": northbound,
        "margin_balance": margin_data,
        **combined,
    }
