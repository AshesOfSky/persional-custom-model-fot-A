"""
factor_screener.py — 量化多因子选股引擎
基于 ak.stock_zh_a_spot_em() 一次API调用获取全A股实时数据
截面Z-score标准化 + 加权评分 + 位置分类
"""

import akshare as ak
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Callable
from datetime import datetime
import logging
import time
import os
import sqlite3
import json

logger = logging.getLogger(__name__)

# ── 权重预设（v2: 加入北向/板块/股息，价值型剥离动量，新增红利型） ──────────

WEIGHT_PRESETS = {
    "均衡型": {
        "动量": 0.16, "量能": 0.16, "技术": 0.20,
        "波动": 0.10, "估值": 0.08, "市值": 0.05,
        "北向": 0.10, "板块": 0.10, "股息": 0.05,
    },
    "动量型": {
        "动量": 0.28, "量能": 0.18, "技术": 0.16,
        "波动": 0.06, "估值": 0.03, "市值": 0.07,
        "北向": 0.10, "板块": 0.10, "股息": 0.02,
    },
    "价值型": {
        "动量": 0.05, "量能": 0.10, "技术": 0.10,
        "波动": 0.12, "估值": 0.30, "市值": 0.05,
        "北向": 0.08, "板块": 0.05, "股息": 0.15,
    },
    "红利型": {  # 新增：2024-2026 红利风格回归
        "动量": 0.05, "量能": 0.08, "技术": 0.10,
        "波动": 0.12, "估值": 0.15, "市值": 0.05,
        "北向": 0.05, "板块": 0.05, "股息": 0.35,
    },
}

# 因子→类别映射
FACTOR_CATEGORY = {
    "f_momentum_5d": "动量",
    "f_momentum_60d": "动量",
    "f_volume_ratio": "量能",
    "f_turnover": "量能",
    "f_amount": "量能",
    "f_amplitude": "波动",
    "f_pe": "估值",
    "f_pb": "估值",
    "f_market_cap": "市值",
    # 历史因子（仅Top N）
    "f_volatility_20d": "波动",
    "f_rsi_14": "技术",
    "f_macd_hist": "技术",
    "f_bb_position": "技术",
    "f_ema_alignment": "技术",
    # A 股特色因子（v2 新增）
    "f_northbound": "北向",
    "f_limit_up_recent": "动量",
    "f_sector_strength": "板块",
    "f_dividend_yield": "股息",
    "f_margin_flow": "量能",
}

# 因子方向（1=越大越好, -1=越小越好, 0=特殊处理）
FACTOR_DIRECTION = {
    "f_momentum_5d": 1,
    "f_momentum_60d": 1,
    "f_volume_ratio": 1,
    "f_turnover": 1,
    "f_amount": 1,
    "f_amplitude": -1,
    "f_pe": -1,
    "f_pb": -1,
    "f_market_cap": -1,
    "f_volatility_20d": -1,
    "f_rsi_14": 0,
    "f_macd_hist": 1,
    "f_bb_position": 0,
    "f_ema_alignment": 1,
    # 新增 A 股特色因子方向
    "f_northbound": 1,         # 净流入越多越好
    "f_limit_up_recent": 1,    # 近期涨停频率越高越强
    "f_sector_strength": 1,    # 板块跑赢大盘越多越好
    "f_dividend_yield": 1,     # 股息率越高越好
    "f_margin_flow": 0,        # 杠杆资金过热反而是反向信号，特殊处理
}

# ── 选股范围 ─────────────────────────────────────────────────────────

UNIVERSE_OPTIONS = ["全市场", "沪深300", "中证500", "创业板", "科创板"]


def fetch_universe(universe_key: str = "全市场") -> pd.DataFrame:
    """
    获取选股范围的实时行情数据
    核心：ak.stock_zh_a_spot_em() 一次调用返回全A股
    """
    try:
        spot_df = ak.stock_zh_a_spot_em()
    except Exception as e:
        logger.error(f"获取全市场行情失败: {e}")
        return pd.DataFrame()

    if spot_df is None or spot_df.empty:
        return pd.DataFrame()

    # 确保代码为字符串
    spot_df["代码"] = spot_df["代码"].astype(str)

    if universe_key == "全市场":
        return spot_df

    if universe_key == "创业板":
        return spot_df[spot_df["代码"].str.startswith(("300", "301"))].copy()

    if universe_key == "科创板":
        return spot_df[spot_df["代码"].str.startswith(("688", "689"))].copy()

    # 沪深300/中证500：获取成分股列表
    index_map = {"沪深300": "000300", "中证500": "000905"}
    index_code = index_map.get(universe_key)
    if index_code:
        try:
            cons = ak.index_stock_cons(symbol=index_code)
            if cons is not None and not cons.empty:
                codes = set(cons["品种代码"].astype(str))
                return spot_df[spot_df["代码"].isin(codes)].copy()
        except Exception as e:
            logger.warning(f"获取{universe_key}成分股失败: {e}，回退全市场")

    return spot_df


# ── Spot因子计算 ──────────────────────────────────────────────────────

def compute_spot_factors(spot_df: pd.DataFrame) -> pd.DataFrame:
    """
    从实时行情数据提取因子列
    纯pandas计算，无额外API调用
    """
    df = spot_df.copy()

    # 过滤：去除ST、停牌、新股（上市不足60天用涨跌幅为0近似）
    if "名称" in df.columns:
        df = df[~df["名称"].str.contains("ST|退", na=False)]

    # 过滤停牌（成交额为0）
    if "成交额" in df.columns:
        df["成交额"] = pd.to_numeric(df["成交额"], errors="coerce").fillna(0)
        df = df[df["成交额"] > 0]

    # 过滤异常价格
    if "最新价" in df.columns:
        df["最新价"] = pd.to_numeric(df["最新价"], errors="coerce")
        df = df[df["最新价"] > 0]

    # 提取因子
    col_map = {
        "涨跌幅": "f_momentum_5d",
        "60日涨跌幅": "f_momentum_60d",
        "量比": "f_volume_ratio",
        "换手率": "f_turnover",
        "成交额": "f_amount",
        "振幅": "f_amplitude",
        "市盈率-动态": "f_pe",
        "市净率": "f_pb",
        "流通市值": "f_market_cap",
    }

    # 备选列名映射（不同akshare版本列名可能不同）
    col_aliases = {
        "市盈率-动态": ["市盈率(动态)", "市盈率"],
        "市净率": ["市净率(MRQ)"],
    }

    for src_col, factor_name in col_map.items():
        if src_col in df.columns:
            df[factor_name] = pd.to_numeric(df[src_col], errors="coerce")
        else:
            # 尝试备选列名
            found = False
            for alias in col_aliases.get(src_col, []):
                if alias in df.columns:
                    df[factor_name] = pd.to_numeric(df[alias], errors="coerce")
                    found = True
                    break
            if not found:
                df[factor_name] = np.nan

    # 对PE/PB做合理截断（负值 + 行业百分位截断）
    if "f_pe" in df.columns:
        df.loc[df["f_pe"] <= 0, "f_pe"] = np.nan
        # 行业百分位截断：有行业列时按行业 98 百分位截断，否则全市场 98 百分位
        pe_valid = df["f_pe"].dropna()
        if len(pe_valid) > 20:
            if "所属行业" in df.columns:
                for ind, grp in df.groupby("所属行业"):
                    if len(grp) >= 5:
                        p98 = grp["f_pe"].quantile(0.98)
                        df.loc[(df["所属行业"] == ind) & (df["f_pe"] > p98), "f_pe"] = np.nan
            else:
                p98 = pe_valid.quantile(0.98)
                df.loc[df["f_pe"] > p98, "f_pe"] = np.nan
        else:
            df.loc[df["f_pe"] > 500, "f_pe"] = np.nan  # 兜底

    if "f_pb" in df.columns:
        df.loc[df["f_pb"] <= 0, "f_pb"] = np.nan
        pb_valid = df["f_pb"].dropna()
        if len(pb_valid) > 20:
            if "所属行业" in df.columns:
                for ind, grp in df.groupby("所属行业"):
                    if len(grp) >= 5:
                        p98 = grp["f_pb"].quantile(0.98)
                        df.loc[(df["所属行业"] == ind) & (df["f_pb"] > p98), "f_pb"] = np.nan
            else:
                p98 = pb_valid.quantile(0.98)
                df.loc[df["f_pb"] > p98, "f_pb"] = np.nan
        else:
            df.loc[df["f_pb"] > 50, "f_pb"] = np.nan  # 兜底

    return df.reset_index(drop=True)


# ── 历史因子计算（单股） ──────────────────────────────────────────────

def compute_historical_factors(code: str, days: int = 60,
                                with_a_share_factors: bool = True,
                                stock_info: dict = None,
                                sector_index_df: pd.DataFrame = None) -> Dict[str, float]:
    """
    单只股票的历史技术因子（v2: 可选启用 A 股特色因子）

    参数:
        code: 股票代码（不带后缀，如 600519）
        days: 历史天数
        with_a_share_factors: 启用北向/涨停近期/板块强度/股息率/融资融券 5 个 A 股因子
                              （需要额外 API 调用，全市场扫描时建议配缓存）
        stock_info: 股息率因子需要的基本面字典（含 dividend_yield 字段）
        sector_index_df: 板块强度因子需要的板块指数 DataFrame（含 Close 列）
    """
    factors = {}
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        if df is None or df.empty or len(df) < 20:
            return factors

        df = df.tail(days).copy()
        close = pd.to_numeric(df["收盘"], errors="coerce")
        high = pd.to_numeric(df["最高"], errors="coerce")
        low = pd.to_numeric(df["最低"], errors="coerce")
        volume = pd.to_numeric(df["成交量"], errors="coerce")

        # 20日波动率
        returns = close.pct_change().dropna()
        if len(returns) >= 20:
            factors["f_volatility_20d"] = float(returns.tail(20).std() * np.sqrt(252) * 100)

        # RSI 14
        if len(returns) >= 14:
            delta = returns.tail(14)
            gain = delta.clip(lower=0).mean()
            loss = (-delta.clip(upper=0)).mean()
            rs = gain / loss if loss != 0 else 100
            factors["f_rsi_14"] = float(100 - 100 / (1 + rs))

        # MACD柱
        if len(close) >= 26:
            ema12 = close.ewm(span=12).mean()
            ema26 = close.ewm(span=26).mean()
            dif = ema12 - ema26
            dea = dif.ewm(span=9).mean()
            macd_hist = (dif - dea) * 2
            factors["f_macd_hist"] = float(macd_hist.iloc[-1])

        # BB位置 (0-1范围，0.5=中轨)
        if len(close) >= 20:
            ma20 = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            upper = ma20 + 2 * std20
            lower = ma20 - 2 * std20
            bb_range = upper.iloc[-1] - lower.iloc[-1]
            if bb_range > 0:
                factors["f_bb_position"] = float(
                    (close.iloc[-1] - lower.iloc[-1]) / bb_range
                )

        # EMA排列 (EMA8 > EMA21 > EMA55 则为多头排列，得分+1)
        if len(close) >= 55:
            ema8 = close.ewm(span=8).mean().iloc[-1]
            ema21 = close.ewm(span=21).mean().iloc[-1]
            ema55 = close.ewm(span=55).mean().iloc[-1]
            score = 0
            if ema8 > ema21:
                score += 1
            if ema21 > ema55:
                score += 1
            if close.iloc[-1] > ema8:
                score += 1
            factors["f_ema_alignment"] = float(score)  # 0-3

        # ── A 股特色因子 ──
        if with_a_share_factors:
            # 涨停近期因子（用已经拉到的 K 线，无额外 API 调用）
            try:
                ohlcv_for_limit = pd.DataFrame({"Close": close.values}, index=df.index)
                factors["f_limit_up_recent"] = get_limit_up_recency_score(code, ohlcv_for_limit)
            except Exception:
                factors["f_limit_up_recent"] = 0.0

            # 股息率因子（依赖外部传入的 info dict）
            if stock_info:
                try:
                    factors["f_dividend_yield"] = get_dividend_yield_score(stock_info)
                except Exception:
                    factors["f_dividend_yield"] = 0.0

            # 板块强度因子（依赖外部传入的板块指数 DataFrame）
            if sector_index_df is not None and not sector_index_df.empty:
                try:
                    stock_ohlcv = pd.DataFrame({"Close": close.values}, index=df.index)
                    factors["f_sector_strength"] = get_sector_relative_strength(
                        stock_ohlcv, sector_index_df,
                    )
                except Exception:
                    factors["f_sector_strength"] = 0.0

            # 北向资金 + 融资融券（每只股票一次 API 调用，全市场扫描请配缓存）
            try:
                factors["f_northbound"] = get_northbound_score(code)
            except Exception:
                factors["f_northbound"] = 0.0
            try:
                factors["f_margin_flow"] = get_margin_flow_score(code)
            except Exception:
                factors["f_margin_flow"] = 0.0

    except Exception as e:
        logger.debug(f"历史因子计算失败 {code}: {e}")

    return factors


# ── 因子时间衰减（v3 新增）────────────────────────────────────────────
# 半衰期：因子信号随时间衰减，动量因子衰减快，估值因子衰减慢

FACTOR_HALF_LIFE_DAYS = {
    "动量": 60,      # ~3个月
    "量能": 20,      # ~1个月
    "技术": 40,      # ~2个月
    "波动": 60,      # ~3个月
    "估值": 250,     # ~12个月
    "市值": 250,     # ~12个月
    "北向": 10,      # ~2周（资金流向变化快）
    "板块": 40,      # ~2个月
    "股息": 250,     # ~12个月（分红频率低）
}


def apply_factor_decay(z_scores: pd.Series, factor_name: str,
                       data_age_days: int = 0) -> pd.Series:
    """
    对因子Z-score施加时间衰减
    data_age_days: 因子数据的"年龄"（距计算日的天数），0=当天
    返回衰减后的Z-score
    """
    if data_age_days <= 0:
        return z_scores  # 当天数据不衰减

    category = FACTOR_CATEGORY.get(factor_name, "")
    half_life = FACTOR_HALF_LIFE_DAYS.get(category, 120)
    decay = 0.5 ** (data_age_days / half_life)
    return z_scores * decay


# ── Z-score标准化 ────────────────────────────────────────────────────

def _zscore_series(series: pd.Series) -> pd.Series:
    """单列Z-score，cap ±3"""
    s = series.astype(float)
    mean, std = s.mean(), s.std()
    if std > 0:
        return ((s - mean) / std).clip(-3, 3)
    return pd.Series(0.0, index=s.index)


# 需要行业分段标准化的因子（估值/股息在不同行业天然差异大）
_INDUSTRY_SEGMENTED_FACTORS = {"f_pe", "f_pb", "f_dividend_yield"}

# 市值分段边界（亿元），用于波动/量能因子
_MCAP_BINS = [0, 30, 100, 500, float("inf")]
_MCAP_LABELS = ["微盘", "小盘", "中盘", "大盘"]


def normalize_factors(df: pd.DataFrame, factor_cols: List[str],
                      segment_by_industry: bool = True) -> pd.DataFrame:
    """截面Z-score标准化，cap ±3

    segment_by_industry=True 时：
    - 估值/股息因子按行业板块分段Z-score（避免银行 vs 科技 PE 比较偏差）
    - 波动/量能因子按市值分段Z-score（微盘股天然高波动/低量能）
    - 其余因子全截面标准化
    分段内样本 < 10 时回退全截面，防止小样本极端值。
    """
    result = df.copy()

    # 预处理：提取行业列和市值分段列
    has_industry = "所属行业" in result.columns if segment_by_industry else False
    has_mcap = "f_market_cap" in result.columns if segment_by_industry else False

    if has_mcap:
        mcap_numeric = pd.to_numeric(result["f_market_cap"], errors="coerce") / 1e8  # 转亿
        result["_mcap_seg"] = pd.cut(mcap_numeric, bins=_MCAP_BINS, labels=_MCAP_LABELS,
                                     right=False)

    for col in factor_cols:
        if col not in result.columns:
            continue

        use_industry_seg = has_industry and col in _INDUSTRY_SEGMENTED_FACTORS
        use_mcap_seg = (has_mcap and not use_industry_seg
                        and FACTOR_CATEGORY.get(col) in ("波动", "量能"))

        if use_industry_seg:
            # 行业分段Z-score
            z = pd.Series(np.nan, index=result.index)
            for industry, grp in result.groupby("所属行业"):
                idx = grp.index
                if len(grp) >= 10:
                    z.loc[idx] = _zscore_series(grp[col])
                else:
                    # 小样本回退全截面
                    z.loc[idx] = _zscore_series(result[col]).loc[idx]
            result[f"z_{col}"] = z.fillna(0.0)

        elif use_mcap_seg:
            # 市值分段Z-score
            z = pd.Series(np.nan, index=result.index)
            for seg, grp in result.groupby("_mcap_seg"):
                idx = grp.index
                if len(grp) >= 10:
                    z.loc[idx] = _zscore_series(grp[col])
                else:
                    z.loc[idx] = _zscore_series(result[col]).loc[idx]
            result[f"z_{col}"] = z.fillna(0.0)

        else:
            # 全截面标准化
            result[f"z_{col}"] = _zscore_series(result[col])

    # 清理临时列
    if "_mcap_seg" in result.columns:
        result.drop(columns=["_mcap_seg"], inplace=True)

    return result


def _adjust_zscore_direction(df: pd.DataFrame, factor_cols: List[str]) -> pd.DataFrame:
    """根据因子方向调整Z-score符号"""
    result = df.copy()
    for col in factor_cols:
        z_col = f"z_{col}"
        if z_col not in result.columns:
            continue
        direction = FACTOR_DIRECTION.get(col, 1)
        if direction == -1:
            result[z_col] = -result[z_col]
        elif direction == 0:
            # RSI/BB: 偏离50/0.5越多扣分
            if "rsi" in col:
                result[z_col] = -abs(result[z_col])  # 越接近50越好
            elif "bb" in col:
                result[z_col] = -abs(result[z_col])  # 越接近中轨越好
    return result


# ── 综合评分 ─────────────────────────────────────────────────────────

def compute_composite_score(
    df: pd.DataFrame,
    factor_cols: List[str],
    weights: Dict[str, float],
    data_age_days: int = 0,
) -> pd.DataFrame:
    """加权Z-score → 因子衰减 → 综合评分 → 排名

    Args:
        data_age_days: 数据年龄（天），用于因子时间衰减。0=当天数据不衰减。
                       使用缓存数据时从缓存时间戳推算。
    """
    result = df.copy()

    # 施加因子时间衰减（仅当数据非当天时生效）
    if data_age_days > 0:
        for f in factor_cols:
            z_col = f"z_{f}"
            if z_col in result.columns:
                result[z_col] = apply_factor_decay(result[z_col], f, data_age_days)

    # 按类别聚合
    category_scores = {}
    for cat in weights:
        cat_factors = [f for f in factor_cols if FACTOR_CATEGORY.get(f) == cat]
        z_cols = [f"z_{f}" for f in cat_factors if f"z_{f}" in result.columns]
        if z_cols:
            category_scores[cat] = result[z_cols].mean(axis=1)
        else:
            category_scores[cat] = pd.Series(0.0, index=result.index)

    # 存储类别评分
    for cat, scores in category_scores.items():
        result[f"cat_{cat}"] = scores

    # 加权总分
    total = pd.Series(0.0, index=result.index)
    for cat, w in weights.items():
        total += category_scores.get(cat, 0) * w

    result["综合评分"] = total
    result["排名"] = result["综合评分"].rank(ascending=False, method="min").astype(int)

    return result.sort_values("综合评分", ascending=False)


# ── 主入口 ───────────────────────────────────────────────────────────

def run_factor_screening(
    universe_key: str = "全市场",
    preset: str = "均衡型",
    custom_weights: Optional[Dict[str, float]] = None,
    top_n: int = 50,
    deep_analysis: bool = True,
    progress_callback: Optional[Callable] = None,
) -> pd.DataFrame:
    """
    多因子选股主入口

    Args:
        universe_key: 选股范围
        preset: 权重预设 ("均衡型"/"动量型"/"价值型"/"自定义")
        custom_weights: 自定义权重（preset="自定义"时使用）
        top_n: 深度分析的股票数量
        deep_analysis: 是否对Top N做历史因子分析
        progress_callback: 进度回调 (pct, text)

    Returns:
        DataFrame with 综合评分、各因子Z-score、排名
    """
    weights = custom_weights if preset == "自定义" and custom_weights else WEIGHT_PRESETS.get(preset, WEIGHT_PRESETS["均衡型"])

    # Step 1: 获取行情
    if progress_callback:
        progress_callback(0.05, "获取全市场行情数据...")
    spot_df = fetch_universe(universe_key)
    if spot_df.empty:
        return pd.DataFrame()

    # Step 2: 提取spot因子
    if progress_callback:
        progress_callback(0.15, f"计算基础因子 ({len(spot_df)}只股票)...")
    df = compute_spot_factors(spot_df)
    if df.empty:
        return pd.DataFrame()

    # Step 3: Z-score标准化
    spot_factors = [f for f in FACTOR_CATEGORY if not f.startswith("f_volatility") and
                    not f.startswith("f_rsi") and not f.startswith("f_macd") and
                    not f.startswith("f_bb") and not f.startswith("f_ema")]
    spot_factors = [f for f in spot_factors if f in df.columns]

    if progress_callback:
        progress_callback(0.25, "Z-score标准化...")
    df = normalize_factors(df, spot_factors)
    df = _adjust_zscore_direction(df, spot_factors)

    # Step 4: 初步评分排名（实时数据 data_age_days=0，不衰减）
    if progress_callback:
        progress_callback(0.30, "初步评分排名...")
    df = compute_composite_score(df, spot_factors, weights, data_age_days=0)

    # Step 5: 对Top N做深度分析
    if deep_analysis and top_n > 0:
        top_df = df.head(min(top_n * 2, len(df))).copy()  # 取2x候选
        total_deep = len(top_df)

        if progress_callback:
            progress_callback(0.35, f"深度分析Top {total_deep}只股票...")

        hist_records = []
        for idx, (_, row) in enumerate(top_df.iterrows()):
            code = str(row.get("代码", ""))
            if not code or len(code) != 6:
                continue

            if progress_callback:
                pct = 0.35 + (idx / total_deep) * 0.55
                progress_callback(pct, f"深度分析: {row.get('名称', code)} ({idx+1}/{total_deep})")

            hist = compute_historical_factors(code)
            hist["代码"] = code
            hist_records.append(hist)
            time.sleep(0.3)

        # 合并历史因子
        if hist_records:
            hist_df = pd.DataFrame(hist_records)
            top_df = top_df.merge(hist_df, on="代码", how="left", suffixes=("", "_hist"))

            # 对历史因子也做标准化
            hist_factor_cols = [f for f in FACTOR_CATEGORY
                                if f.startswith(("f_volatility", "f_rsi", "f_macd", "f_bb", "f_ema"))
                                and f in top_df.columns]
            if hist_factor_cols:
                top_df = normalize_factors(top_df, hist_factor_cols)
                top_df = _adjust_zscore_direction(top_df, hist_factor_cols)

            # 重新计算综合评分（全因子，实时数据不衰减）
            all_factors = spot_factors + hist_factor_cols
            top_df = compute_composite_score(top_df, all_factors, weights, data_age_days=0)
            df = top_df.head(top_n)
        else:
            df = df.head(top_n)
    else:
        df = df.head(top_n)

    if progress_callback:
        progress_callback(1.0, "选股完成!")

    return df.reset_index(drop=True)


# ── A 股特色因子（v2 新增）─────────────────────────────────────────────
# 这些因子均针对单只股票计算，全市场扫描时建议配 6-12h 缓存避免重复 API 调用。

def get_northbound_score(ticker: str) -> float:
    """
    北向资金因子：5 日个股北向净流入 + 20 日趋势
    返回 -1.0..+1.0 标量
    """
    try:
        code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
        df = ak.stock_hsgt_individual_em(stock=code)
        if df is None or df.empty or "持股市值" not in df.columns:
            return 0.0
        df = df.head(25).copy()
        # 持股市值差分作为净流入近似
        net_flow = df["持股市值"].astype(float).diff(-1).fillna(0)
        net_5d = float(net_flow.head(5).sum())
        net_20d = float(net_flow.head(20).sum())
        score = 0.0
        if net_5d > 1e8:
            score += 0.6
        elif net_5d < -1e8:
            score -= 0.6
        # 20 日趋势加成
        if net_20d > 0 and net_5d > 0:
            score += 0.4
        elif net_20d < 0 and net_5d < 0:
            score -= 0.4
        return max(-1.0, min(1.0, score))
    except Exception as e:
        logger.debug(f"北向资金因子计算失败 {ticker}: {e}")
        return 0.0


def get_limit_up_recency_score(ticker: str, df: pd.DataFrame = None) -> float:
    """
    涨停板近期影响因子：基于过去 20 日涨停频率和最近性
    主板涨停 ≥ 9.8%，创业板/科创板 ≥ 19.8%
    """
    try:
        if df is None or df.empty or "Close" not in df.columns:
            return 0.0
        code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
        # 创业板/科创板 20% 涨停板
        is_high_limit = code.startswith("3") or code.startswith("688") or code.startswith("689")
        threshold = 0.198 if is_high_limit else 0.098

        chg = df["Close"].pct_change()
        limit_up = chg >= threshold
        if len(limit_up) < 20:
            return 0.0
        last_3 = int(limit_up.iloc[-3:].sum())
        last_5 = int(limit_up.iloc[-5:].sum())
        last_20 = int(limit_up.iloc[-20:].sum())

        if bool(limit_up.iloc[-1]):
            return 0.8       # 昨日涨停
        if last_5 >= 2:
            return 0.7       # 5 日内连板（妖股）
        if last_3 >= 1:
            return 0.5       # 3 日内有一次涨停
        if last_20 == 0:
            return 0.0
        return 0.2           # 20 日内偶有涨停
    except Exception as e:
        logger.debug(f"涨停近期因子计算失败 {ticker}: {e}")
        return 0.0


def get_sector_relative_strength(stock_df: pd.DataFrame,
                                 sector_index_df: pd.DataFrame) -> float:
    """
    板块相对强度：个股 20 日涨幅 vs 所属板块指数 20 日涨幅
    """
    try:
        if stock_df is None or stock_df.empty or len(stock_df) < 21:
            return 0.0
        if sector_index_df is None or sector_index_df.empty or len(sector_index_df) < 21:
            return 0.0
        stock_20d = float(stock_df["Close"].iloc[-1] / stock_df["Close"].iloc[-21] - 1)
        sector_20d = float(sector_index_df["Close"].iloc[-1] / sector_index_df["Close"].iloc[-21] - 1)
        diff = stock_20d - sector_20d
        if diff > 0.10:
            return 1.0
        if diff > 0.05:
            return 0.6
        if diff > 0:
            return 0.3
        if diff > -0.05:
            return -0.2
        if diff > -0.10:
            return -0.6
        return -1.0
    except Exception as e:
        logger.debug(f"板块强度因子计算失败: {e}")
        return 0.0


def get_dividend_yield_score(info: dict) -> float:
    """股息率因子（红利风格）"""
    try:
        dy = info.get("dividend_yield") if info else None
        if dy is None:
            return 0.0
        dy = float(dy)
        if dy > 0.06:
            return 1.0
        if dy > 0.04:
            return 0.6
        if dy > 0.03:
            return 0.3
        if dy > 0.02:
            return 0.0
        return -0.2
    except Exception as e:
        logger.debug(f"股息率因子计算失败: {e}")
        return 0.0


def get_margin_flow_score(ticker: str) -> float:
    """
    融资融券（融资余额 5 日 vs 20 日）变化率
    注意：> +20% 给中性而非满分（杠杆过热反向风险大于动量收益）
    """
    try:
        code = str(ticker).upper().replace(".SS", "").replace(".SZ", "").replace(".BJ", "")
        df = ak.stock_margin_em(symbol=code)
        if df is None or df.empty or "融资余额" not in df.columns:
            return 0.0
        bal = pd.to_numeric(df["融资余额"], errors="coerce").dropna()
        if len(bal) < 20:
            return 0.0
        bal_5 = float(bal.head(5).mean())
        bal_20 = float(bal.head(20).mean())
        if bal_20 == 0:
            return 0.0
        chg = (bal_5 - bal_20) / bal_20
        if chg > 0.20:
            return 0.2       # 杠杆过热：弱信号而非满分
        if chg > 0.05:
            return 0.5
        if chg > -0.05:
            return 0.0
        if chg > -0.10:
            return -0.3
        return -0.5
    except Exception as e:
        logger.debug(f"融资融券因子计算失败 {ticker}: {e}")
        return 0.0


# ── 缓存 ─────────────────────────────────────────────────────────────

class FactorScreenerCache:
    """因子选股缓存（SQLite，4h TTL）"""

    def __init__(self, cache_hours: int = 4):
        self.cache_hours = cache_hours
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
        os.makedirs(cache_dir, exist_ok=True)
        self.db_path = os.path.join(cache_dir, "factor_cache.db")
        self._init_db()

    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS factor_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cache_key TEXT UNIQUE,
                    data TEXT,
                    created_at TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"初始化因子缓存失败: {e}")

    def get_cached_result(self, universe: str, preset: str, top_n: int):
        """返回 (DataFrame, data_age_days) 或 None。
        data_age_days 用于 apply_factor_decay 在缓存数据上施加时间衰减。
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cache_key = f"factor_{universe}_{preset}_{top_n}"
            cursor = conn.execute(
                "SELECT data, created_at FROM factor_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cursor.fetchone()
            conn.close()

            if row:
                created = datetime.fromisoformat(row[1])
                age_seconds = (datetime.now() - created).total_seconds()
                if age_seconds < self.cache_hours * 3600:
                    data_age_days = max(0, int(age_seconds / 86400))
                    return pd.read_json(row[0], orient="records"), data_age_days
        except Exception as e:
            logger.warning(f"读取因子缓存失败: {e}")
        return None

    def save_result(self, df: pd.DataFrame, universe: str, preset: str, top_n: int):
        try:
            conn = sqlite3.connect(self.db_path)
            cache_key = f"factor_{universe}_{preset}_{top_n}"
            data = df.to_json(orient="records", force_ascii=False)
            conn.execute(
                "INSERT OR REPLACE INTO factor_cache (cache_key, data, created_at) VALUES (?, ?, ?)",
                (cache_key, data, datetime.now().isoformat())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存因子缓存失败: {e}")
