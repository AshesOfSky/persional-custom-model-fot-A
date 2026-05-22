"""
technical.py — 技术指标计算与图表渲染
支持EMA隧道、布林带、KDJ、MACD、RSI、VWAP、OBV、CCI、WR、DMI、SAR等
"""

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ─── EMA 配置 ────────────────────────────────────────────────────────────────

INNER_EMAS = [8, 13, 21]          # 内隧道（快速）
OUTER_EMAS = [55, 144, 233]       # 外隧道（慢速）- 精简版，去掉冗余的169/288/338
OUTER_EMAS_FULL = [55, 144, 169, 288, 338]  # 完整外隧道（数据充足时可选用）

INNER_COLORS = {
    8:  "#FF6B35",   # 橙红
    13: "#FFA500",   # 橙
    21: "#FFD700",   # 金黄
}

OUTER_COLORS = {
    55:  "#00B4D8",  # 亮蓝
    144: "#0077B6",  # 深蓝
    233: "#48CAE4",  # 浅蓝绿（新，替代169）
    169: "#48CAE4",  # 浅蓝绿（兼容旧数据）
    288: "#52B788",  # 绿（兼容旧数据）
    338: "#40916C",  # 深绿（兼容旧数据）
}

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_COLOR = "#FFD700"   # 金色

DARK_BG     = "#1a1a2e"
DARK_PAPER  = "#16213e"
DARK_CARD   = "#0f3460"
GRID_COLOR  = "#1e2d4d"
UP_COLOR    = "#F6465D"   # A股习惯：涨=红
DOWN_COLOR  = "#0ECB81"   # 跌=绿


# ─── 指标计算 ────────────────────────────────────────────────────────────────

def calc_emas(df: pd.DataFrame) -> pd.DataFrame:
    """计算所有 EMA，新列命名为 EMA_{n}"""
    close = df["Close"]
    for n in INNER_EMAS + OUTER_EMAS:
        df[f"EMA_{n}"] = close.ewm(span=n, adjust=False).mean()
    return df


def calc_macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> pd.DataFrame:
    close = df["Close"]
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    df["MACD"]        = ema_fast - ema_slow
    df["MACD_signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
    df["MACD_hist"]   = df["MACD"] - df["MACD_signal"]
    return df


def calc_rsi(df: pd.DataFrame, period=14) -> pd.DataFrame:
    delta = df["Close"].diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    # Pure-bull (no losses) → 100; flat market (no gains either) → 50 neutral
    avg_loss_safe = avg_loss.replace(0, np.nan)
    rs = avg_gain / avg_loss_safe
    df["RSI"] = 100 - (100 / (1 + rs))
    flat_mask = (avg_gain == 0) & (avg_loss == 0)
    df.loc[flat_mask, "RSI"] = 50.0
    df["RSI"] = df["RSI"].fillna(100.0)
    return df


def calc_fibonacci(df: pd.DataFrame) -> dict:
    """计算所选周期的斐波那契回撤位"""
    high = df["High"].max()
    low  = df["Low"].min()
    diff = high - low
    levels = {}
    for lvl in FIB_LEVELS:
        price = high - diff * lvl
        levels[lvl] = round(price, 4)
    return {"high": high, "low": low, "levels": levels}


def calc_bollinger(df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """
    计算布林带 (Bollinger Bands)
    - 中轨: period日简单移动平均线
    - 上轨: 中轨 + std_dev * 标准差
    - 下轨: 中轨 - std_dev * 标准差
    """
    close = df["Close"]
    df["BB_middle"] = close.rolling(window=period, min_periods=1).mean()
    rolling_std = close.rolling(window=period, min_periods=1).std()
    df["BB_upper"] = df["BB_middle"] + (rolling_std * std_dev)
    df["BB_lower"] = df["BB_middle"] - (rolling_std * std_dev)
    # 布林带宽度 (Bandwidth) = (上轨-下轨)/中轨
    df["BB_bandwidth"] = (df["BB_upper"] - df["BB_lower"]) / df["BB_middle"]
    # %B指标 = (收盘价-下轨)/(上轨-下轨)
    bb_range = df["BB_upper"] - df["BB_lower"]
    df["BB_percent"] = (close - df["BB_lower"]) / bb_range.replace(0, np.nan)
    return df


def adaptive_rsi_thresholds(df: pd.DataFrame, lookback: int = 60) -> dict:
    """
    自适应 RSI 阈值 — 根据近期波动率百分位动态调整超买超卖线
    高波动期放宽（减少假信号），低波动期收紧（提高灵敏度）

    返回 {"overbought": float, "oversold": float, "volatility_regime": str}
    """
    default = {"overbought": 70.0, "oversold": 30.0, "volatility_regime": "normal"}
    if df is None or df.empty or "Close" not in df.columns or len(df) < lookback:
        return default

    returns = df["Close"].pct_change().dropna()
    if len(returns) < lookback:
        return default

    current_vol = float(returns.tail(20).std())
    hist_vol = returns.tail(lookback).std()
    vol_pct = float((returns.rolling(20).std().rank(pct=True)).iloc[-1]) if len(returns) >= 20 else 0.5

    if vol_pct > 0.8:
        # 高波动：放宽到 80/20
        ob, os_val, regime = 80.0, 20.0, "high"
    elif vol_pct > 0.6:
        ob, os_val, regime = 75.0, 25.0, "above_avg"
    elif vol_pct < 0.2:
        # 低波动：收紧到 65/35
        ob, os_val, regime = 65.0, 35.0, "low"
    elif vol_pct < 0.4:
        ob, os_val, regime = 68.0, 32.0, "below_avg"
    else:
        ob, os_val, regime = 70.0, 30.0, "normal"

    return {"overbought": ob, "oversold": os_val, "volatility_regime": regime}


def calc_kdj(df: pd.DataFrame, n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
    """
    计算KDJ指标
    - RSV = (收盘价 - N日内最低价) / (N日内最高价 - N日内最低价) * 100
    - K = m1日RSV平滑 (EMA)
    - D = m2日K平滑 (EMA)
    - J = 3K - 2D
    """
    low_list = df["Low"].rolling(window=n, min_periods=1).min()
    high_list = df["High"].rolling(window=n, min_periods=1).max()
    # 当 high == low 时，价格区间没有变化，RSV设为50（中间值）
    price_range = high_list - low_list
    rsv = (df["Close"] - low_list) / price_range.replace(0, np.nan) * 100
    rsv = rsv.fillna(50)  # 当价格无变化时，RSV为50

    # 使用EMA平滑计算K值
    df["KDJ_K"] = rsv.ewm(com=m1 - 1, adjust=False).mean()
    # D值是K值的平滑
    df["KDJ_D"] = df["KDJ_K"].ewm(com=m2 - 1, adjust=False).mean()
    # J值 = 3K - 2D
    df["KDJ_J"] = 3 * df["KDJ_K"] - 2 * df["KDJ_D"]

    return df


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """计算ATR (Average True Range) 用于止损设置"""
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR"] = tr.ewm(span=period, adjust=False).mean()
    return df


def calc_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算VWAP (Volume Weighted Average Price) 成交量加权均价
    VWAP = 累计(typical_price * volume) / 累计(volume)
    typical_price = (High + Low + Close) / 3
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    df["VWAP"] = (typical_price * df["Volume"]).cumsum() / df["Volume"].cumsum()

    # 同时计算VWAP标准差带（类似布林带）
    df["VWAP_std"] = ((typical_price - df["VWAP"]) ** 2 * df["Volume"]).cumsum() / df["Volume"].cumsum()
    df["VWAP_std"] = df["VWAP_std"] ** 0.5

    df["VWAP_upper"] = df["VWAP"] + 2 * df["VWAP_std"]
    df["VWAP_lower"] = df["VWAP"] - 2 * df["VWAP_std"]

    return df


def calc_obv(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算OBV (On Balance Volume) 能量潮指标
    如果当日收盘价 > 前日收盘价，当日成交量为正值
    如果当日收盘价 < 前日收盘价，当日成交量为负值
    OBV = 前日OBV + 当日成交量（带符号）
    """
    diff = df["Close"].diff()
    signed = np.where(diff > 0, df["Volume"],
              np.where(diff < 0, -df["Volume"], 0))
    signed[0] = 0  # 首日 OBV = 0
    df["OBV"] = signed.cumsum()
    df["OBV_EMA"] = df["OBV"].ewm(span=20, adjust=False).mean()
    return df


def calc_volume_ma(df: pd.DataFrame, periods: list | None = None) -> pd.DataFrame:
    """计算成交量移动平均线"""
    if periods is None:
        periods = [5, 20, 60]
    for period in periods:
        df[f"Vol_MA_{period}"] = df["Volume"].rolling(window=period, min_periods=1).mean()
    return df


def calc_cci(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    计算CCI (Commodity Channel Index) 商品通道指数
    CCI = (TP - SMA_TP) / (0.015 * MAD)
    TP = (High + Low + Close) / 3
    """
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    tp_sma = tp.rolling(window=period, min_periods=1).mean()
    tp_mad = tp.rolling(window=period, min_periods=1).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["CCI"] = (tp - tp_sma) / (0.015 * tp_mad.replace(0, np.nan))
    return df


def calc_wr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    计算WR (Williams %R) 威廉指标
    WR = (HH - Close) / (HH - LL) * -100
    HH = N日内最高价, LL = N日内最低价
    """
    hh = df["High"].rolling(window=period, min_periods=1).max()
    ll = df["Low"].rolling(window=period, min_periods=1).min()
    rng = (hh - ll).replace(0, np.nan)
    df["WR"] = ((hh - df["Close"]) / rng * -100).fillna(-50.0)
    return df


def calc_dmi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    计算DMI (Directional Movement Index) 趋向指标
    +DI = 100 * EMA(+DM) / ATR
    -DI = 100 * EMA(-DM) / ATR
    ADX = 100 * EMA(|+DI - -DI| / (+DI + -DI))
    """
    high_diff = df["High"].diff()
    low_diff = -df["Low"].diff()

    plus_dm = np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0)
    minus_dm = np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0)

    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"] - df["Close"].shift()).abs()
    ], axis=1).max(axis=1)

    atr = tr.ewm(span=period, adjust=False).mean()

    df["DI_plus"] = 100 * pd.Series(plus_dm, index=df.index).ewm(span=period, adjust=False).mean() / atr
    df["DI_minus"] = 100 * pd.Series(minus_dm, index=df.index).ewm(span=period, adjust=False).mean() / atr

    di_diff = (df["DI_plus"] - df["DI_minus"]).abs()
    di_sum = df["DI_plus"] + df["DI_minus"]
    df["ADX"] = 100 * (di_diff / di_sum.replace(0, np.nan)).ewm(span=period, adjust=False).mean()

    return df


def calc_sar(df: pd.DataFrame, af_start: float = 0.02, af_max: float = 0.2) -> pd.DataFrame:
    """
    计算SAR (Stop And Reverse) 抛物线转向指标
    简化算法实现
    """
    n = len(df)
    sar = [df["Low"].iloc[0]]
    ep = df["High"].iloc[0]  # 极值点
    af = af_start
    trend = 1  # 1为上升，-1为下降

    for i in range(1, n):
        prev_sar = sar[-1]
        current_sar = prev_sar + af * (ep - prev_sar)

        if trend == 1:  # 上升趋势
            if df["Low"].iloc[i] < current_sar:  # 反转
                trend = -1
                current_sar = ep
                ep = df["Low"].iloc[i]
                af = af_start
            else:
                if df["High"].iloc[i] > ep:
                    ep = df["High"].iloc[i]
                    af = min(af + af_start, af_max)
        else:  # 下降趋势
            if df["High"].iloc[i] > current_sar:  # 反转
                trend = 1
                current_sar = ep
                ep = df["High"].iloc[i]
                af = af_start
            else:
                if df["Low"].iloc[i] < ep:
                    ep = df["Low"].iloc[i]
                    af = min(af + af_start, af_max)

        sar.append(current_sar)

    df["SAR"] = sar
    return df


def calc_momentum(df: pd.DataFrame, period: int = 10) -> pd.DataFrame:
    """
    计算Momentum (动量指标)
    MOM = Close - Close_n_periods_ago
    """
    df["MOM"] = df["Close"] - df["Close"].shift(period)
    return df


def calc_stoch(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    """
    计算Stochastic Oscillator (随机指标)
    %K = (Close - LL) / (HH - LL) * 100
    %D = SMA(%K, d_period)
    """
    ll = df["Low"].rolling(window=k_period, min_periods=1).min()
    hh = df["High"].rolling(window=k_period, min_periods=1).max()
    df["STOCH_K"] = (df["Close"] - ll) / (hh - ll) * 100
    df["STOCH_D"] = df["STOCH_K"].rolling(window=d_period, min_periods=1).mean()
    return df


def calc_volatility(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """
    计算波动率指标
    """
    df["Volatility"] = df["Close"].pct_change().rolling(window=period, min_periods=1).std() * np.sqrt(252) * 100
    return df


def calc_psychological_line(df: pd.DataFrame, period: int = 12) -> pd.DataFrame:
    """
    计算PSY (心理线)
    PSY = N日内上涨天数 / N * 100
    """
    df["PSY"] = (df["Close"] > df["Close"].shift(1)).rolling(window=period, min_periods=1).sum() / period * 100
    return df


def calc_ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26,
                   senkou_b: int = 52, displacement: int = 26) -> pd.DataFrame:
    """
    一目均衡表 (Ichimoku Kinko Hyo)
    转换线(Tenkan)、基准线(Kijun)、先行带A/B(SpanA/SpanB)、迟行线(Chikou)
    """
    high = df["High"]
    low = df["Low"]

    # 转换线 = (最近9日最高价 + 最近9日最低价) / 2
    tenkan_high = high.rolling(window=tenkan, min_periods=1).max()
    tenkan_low = low.rolling(window=tenkan, min_periods=1).min()
    df["Ichimoku_Tenkan"] = (tenkan_high + tenkan_low) / 2

    # 基准线 = (最近26日最高价 + 最近26日最低价) / 2
    kijun_high = high.rolling(window=kijun, min_periods=1).max()
    kijun_low = low.rolling(window=kijun, min_periods=1).min()
    df["Ichimoku_Kijun"] = (kijun_high + kijun_low) / 2

    # 先行带A = (转换线 + 基准线) / 2，前移 displacement 根
    df["Ichimoku_SpanA"] = ((df["Ichimoku_Tenkan"] + df["Ichimoku_Kijun"]) / 2).shift(displacement)

    # 先行带B = (最近52日最高价 + 最近52日最低价) / 2，前移 displacement 根
    senkou_b_high = high.rolling(window=senkou_b, min_periods=1).max()
    senkou_b_low = low.rolling(window=senkou_b, min_periods=1).min()
    df["Ichimoku_SpanB"] = ((senkou_b_high + senkou_b_low) / 2).shift(displacement)

    # 迟行线 = 收盘价回退 displacement 根
    df["Ichimoku_Chikou"] = df["Close"].shift(-displacement)

    # 云层厚度 = SpanA - SpanB（正值=阳云/多头，负值=阴云/空头）
    df["Ichimoku_CloudThickness"] = df["Ichimoku_SpanA"] - df["Ichimoku_SpanB"]
    # 云层厚度百分比（相对于价格，便于跨股票比较）
    df["Ichimoku_CloudPct"] = df["Ichimoku_CloudThickness"] / df["Close"] * 100

    return df


def calc_td_sequential(df: pd.DataFrame) -> pd.DataFrame:
    """
    TD Sequential (Tom DeMark 序列)
    Setup: 连续收盘价 > 或 < 4根前收盘价的计数 (1-9)
    Countdown: Setup 9 完成后，满足条件的K线计数到 13，产生更强确认信号
      - 买 Countdown: Close <= 2根前的 Low（条件更宽松，不要求连续）
      - 卖 Countdown: Close >= 2根前的 High
    """
    n = len(df)
    close = df["Close"].values
    high = df["High"].values
    low = df["Low"].values

    td_setup = np.zeros(n, dtype=int)
    td_signal = np.zeros(n, dtype=int)
    td_countdown = np.zeros(n, dtype=int)  # 正=买countdown, 负=卖countdown

    buy_count = 0   # 连续 Close < Close[i-4] 的计数（卖方衰竭->买信号）
    sell_count = 0  # 连续 Close > Close[i-4] 的计数（买方衰竭->卖信号）

    # Countdown 状态
    buy_cd_active = False
    buy_cd_count = 0
    sell_cd_active = False
    sell_cd_count = 0

    for i in range(4, n):
        # ── Setup 阶段 ──
        if close[i] < close[i - 4]:
            buy_count += 1
            sell_count = 0
        elif close[i] > close[i - 4]:
            sell_count += 1
            buy_count = 0
        else:
            buy_count = 0
            sell_count = 0

        if buy_count > 0:
            td_setup[i] = buy_count
            if buy_count == 9:
                td_signal[i] = 1  # Setup 买信号
                buy_count = 0
                # 激活买 Countdown
                buy_cd_active = True
                buy_cd_count = 0
                # 新的买Setup完成时取消对立的卖Countdown
                sell_cd_active = False
                sell_cd_count = 0
        elif sell_count > 0:
            td_setup[i] = -sell_count
            if sell_count == 9:
                td_signal[i] = -1  # Setup 卖信号
                sell_count = 0
                # 激活卖 Countdown
                sell_cd_active = True
                sell_cd_count = 0
                buy_cd_active = False
                buy_cd_count = 0

        # ── Countdown 阶段（不要求连续，只要满足条件就计数）──
        if buy_cd_active and i >= 2:
            if close[i] <= low[i - 2]:
                buy_cd_count += 1
                td_countdown[i] = buy_cd_count
                if buy_cd_count == 13:
                    td_signal[i] = 2  # Countdown 买确认（比 Setup 信号更强）
                    buy_cd_active = False
                    buy_cd_count = 0

        if sell_cd_active and i >= 2:
            if close[i] >= high[i - 2]:
                sell_cd_count += 1
                td_countdown[i] = -sell_cd_count
                if sell_cd_count == 13:
                    td_signal[i] = -2  # Countdown 卖确认
                    sell_cd_active = False
                    sell_cd_count = 0

    df["TD_Setup"] = td_setup
    df["TD_Signal"] = td_signal
    df["TD_Countdown"] = td_countdown

    return df


def calc_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Heikin-Ashi 平均K线
    HA_Close = (O+H+L+C)/4
    HA_Open = (prev_HA_Open + prev_HA_Close)/2
    HA_High = max(H, HA_Open, HA_Close)
    HA_Low = min(L, HA_Open, HA_Close)
    """
    ha_close = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4

    ha_open = np.zeros(len(df))
    ha_open[0] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i - 1] + ha_close.iloc[i - 1]) / 2

    ha_open_s = pd.Series(ha_open, index=df.index)
    ha_high = pd.concat([df["High"], ha_open_s, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([df["Low"], ha_open_s, ha_close], axis=1).min(axis=1)

    df["HA_Open"] = ha_open_s
    df["HA_High"] = ha_high
    df["HA_Low"] = ha_low
    df["HA_Close"] = ha_close

    return df


def calc_multi_timeframe_trend(df_daily: pd.DataFrame) -> dict:
    """
    多周期趋势确认 — 用日线数据模拟周线/月线级别 EMA 方向
    返回 {"weekly_bull": bool, "monthly_bull": bool, "mtf_score": int}
    mtf_score: +2 全周期共振多头, +1 周线多头, 0 中性, -1 周线空头, -2 全周期共振空头
    """
    result = {"weekly_bull": None, "monthly_bull": None, "mtf_score": 0}
    if df_daily is None or df_daily.empty or len(df_daily) < 120:
        return result

    close = df_daily["Close"]

    # 周线级别：EMA_21(日) ≈ EMA_4~5(周)，EMA_55(日) ≈ EMA_11(周)
    # 用日线 EMA_21 vs EMA_55 判断周线方向
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema55 = close.ewm(span=55, adjust=False).mean()
    weekly_bull = float(ema21.iloc[-1]) > float(ema55.iloc[-1])
    result["weekly_bull"] = weekly_bull

    # 月线级别：EMA_55(日) vs EMA_144(日) ≈ 月线 EMA_3 vs EMA_7
    if len(close) >= 200:
        ema144 = close.ewm(span=144, adjust=False).mean()
        monthly_bull = float(ema55.iloc[-1]) > float(ema144.iloc[-1])
        result["monthly_bull"] = monthly_bull
    else:
        monthly_bull = None
        result["monthly_bull"] = None

    # 多周期共振评分
    score = 0
    if weekly_bull:
        score += 1
    elif weekly_bull is False:
        score -= 1
    if monthly_bull is True:
        score += 1
    elif monthly_bull is False:
        score -= 1
    result["mtf_score"] = score

    return result


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """添加所有技术指标"""
    df = df.copy()
    df = calc_emas(df)
    df = calc_macd(df)
    df = calc_rsi(df)
    df = calc_bollinger(df)
    df = calc_kdj(df)
    df = calc_atr(df)
    df = calc_vwap(df)
    df = calc_obv(df)
    df = calc_volume_ma(df)
    df = calc_cci(df)
    df = calc_wr(df)
    df = calc_dmi(df)
    df = calc_sar(df)
    df = calc_momentum(df)
    df = calc_stoch(df)
    df = calc_volatility(df)
    df = calc_psychological_line(df)
    df = calc_ichimoku(df)
    df = calc_td_sequential(df)
    df = calc_heikin_ashi(df)
    return df


# ─── 指标配置类 ───────────────────────────────────────────────────────────────

class IndicatorConfig:
    """图表指标配置类"""
    def __init__(self):
        # 主图指标开关
        self.show_ema_inner = True
        self.show_ema_outer = True
        self.show_bollinger = True
        self.show_vwap = True
        self.show_sar = False
        self.show_fibonacci = True
        self.show_ichimoku = False
        self.show_td_sequential = False
        self.show_elliott_wave = False

        # K线类型: "candle" | "heikin_ashi"
        self.chart_type = "candle"

        # 副图指标选择
        self.secondary_indicator_1 = "MACD"  # 副图1: MACD/RSI/KDJ/CCI/WR/DMI/MOM
        self.secondary_indicator_2 = "KDJ"   # 副图2
        self.secondary_indicator_3 = "VOL"   # 副图3: VOL/OBV
        self.secondary_indicator_4 = "OBV"   # 副图4

    def to_dict(self):
        return {
            "show_ema_inner": self.show_ema_inner,
            "show_ema_outer": self.show_ema_outer,
            "show_bollinger": self.show_bollinger,
            "show_vwap": self.show_vwap,
            "show_sar": self.show_sar,
            "show_fibonacci": self.show_fibonacci,
            "show_ichimoku": self.show_ichimoku,
            "show_td_sequential": self.show_td_sequential,
            "show_elliott_wave": self.show_elliott_wave,
            "chart_type": self.chart_type,
            "secondary_1": self.secondary_indicator_1,
            "secondary_2": self.secondary_indicator_2,
            "secondary_3": self.secondary_indicator_3,
            "secondary_4": self.secondary_indicator_4,
        }


# ─── Plotly 图表构建 ─────────────────────────────────────────────────────────

def build_chart(df: pd.DataFrame, ticker: str = "", period: str = "",
                config: IndicatorConfig = None) -> go.Figure:
    """
    构建完整技术分析图（5子图）
    支持通过config参数控制各指标的显示
    """
    if config is None:
        config = IndicatorConfig()

    df = add_all_indicators(df)
    fib = calc_fibonacci(df)

    fig = make_subplots(
        rows=5, cols=1,
        shared_xaxes=True,
        row_heights=[0.50, 0.15, 0.12, 0.12, 0.11],
        vertical_spacing=0.015,
        subplot_titles=("", config.secondary_indicator_1, config.secondary_indicator_2,
                       config.secondary_indicator_3, config.secondary_indicator_4),
    )

    # ── 主图：K线（支持普通/Heikin-Ashi切换）─────────────────────────────
    if config.chart_type == "heikin_ashi" and "HA_Open" in df.columns:
        _o, _h, _l, _c = df["HA_Open"], df["HA_High"], df["HA_Low"], df["HA_Close"]
        _name = "平均K线"
    else:
        _o, _h, _l, _c = df["Open"], df["High"], df["Low"], df["Close"]
        _name = "K线"
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=_o, high=_h, low=_l, close=_c,
            increasing_line_color=UP_COLOR,
            decreasing_line_color=DOWN_COLOR,
            increasing_fillcolor=UP_COLOR,
            decreasing_fillcolor=DOWN_COLOR,
            name=_name,
            showlegend=False,
        ),
        row=1, col=1,
    )

    # ── EMA外隧道 ────────────────────────────────────────────────────────
    if config.show_ema_outer:
        # 外隧道填充带
        ema_outer_min = f"EMA_{OUTER_EMAS[0]}"   # EMA55
        ema_outer_max = f"EMA_{OUTER_EMAS[-1]}"  # EMA338
        _add_fill_band(fig, df, ema_outer_min, ema_outer_max,
                       fill_color="rgba(0,119,182,0.12)", row=1)

        # 外隧道EMA线
        for n in OUTER_EMAS:
            col_name = f"EMA_{n}"
            if col_name in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df.index, y=df[col_name],
                        mode="lines",
                        line=dict(color=OUTER_COLORS[n], width=1),
                        name=f"EMA{n}",
                        opacity=0.85,
                    ),
                    row=1, col=1,
                )

    # ── EMA内隧道 ────────────────────────────────────────────────────────
    if config.show_ema_inner:
        # 内隧道填充带
        ema_inner_min = f"EMA_{INNER_EMAS[0]}"   # EMA8
        ema_inner_max = f"EMA_{INNER_EMAS[-1]}"  # EMA21
        _add_fill_band(fig, df, ema_inner_min, ema_inner_max,
                       fill_color="rgba(255,165,0,0.15)", row=1)

        # 内隧道EMA线
        for n in INNER_EMAS:
            col_name = f"EMA_{n}"
            if col_name in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df.index, y=df[col_name],
                        mode="lines",
                        line=dict(color=INNER_COLORS[n], width=1.5),
                        name=f"EMA{n}",
                        opacity=0.9,
                    ),
                    row=1, col=1,
                )

    # ── 布林带 ────────────────────────────────────────────────────────────
    if config.show_bollinger:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["BB_upper"],
                mode="lines",
                line=dict(color="rgba(255,255,255,0.3)", width=1),
                name="布林上轨",
                showlegend=True,
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["BB_lower"],
                mode="lines",
                line=dict(color="rgba(255,255,255,0.3)", width=1),
                fill="tonexty",
                fillcolor="rgba(255,255,255,0.05)",
                name="布林下轨",
                showlegend=True,
            ),
            row=1, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["BB_middle"],
                mode="lines",
                line=dict(color="rgba(255,255,255,0.5)", width=1, dash="dash"),
                name="布林中轨",
                showlegend=True,
            ),
            row=1, col=1,
        )

    # ── VWAP ──────────────────────────────────────────────────────────────
    if config.show_vwap:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["VWAP"],
                mode="lines",
                line=dict(color="#9C27B0", width=1.5, dash="dot"),  # 紫色虚线
                name="VWAP",
                showlegend=True,
            ),
            row=1, col=1,
        )

    # ── SAR ───────────────────────────────────────────────────────────────
    if config.show_sar:
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["SAR"],
                mode="markers",
                marker=dict(color="#FFA500", size=3, symbol="diamond"),
                name="SAR",
                showlegend=True,
            ),
            row=1, col=1,
        )

    # ── 一目均衡表 (Ichimoku Cloud) ─────────────────────────────────────
    if config.show_ichimoku and "Ichimoku_Tenkan" in df.columns:
        # 云带填充：SpanA vs SpanB
        span_a = df["Ichimoku_SpanA"]
        span_b = df["Ichimoku_SpanB"]
        # 添加 SpanA 线（透明，用于填充基础）
        fig.add_trace(
            go.Scatter(
                x=df.index, y=span_a,
                mode="lines",
                line=dict(color="rgba(76,175,80,0.4)", width=0),
                name="先行带A",
                showlegend=False,
            ),
            row=1, col=1,
        )
        # SpanB 线，与SpanA间填充
        fig.add_trace(
            go.Scatter(
                x=df.index, y=span_b,
                mode="lines",
                line=dict(color="rgba(244,67,54,0.4)", width=0),
                fill="tonexty",
                fillcolor="rgba(76,175,80,0.08)",
                name="先行带B",
                showlegend=False,
            ),
            row=1, col=1,
        )
        # 转换线 (Tenkan)
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["Ichimoku_Tenkan"],
                mode="lines",
                line=dict(color="#26A69A", width=1.2),
                name="转换线(9)",
                opacity=0.9,
            ),
            row=1, col=1,
        )
        # 基准线 (Kijun)
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["Ichimoku_Kijun"],
                mode="lines",
                line=dict(color="#EF5350", width=1.2),
                name="基准线(26)",
                opacity=0.9,
            ),
            row=1, col=1,
        )
        # 迟行线 (Chikou) - 只画非NaN部分
        chikou = df["Ichimoku_Chikou"].dropna()
        if len(chikou) > 0:
            fig.add_trace(
                go.Scatter(
                    x=chikou.index, y=chikou,
                    mode="lines",
                    line=dict(color="#AB47BC", width=1, dash="dot"),
                    name="迟行线",
                    opacity=0.7,
                ),
                row=1, col=1,
            )

    # ── TD Sequential 标注 ───────────────────────────────────────────────
    if config.show_td_sequential and "TD_Signal" in df.columns:
        td_signals = df["TD_Signal"]
        td_setup = df["TD_Setup"]
        for i in range(len(df)):
            sig = td_signals.iloc[i]
            if sig == 1:  # 买信号 (卖方衰竭完成9)
                fig.add_annotation(
                    x=df.index[i], y=df["Low"].iloc[i],
                    text="9", showarrow=False,
                    font=dict(color="#4CAF50", size=11, family="Arial Black"),
                    yshift=-14, row=1, col=1,
                )
            elif sig == -1:  # 卖信号 (买方衰竭完成9)
                fig.add_annotation(
                    x=df.index[i], y=df["High"].iloc[i],
                    text="9", showarrow=False,
                    font=dict(color="#F44336", size=11, family="Arial Black"),
                    yshift=14, row=1, col=1,
                )
            else:
                # 显示计数 7, 8（接近完成时标注）
                setup = td_setup.iloc[i]
                if setup in (7, 8):
                    fig.add_annotation(
                        x=df.index[i], y=df["Low"].iloc[i],
                        text=str(setup), showarrow=False,
                        font=dict(color="rgba(76,175,80,0.5)", size=8),
                        yshift=-10, row=1, col=1,
                    )
                elif setup in (-7, -8):
                    fig.add_annotation(
                        x=df.index[i], y=df["High"].iloc[i],
                        text=str(abs(setup)), showarrow=False,
                        font=dict(color="rgba(244,67,54,0.5)", size=8),
                        yshift=10, row=1, col=1,
                    )

    # ── Elliott波浪标注 ────────────────────────────────────────────────────
    if config.show_elliott_wave:
        try:
            from .elliott_wave import detect_elliott_waves
            ew_data = detect_elliott_waves(df)
            wave_labels = ew_data.get("wave_labels", [])

            if wave_labels:
                # 分离推动浪与修正浪标签
                impulse_pts = [w for w in wave_labels if "impulse" in w.get("wave_type", "")]
                corrective_pts = [w for w in wave_labels if "corrective" in w.get("wave_type", "")]

                # 推动浪连线: 金色实线
                if len(impulse_pts) >= 2:
                    fig.add_trace(
                        go.Scatter(
                            x=[df.index[w["index"]] for w in impulse_pts if w["index"] < len(df)],
                            y=[w["price"] for w in impulse_pts if w["index"] < len(df)],
                            mode="lines+markers",
                            line=dict(color="#FFB300", width=2),
                            marker=dict(size=7, color="#FFB300", symbol="diamond"),
                            name="推动浪",
                            opacity=0.9,
                        ),
                        row=1, col=1,
                    )

                # 修正浪连线: 紫色虚线
                if len(corrective_pts) >= 2:
                    fig.add_trace(
                        go.Scatter(
                            x=[df.index[w["index"]] for w in corrective_pts if w["index"] < len(df)],
                            y=[w["price"] for w in corrective_pts if w["index"] < len(df)],
                            mode="lines+markers",
                            line=dict(color="#AB47BC", width=2, dash="dash"),
                            marker=dict(size=7, color="#AB47BC", symbol="diamond"),
                            name="修正浪",
                            opacity=0.9,
                        ),
                        row=1, col=1,
                    )

                # 浪标签
                for w in wave_labels:
                    if w["index"] >= len(df):
                        continue
                    is_high = w.get("pivot_type") == "high"
                    yshift = 18 if is_high else -18
                    color = "#FFB300" if "impulse" in w.get("wave_type", "") else "#AB47BC"
                    fig.add_annotation(
                        x=df.index[w["index"]],
                        y=w["price"],
                        text=w["label"],
                        showarrow=False,
                        font=dict(color=color, size=13, family="Arial Black"),
                        yshift=yshift,
                        row=1, col=1,
                    )

            # 预测目标位: 水平虚线
            targets = ew_data.get("projected_targets", [])
            for t in targets[:3]:
                t_color = "#0ECB81" if t.get("direction") == "up" else "#F6465D"
                fig.add_hline(
                    y=t["price"],
                    line_dash="dot",
                    line_color=t_color,
                    line_width=1,
                    annotation_text=f"{t['label']} ({t['price']:.2f})",
                    annotation_font_color=t_color,
                    annotation_font_size=10,
                    row=1, col=1,
                )
        except Exception as _ew_err:
            pass  # 波浪检测失败时静默跳过

    # ── 斐波那契回撤线 ────────────────────────────────────────────────────
    if config.show_fibonacci:
        x_start = df.index[0]
        x_end   = df.index[-1]
        fib_label = {
            0.0:   "0%",
            0.236: "23.6%",
            0.382: "38.2%",
            0.5:   "50%",
            0.618: "61.8%",
            0.786: "78.6%",
            1.0:   "100%",
        }
        for lvl, price in fib["levels"].items():
            fig.add_shape(
                type="line",
                x0=x_start, x1=x_end,
                y0=price, y1=price,
                line=dict(color=FIB_COLOR, width=0.8, dash="dot"),
                row=1, col=1,
            )
            fig.add_annotation(
                x=x_end, y=price,
                text=f"  {fib_label[lvl]} {price:.2f}",
                showarrow=False,
                font=dict(color=FIB_COLOR, size=9),
                xanchor="left",
                row=1, col=1,
            )

    # ── 副图1 ──────────────────────────────────────────────────────────────
    _add_secondary_indicator(fig, df, config.secondary_indicator_1, row=2)

    # ── 副图2 ──────────────────────────────────────────────────────────────
    _add_secondary_indicator(fig, df, config.secondary_indicator_2, row=3)

    # ── 副图3 ──────────────────────────────────────────────────────────────
    _add_secondary_indicator(fig, df, config.secondary_indicator_3, row=4)

    # ── 副图4 ──────────────────────────────────────────────────────────────
    _add_secondary_indicator(fig, df, config.secondary_indicator_4, row=5)

    # ── 全局布局 ──────────────────────────────────────────────────────────
    title = f"{ticker}  技术分析  ({period})"
    fig.update_layout(
        title=dict(text=title, font=dict(color="#E0E0E0", size=14)),
        paper_bgcolor=DARK_PAPER,
        plot_bgcolor=DARK_BG,
        font=dict(color="#C0C0C0", size=10),
        xaxis_rangeslider_visible=False,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            font=dict(color="#C0C0C0", size=9),
            orientation="h",
            y=1.02,
        ),
        margin=dict(l=60, r=120, t=60, b=40),
        height=900,
    )

    # 所有子图统一暗色坐标轴
    for i in range(1, 6):
        fig.update_xaxes(
            showgrid=True, gridcolor=GRID_COLOR,
            zeroline=False, showline=False,
            tickfont=dict(color="#888"),
            row=i, col=1,
        )
        fig.update_yaxes(
            showgrid=True, gridcolor=GRID_COLOR,
            zeroline=False, showline=False,
            tickfont=dict(color="#888"),
            row=i, col=1,
        )

    # 子图标题颜色
    for ann in fig.layout.annotations:
        ann.font.color = "#888"
        ann.font.size  = 10

    return fig


def annotate_patterns_on_chart(fig: go.Figure, df: pd.DataFrame, analysis: dict) -> go.Figure:
    """
    在图表上标注K线形态和背离信号
    """
    if not analysis or fig is None or df is None or df.empty:
        return fig

    n = len(df)
    dates = df.index if hasattr(df.index, 'strftime') else list(range(n))

    # K线形态标注
    candle_patterns = analysis.get("candlestick_patterns", [])
    for cp in candle_patterns[:5]:
        bar_idx = cp.get("bar_index", 0)
        actual_idx = n + bar_idx  # bar_index is negative
        if 0 <= actual_idx < n:
            direction = cp.get("direction", "")
            if direction == "看涨":
                y_pos = df["Low"].iloc[actual_idx] * 0.998
                symbol = "triangle-up"
                color = "#0ECB81"
            elif direction == "看跌":
                y_pos = df["High"].iloc[actual_idx] * 1.002
                symbol = "triangle-down"
                color = "#F6465D"
            else:
                y_pos = df["Close"].iloc[actual_idx]
                symbol = "diamond"
                color = "#FFD700"

            fig.add_trace(go.Scatter(
                x=[dates[actual_idx]],
                y=[y_pos],
                mode="markers",
                marker=dict(symbol=symbol, size=10, color=color, line=dict(width=1, color="#fff")),
                name=cp.get("name_cn", ""),
                hovertext=f"{cp.get('name_cn', '')} ({cp.get('direction', '')})<br>{cp.get('description', '')[:30]}",
                hoverinfo="text",
                showlegend=False,
            ), row=1, col=1)

    # 背离连线标注
    div_summary = analysis.get("divergence_summary", {})
    active_divs = div_summary.get("active_divergences", []) if div_summary else []
    for d in active_divs[:2]:
        start_idx = d.get("start_idx", 0)
        end_idx = d.get("end_idx", 0)
        if 0 <= start_idx < n and 0 <= end_idx < n:
            div_type = d.get("type", "")
            color = "#F6465D" if "顶" in div_type else "#0ECB81"

            # 价格连线
            if "顶" in div_type or "high" in d.get("type_en", ""):
                y1 = df["High"].iloc[start_idx]
                y2 = df["High"].iloc[end_idx]
            else:
                y1 = df["Low"].iloc[start_idx]
                y2 = df["Low"].iloc[end_idx]

            fig.add_trace(go.Scatter(
                x=[dates[start_idx], dates[end_idx]],
                y=[y1, y2],
                mode="lines",
                line=dict(color=color, width=2, dash="dash"),
                name=f"{d.get('indicator', '')} {div_type}",
                hovertext=f"{d.get('indicator', '')} {div_type}",
                hoverinfo="text",
                showlegend=False,
            ), row=1, col=1)

    return fig


def _add_secondary_indicator(fig: go.Figure, df: pd.DataFrame, indicator: str, row: int):
    """添加副图指标"""

    if indicator == "MACD":
        colors_hist = [UP_COLOR if v >= 0 else DOWN_COLOR for v in df["MACD_hist"].fillna(0)]
        fig.add_trace(
            go.Bar(
                x=df.index, y=df["MACD_hist"],
                marker_color=colors_hist,
                name="MACD柱",
                showlegend=False,
            ),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["MACD"],
                       line=dict(color="#00B4D8", width=1),
                       name="MACD", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["MACD_signal"],
                       line=dict(color="#FFA500", width=1),
                       name="Signal", showlegend=False),
            row=row, col=1,
        )

    elif indicator == "RSI":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["RSI"],
                       line=dict(color="#00B4D8", width=1.2),
                       name="RSI", showlegend=False),
            row=row, col=1,
        )
        # 超买超卖线
        for level, color in [(70, "rgba(246,70,93,0.4)"), (30, "rgba(14,203,129,0.4)")]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color=color, line_width=1, row=row, col=1)

    elif indicator == "KDJ":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["KDJ_K"],
                       line=dict(color="#00B4D8", width=1.2),
                       name="K值", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["KDJ_D"],
                       line=dict(color="#FFA500", width=1.2),
                       name="D值", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["KDJ_J"],
                       line=dict(color="#F6465D", width=1),
                       name="J值", showlegend=False),
            row=row, col=1,
        )
        for level, color in [(80, "rgba(246,70,93,0.4)"), (20, "rgba(14,203,129,0.4)")]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color=color, line_width=1, row=row, col=1)

    elif indicator == "CCI":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["CCI"],
                       line=dict(color="#00B4D8", width=1.2),
                       name="CCI", showlegend=False),
            row=row, col=1,
        )
        for level in [100, -100]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color="rgba(128,128,128,0.4)", line_width=1, row=row, col=1)

    elif indicator == "WR":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["WR"],
                       line=dict(color="#9C27B0", width=1.2),
                       name="WR", showlegend=False),
            row=row, col=1,
        )
        for level, color in [(-20, "rgba(246,70,93,0.4)"), (-80, "rgba(14,203,129,0.4)")]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color=color, line_width=1, row=row, col=1)

    elif indicator == "DMI":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["DI_plus"],
                       line=dict(color="#0ECB81", width=1.2),
                       name="+DI", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["DI_minus"],
                       line=dict(color="#F6465D", width=1.2),
                       name="-DI", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["ADX"],
                       line=dict(color="#FFA500", width=1),
                       name="ADX", showlegend=False),
            row=row, col=1,
        )

    elif indicator == "MOM":
        colors_mom = [UP_COLOR if v >= 0 else DOWN_COLOR for v in df["MOM"].fillna(0)]
        fig.add_trace(
            go.Bar(
                x=df.index, y=df["MOM"],
                marker_color=colors_mom,
                name="Momentum",
                showlegend=False,
            ),
            row=row, col=1,
        )

    elif indicator == "STOCH":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["STOCH_K"],
                       line=dict(color="#00B4D8", width=1.2),
                       name="%K", showlegend=False),
            row=row, col=1,
        )
        fig.add_trace(
            go.Scatter(x=df.index, y=df["STOCH_D"],
                       line=dict(color="#FFA500", width=1.2),
                       name="%D", showlegend=False),
            row=row, col=1,
        )
        for level, color in [(80, "rgba(246,70,93,0.4)"), (20, "rgba(14,203,129,0.4)")]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color=color, line_width=1, row=row, col=1)

    elif indicator == "VOL":
        vol_colors = [
            UP_COLOR if c >= o else DOWN_COLOR
            for c, o in zip(df["Close"], df["Open"])
        ]
        fig.add_trace(
            go.Bar(
                x=df.index, y=df["Volume"],
                marker_color=vol_colors,
                name="成交量",
                showlegend=False,
            ),
            row=row, col=1,
        )
        if "Vol_MA_5" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["Vol_MA_5"],
                    mode="lines",
                    line=dict(color="#FFA500", width=1),
                    name="Vol MA5",
                    showlegend=False,
                ),
                row=row, col=1,
            )
        if "Vol_MA_20" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["Vol_MA_20"],
                    mode="lines",
                    line=dict(color="#00B4D8", width=1),
                    name="Vol MA20",
                    showlegend=False,
                ),
                row=row, col=1,
            )

    elif indicator == "OBV":
        fig.add_trace(
            go.Scatter(
                x=df.index, y=df["OBV"],
                mode="lines",
                line=dict(color="#E0E0E0", width=1),
                name="OBV",
                showlegend=False,
            ),
            row=row, col=1,
        )
        if "OBV_EMA" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df.index, y=df["OBV_EMA"],
                    mode="lines",
                    line=dict(color="#FFA500", width=1),
                    name="OBV EMA20",
                    showlegend=False,
                ),
                row=row, col=1,
            )

    elif indicator == "PSY":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["PSY"],
                       line=dict(color="#00B4D8", width=1.2),
                       name="PSY", showlegend=False),
            row=row, col=1,
        )
        for level in [75, 25]:
            fig.add_hline(y=level, line_dash="dash",
                          line_color="rgba(128,128,128,0.4)", line_width=1, row=row, col=1)

    elif indicator == "VOLATILITY":
        fig.add_trace(
            go.Scatter(x=df.index, y=df["Volatility"],
                       line=dict(color="#FFA500", width=1.2),
                       name="波动率", showlegend=False),
            row=row, col=1,
        )


def _add_fill_band(fig, df, lower_col, upper_col, fill_color, row):
    """在两条 EMA 之间填充半透明色带"""
    if lower_col not in df.columns or upper_col not in df.columns:
        return
    # 上边界（透明线）
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df[upper_col],
            mode="lines",
            line=dict(width=0),
            showlegend=False,
            hoverinfo="skip",
        ),
        row=row, col=1,
    )
    # 下边界（填充到上边界）
    fig.add_trace(
        go.Scatter(
            x=df.index, y=df[lower_col],
            mode="lines",
            line=dict(width=0),
            fill="tonexty",
            fillcolor=fill_color,
            showlegend=False,
            hoverinfo="skip",
        ),
        row=row, col=1,
    )


# ── 买卖信号标注 ─────────────────────────────────────────────────────

def annotate_buy_sell_signals(fig: go.Figure, df, signals: dict) -> go.Figure:
    """
    在图表上标注买卖箭头
    signals: generate_composite_signals() 的返回值
    """
    if not signals:
        return fig

    buy_sigs = signals.get("buy_signals", [])
    sell_sigs = signals.get("sell_signals", [])

    # 买入信号 - 绿色上三角
    if buy_sigs:
        size_map = {"强": 14, "中": 10, "弱": 7}
        for sig in buy_sigs:
            idx = sig.get("bar_index", 0)
            if 0 <= idx < len(df):
                x_val = df.index[idx]
                y_val = sig.get("price", df["Low"].iloc[idx])
                strength = sig.get("strength", "中")
                confs = sig.get("confirmations", [])
                hover = f"买入信号({strength})<br>{'<br>'.join(confs)}"

                fig.add_trace(
                    go.Scatter(
                        x=[x_val], y=[y_val * 0.997],
                        mode="markers+text",
                        marker=dict(
                            symbol="triangle-up",
                            size=size_map.get(strength, 10),
                            color="#26A69A",
                            line=dict(width=1, color="#fff"),
                        ),
                        text=["B"] if strength == "强" else [""],
                        textposition="bottom center",
                        textfont=dict(size=8, color="#26A69A"),
                        hovertext=hover,
                        hoverinfo="text",
                        showlegend=False,
                    ),
                    row=1, col=1,
                )

    # 卖出信号 - 红色下三角
    if sell_sigs:
        size_map = {"强": 14, "中": 10, "弱": 7}
        for sig in sell_sigs:
            idx = sig.get("bar_index", 0)
            if 0 <= idx < len(df):
                x_val = df.index[idx]
                y_val = sig.get("price", df["High"].iloc[idx])
                strength = sig.get("strength", "中")
                confs = sig.get("confirmations", [])
                hover = f"卖出信号({strength})<br>{'<br>'.join(confs)}"

                fig.add_trace(
                    go.Scatter(
                        x=[x_val], y=[y_val * 1.003],
                        mode="markers+text",
                        marker=dict(
                            symbol="triangle-down",
                            size=size_map.get(strength, 10),
                            color="#EF5350",
                            line=dict(width=1, color="#fff"),
                        ),
                        text=["S"] if strength == "强" else [""],
                        textposition="top center",
                        textfont=dict(size=8, color="#EF5350"),
                        hovertext=hover,
                        hoverinfo="text",
                        showlegend=False,
                    ),
                    row=1, col=1,
                )

    return fig


def annotate_sr_bands(fig: go.Figure, sr_levels: dict, current_price: float) -> go.Figure:
    """
    在图表上绘制水平支撑/压力带
    sr_levels: calc_daily_levels() 的返回值
    """
    if not sr_levels:
        return fig

    supports = sr_levels.get("all_supports", [])
    resistances = sr_levels.get("all_resistances", [])

    # 支撑位 - 绿色水平线
    for i, s in enumerate(supports[:4]):
        price = s.get("price", 0)
        method = s.get("method", "")
        strength = s.get("strength", "弱")
        dash = "solid" if strength == "强" else ("dash" if strength == "中" else "dot")
        width = 1.5 if strength == "强" else 1
        opacity = 0.8 if strength == "强" else (0.6 if strength == "中" else 0.4)

        fig.add_hline(
            y=price, line_dash=dash,
            line_color=f"rgba(38,166,154,{opacity})", line_width=width,
            annotation_text=f"S: {method} ({price:.2f})" if i < 2 else "",
            annotation_position="bottom left",
            annotation_font_size=9,
            annotation_font_color=f"rgba(38,166,154,{opacity})",
            row=1, col=1,
        )

    # 压力位 - 红色水平线
    for i, r in enumerate(resistances[:4]):
        price = r.get("price", 0)
        method = r.get("method", "")
        strength = r.get("strength", "弱")
        dash = "solid" if strength == "强" else ("dash" if strength == "中" else "dot")
        width = 1.5 if strength == "强" else 1
        opacity = 0.8 if strength == "强" else (0.6 if strength == "中" else 0.4)


        fig.add_hline(
            y=price, line_dash=dash,
            line_color=f"rgba(239,83,80,{opacity})", line_width=width,
            annotation_text=f"R: {method} ({price:.2f})" if i < 2 else "",
            annotation_position="top left",
            annotation_font_size=9,
            annotation_font_color=f"rgba(239,83,80,{opacity})",
            row=1, col=1,
        )

    return fig
