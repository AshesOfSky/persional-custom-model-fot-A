# 策略模型完整规格（实盘回测可直接实现）

本文档把当前 `custom-model` 项目的全部决策逻辑写成**与具体框架无关的规格**——任何回测引擎（vectorbt / backtrader / 自研 SQL+Python / Quantopian-like）都能照此实现并得到一致结果。

所有公式与阈值均为已落地代码的 1:1 复刻，与 `modules/*.py` 同步。

---

## 1. 输入数据

### 1.1 必备字段

每个标的需要按交易日提供：

| 字段 | 说明 | 单位 |
|---|---|---|
| `Open / High / Low / Close` | 不复权或前复权（前复权更接近实战） | 标的本币 |
| `Volume` | 成交量 | 股数 |
| `Date` | 交易日（datetime） | 索引 |

最少 60 根 K 线，推荐 ≥ 250 根（以便 EMA288 和波动率锥）。

### 1.2 元数据（每个标的）

| 字段 | 来源 | 用途 |
|---|---|---|
| `ticker` | 输入 | 板块识别 |
| `name` | 行情接口 | ST 识别 |
| `market` | 数据路由 | 无风险利率选择，取值 `A股 / 美股 / 港股 / 国内期货 / 美股期货 / 外汇 / 其他` |
| `market_cap_yi` | 行情快照（流通市值，单位"亿"） | 止损市值修正 |
| `dividend_yield` | 基本面接口 | 红利因子 |

### 1.3 板块判定（A 股）

```python
def get_board(ticker, name):
    code = ticker.replace(".SS","").replace(".SZ","").replace(".BJ","").upper()
    if "ST" in name.upper().replace(" ","") or "退" in name:
        return "ST"        # 不入场
    if not code.isdigit():
        return "其他"
    if code.startswith("688") or code.startswith("689"):
        return "科创板"
    if code.startswith("3"):
        return "创业板"
    if code.startswith("4") or code.startswith("8"):
        return "北交所"
    if code[:3] in ("600","601","603","605","000","001","002","003"):
        return "主板"
    return "其他"
```

---

## 2. 指标计算（一次性预处理）

所有指标对每根 K 线 `i` 都需算出。下面给的是封闭公式。

### 2.1 EMA 系列

```
EMA_n[i] = α × Close[i] + (1-α) × EMA_n[i-1]    其中 α = 2/(n+1)
EMA_n[0] = Close[0]
```

需要 8 / 13 / 21（内层）+ 55 / 144 / 169 / 288 / 338（外层）共 8 条。

### 2.2 MACD（12/26/9）

```
EMA_fast  = EMA(Close, 12)
EMA_slow  = EMA(Close, 26)
MACD      = EMA_fast - EMA_slow
MACD_sig  = EMA(MACD, 9)
MACD_hist = MACD - MACD_sig
```

### 2.3 RSI（14）

```
delta[i]   = Close[i] - Close[i-1]
gain[i]    = max(delta[i], 0)
loss[i]    = max(-delta[i], 0)
avg_gain   = EMA_wilder(gain, 14)         # com=13, adjust=False
avg_loss   = EMA_wilder(loss, 14)
RS         = avg_gain / avg_loss          # avg_loss==0 时设为 NaN
RSI        = 100 - 100 / (1+RS)

# 边界规则（v1.x 修复）：
if avg_gain == 0 and avg_loss == 0:
    RSI = 50      # 完全平盘 → 中性
elif avg_loss == 0:
    RSI = 100     # 纯多头 → 满分
```

### 2.4 KDJ（9, 3, 3）

```
LL = min(Low[i-8 .. i])
HH = max(High[i-8 .. i])
RSV = (HH-LL == 0) ? 50 : (Close[i] - LL) / (HH - LL) × 100
K   = EMA(RSV, com=2)
D   = EMA(K,   com=2)
J   = 3K - 2D
```

### 2.5 布林带（20, 2σ）

```
BB_mid    = SMA(Close, 20)
σ20       = STD(Close, 20)
BB_upper  = BB_mid + 2σ20
BB_lower  = BB_mid - 2σ20
BB_bw     = (BB_upper - BB_lower) / BB_mid
%B        = (Close - BB_lower) / max(BB_upper - BB_lower, ε)
```

### 2.6 ATR（14）

```
TR[i] = max(High-Low, |High-Close[i-1]|, |Low-Close[i-1]|)
ATR   = EMA(TR, 14)
```

### 2.7 WR / CCI / DMI / SAR / VWAP / OBV

WR/CCI/DMI/SAR 公式见 `modules/technical.py`。这些进入"指标解读"概率表，不直接驱动入场，回测可暂略。

### 2.8 量能均线

```
Vol_MA5  = SMA(Volume, 5)
Vol_MA20 = SMA(Volume, 20)
Vol_MA60 = SMA(Volume, 60)
```

---

## 3. 趋势强度评分（0-100）

每根 K 线计算一次，输出 `score`（0-100）和 `level`（5 档）。

### 3.1 公式

```
score = 45  # 基准（A 股略偏空，避免单维信号虚标"强势"）

# A. EMA ±15
if EMA_8 > EMA_13 > EMA_21:                # 内层多头
    score += 12
    if EMA_21 > EMA_55:                    # 内外联动
        score += 3
elif EMA_8 < EMA_13 < EMA_21:              # 内层空头
    score -= 12
    if EMA_21 < EMA_55:
        score -= 3

# B. MACD ±15
macd_bullish = (MACD[i] > MACD_sig[i]) and (MACD[i-1] <= MACD_sig[i-1])
macd_bearish = (MACD[i] < MACD_sig[i]) and (MACD[i-1] >= MACD_sig[i-1])
hist_expanding = abs(MACD_hist[i]) > abs(MACD_hist[i-1])
if macd_bullish:
    score += 12 + (3 if hist_expanding else 0)
elif macd_bearish:
    score -= 12 + (3 if hist_expanding else 0)

# C. KDJ ±10
kdj_strength = clip( (J[i] - 50) / 25 , -3, 3)        # 标量 -3..+3
score += int(kdj_strength * 2)
kdj_golden = (K[i] > D[i]) and (K[i-1] <= D[i-1]) and (K[i] < 50)   # 低位金叉
kdj_dead   = (K[i] < D[i]) and (K[i-1] >= D[i-1]) and (K[i] > 50)   # 高位死叉
if kdj_golden:
    score += 4
elif kdj_dead:
    score -= 4

# D. 布林 ±7
if Close > BB_upper:    score += 5
elif Close < BB_lower:  score -= 5
elif Close > BB_mid:    score += 2
else:                   score -= 2

# E. 量能 ±8（v2 新增）
price_up_5d = Close[i] > Close[i-5]
vol_ratio = mean(Volume[i-4..i]) / mean(Volume[i-19..i])
if price_up_5d and vol_ratio > 1.2:        score += 6   # 放量上涨
elif (not price_up_5d) and vol_ratio < 0.8: score += 2   # 缩量下跌（企稳）
elif price_up_5d and vol_ratio < 0.8:      score -= 4   # 缩量上涨（背离）
elif (not price_up_5d) and vol_ratio > 1.2: score -= 6   # 放量下跌

# F. 动量 ±8（v2 新增；提供基准时按相对强度）
mom_5  = Close[i]/Close[i-5]  - 1
mom_60 = Close[i]/Close[i-60] - 1
if benchmark_df is not None:
    bm_5  = Bench[i]/Bench[i-5]  - 1
    bm_60 = Bench[i]/Bench[i-60] - 1
    rel_5  = mom_5  - bm_5
    rel_60 = mom_60 - bm_60
    if rel_5  >  0.02: score += 5
    elif rel_5  < -0.02: score -= 5
    if rel_60 >  0.05: score += 3
    elif rel_60 < -0.05: score -= 3
else:
    if mom_5  >  0.03: score += 5
    elif mom_5  < -0.03: score -= 5
    if mom_60 >  0.10: score += 3
    elif mom_60 < -0.10: score -= 3

# G. 北向资金 ±7（v2 新增；可选）
if northbound_score is not None:           # -1..+1 标量
    score += round(clip(northbound_score, -1, 1) × 7)

score = clip(score, 0, 100)
```

### 3.2 档位

| score | level | 用途 |
|---|---|---|
| ≥ 80 | 强势多头 | 信号入场 + 持仓加码（可选） |
| 62-79 | 偏多 | 信号入场 |
| 38-61 | 中性震荡 | 观望（不入场） |
| 18-37 | 偏空 | 信号离场 |
| < 18 | 强势空头 | 强制离场 + 反向（如允许做空） |

---

## 4. 入场信号（"信号桶"投票制）

### 4.1 原始确认条件（每根 K 线判定）

**买入端（`_check_buy_conditions`）**

| ID | 条件 | 公式 |
|---|---|---|
| B1 | EMA 金叉 8×21 | `EMA8[i] > EMA21[i] and EMA8[i-1] <= EMA21[i-1]` |
| B2 | MACD 金叉（含柱转正补底） | `MACD[i]>sig[i] and MACD[i-1]<=sig[i-1]` 优先；否则 `hist[i]>0 and hist[i-1]<=0` |
| B3 | RSI 超卖反弹 | `RSI[i] > 30 and RSI[i-1] <= 30` |
| B4 | KDJ 低位金叉 | `K[i]>D[i] and K[i-1]<=D[i-1] and K[i] < 50` |
| B5 | 布林下轨反弹 | `Low[i-1] <= BB_lower[i-1] and Close[i] > BB_lower[i]` |
| B6 | 接近支撑位 | `min over top-3 supports of |Close[i]-S| / S < 0.01` |
| B7 | 放量 | `Vol[i] > 1.5 × Vol_MA5[i]` |
| B8 | TD 买入信号 | `TD_setup[i] == 9 and TD_buy_signal[i]` |

**卖出端（`_check_sell_conditions`）** —— 镜像版：

| ID | 条件 |
|---|---|
| S1 | EMA 死叉 8×21 |
| S2 | MACD 死叉（含柱转负补底） |
| S3 | RSI 超买回落（70 上→下） |
| S4 | KDJ 高位死叉（K>50） |
| S5 | 布林上轨回落 |
| S6 | 接近压力位 |
| S7 | 缩量上涨（量价背离）：`Close[i]>Close[i-1] and Vol[i] < 0.7 × Vol_MA5[i]` |

### 4.2 信号桶（消除相关性双重计数）

把上面 8 条按"独立维度"映射到 6 个桶，**每桶最多算 1 票**：

```
BUY_SIGNAL_BUCKETS = {
    "trend":       {B1},                  # EMA
    "momentum":    {B2},                  # MACD
    "oscillator":  {B3, B4},              # RSI + KDJ（任一命中算 1 票）
    "mean_revert": {B5, B6},              # 布林下轨 + 支撑
    "volume":      {B7},                  # 放量
    "pattern":     {B8},                  # TD
}

SELL_SIGNAL_BUCKETS = {
    "trend":       {S1},
    "momentum":    {S2},
    "oscillator":  {S3, S4},
    "mean_revert": {S5, S6},
    "volume":      {S7},
    "pattern":     {},  # 空，留扩展
}

n_buckets = count of buckets with at least one matched ID
```

### 4.3 触发与置信度

```
if n_buckets >= 2:
    signal_strong = (n_buckets >= 3)
    confidence = min(95, 30 + n_buckets × 14)
    # 0:30, 1:44, 2:58, 3:72, 4:86, 5:95
```

**入场判据**：n_buckets ≥ 2 即可触发；保守可以收紧到 ≥ 3。

---

## 5. 入场规则

### 5.1 主多头入场（最常用）

```
def should_enter_long(i):
    if get_board(ticker, name) == "ST":
        return False                              # 直接拒绝
    trend = trend_strength_score(i)
    if trend < 62:                                # 必须至少"偏多"
        return False
    n_buckets, _ = aggregate_buy_buckets(i)
    if n_buckets < 2:                             # 至少 2 个独立桶
        return False
    return True
```

### 5.2 入场价

```
slippage = 0.001    # 0.1%
entry_price = Close[i] × (1 + slippage)           # 做多吃买一价上方
```

### 5.3 仓位规模（资金分配）

主流程使用 95% 现金 + 5% 备用金；如有 R/R 加成可走分级仓位：

```
def position_pct(rr_ratio):
    if rr_ratio >= 3:   return 0.225    # 20-25% 中位
    if rr_ratio >= 2:   return 0.175
    if rr_ratio >= 1.5: return 0.125
    return 0.10                          # < 10%

shares = capital × position_pct(rr) / entry_price
```

简化版（与现有引擎一致）：

```
shares = capital × 0.95 / entry_price
```

### 5.4 入场扣费

```
commission = shares × entry_price × 0.001         # 0.1% 单边
capital -= commission
```

---

## 6. 止损（ATR 动态版）

### 6.1 板块基础倍数

```
BOARD_ATR_MULT = {
    "主板":     2.0,
    "沪深300":  1.8,    # 大盘蓝筹更紧
    "创业板":   2.5,
    "科创板":   2.5,
    "北交所":   3.0,    # 流动性差，止损放宽
    "其他":     2.0,    # 美股/港股等
}
```

### 6.2 市值修正系数

```
def cap_adjust(market_cap_yi):
    if market_cap_yi is None:    return 1.0
    if market_cap_yi >= 500:     return 0.9       # 大盘
    if market_cap_yi >= 100:     return 1.0       # 中盘
    if market_cap_yi >= 30:      return 1.15      # 小盘
    return 1.3                                     # 微盘
```

### 6.3 止损公式（多头）

```
def dynamic_stop_loss_long(entry_price, atr, board, market_cap_yi):
    if board == "ST":
        return None                                      # 拒绝入场
    if atr is None or atr <= 0:
        return entry_price × (1 - 0.05)                  # 回退 5%

    eff_mult = BOARD_ATR_MULT[board] × cap_adjust(market_cap_yi)
    raw = entry_price - atr × eff_mult

    # 硬约束：止损至少 2% 远（防假止损），最多 12% 远（防风险过大）
    max_stop = entry_price × 0.98
    min_stop = entry_price × 0.88
    return clip(raw, min_stop, max_stop)
```

空头镜像：`stop = entry + atr × eff_mult`，clamp 到 `[entry×1.02, entry×1.12]`。

### 6.4 入场后的硬性出场

每根 K 线优先级（自上而下）：

```
1. ST 化（标的名称突变）              → 立即清仓
2. Close[i] <= stop_price             → 触发止损出场
3. Close[i] >= take_profit_3          → 全部止盈
4. trend_strength_score < 18          → 强势空头出场（无视盈亏）
5. 出场端 buckets >= 2                → 信号出场
```

止损价 **入场时一次确定，持仓期内不上移**（无 trailing stop）。要 trailing 可改成"每周更新止损 = max(原止损, 入场价×0.98, 当前价×0.92)"。

---

## 7. 止盈（三档）

### 7.1 支撑/压力加权聚合

候选位（每个都附距离 = `|level - Close| / Close`）：

| 类型 | 权重 |
|---|---|
| Fibonacci 0.236 / 0.382 / 0.5 / 0.618 / 0.786 | 3.0 |
| EMA 8/13/21 中的 max（多头时） | 2.5 |
| EMA 55/144/169/288/338 中的 max | 2.0 |
| BB_upper（多头止盈用） | 2.0 |
| VWAP（若位于现价上方） | 1.5 |
| 近 60 日高点 | 1.0 |

**算法**：
```
candidates = sorted(all_levels above current_price, by distance asc)[:6]
weighted_avg = Σ(level × weight) / Σ(weight)
```

按距离从近到远取**最近 3 档非重叠候选**作为 `take_profit_1 / 2 / 3`（"非重叠"= 任意两档相距 ≥ 1.5 × ATR）。

### 7.2 R/R 比

```
rr = (take_profit_1 - entry_price) / (entry_price - stop_price)
if rr < 1:
    skip_trade = True       # R/R 小于 1 不入场
```

### 7.3 分批止盈（推荐）

```
50% 仓位在 take_profit_1 平掉
30% 仓位在 take_profit_2 平掉
20% 仓位在 take_profit_3 平掉
任何时候触发 stop_price → 剩余全部清仓
```

简单版（可选）：100% 在 take_profit_1 平掉。

---

## 8. 风险指标（无风险利率参数化）

```
DEFAULT_RISK_FREE_RATES = {
    "A股":    0.022,    # 中国 10Y 国债 ~2.2%（2025-2026）
    "港股":   0.040,    # 港币利率紧跟美元
    "美股":   0.043,    # 美国 10Y Treasury ~4.3%
    "国内期货": 0.022,
    "美股期货": 0.043,
    "外汇":    0.030,
    "其他":    0.030,
}
```

公式：

```
sharpe   = √252 × mean(daily_return - rf/252) / std(daily_return)
sortino  = √252 × mean(daily_return - rf/252) / std(downside_returns)
alpha    = annual_return - (rf + β × (annual_bench - rf))     # Jensen
calmar   = annual_return / |max_drawdown|
VaR_95   = percentile(daily_returns, 5)                       # 历史模拟
CVaR_95  = mean(returns where returns <= VaR_95)
```

---

## 9. 单指标"看多概率"（仅用于显示，不作为入场判据）

下表用于在 UI 上给"指标解读卡"贴一个百分比，**不应**直接驱动建仓。校准基于 A 股 2018-2025 全市场 5 日窗口实测胜率。

### 9.1 RSI

| RSI 区间 | bull% | bear% | bias |
|---|---|---|---|
| > 80 | 25 | 55 | -60 |
| 70-80 | 38 | 42 | -25 |
| 50-70 | 52 | 28 | +25 |
| 30-50 | 35 | 40 | -10 |
| 20-30 | 55 | 28 | +45 |
| < 20 | 65 | 18 | +65 |
| 上穿 50 | bias +20 | | |
| 下穿 50 | bias -20 | | |

### 9.2 MACD

| 状态 | bull% | bear% | bias |
|---|---|---|---|
| 金叉 + 0 轴上 | 60 | 22 | +65 |
| 金叉 + 0 轴下 | 52 | 28 | +40 |
| 死叉 + 0 轴下 | 22 | 60 | -65 |
| 死叉 + 0 轴上 | 32 | 50 | -40 |
| 红柱扩张 | 53 | 27 | +30 |
| 红柱收缩 | 42 | 35 | +12 |
| 绿柱 | 27 | 53 | -30 |

### 9.3 KDJ

| 状态 | bull% | bear% | bias |
|---|---|---|---|
| J > 100 + 死叉 | 22 | 60 | -60 |
| J > 100 | 30 | 50 | -40 |
| J < 0 + 金叉 | 62 | 20 | +60 |
| J < 0 | 53 | 27 | +40 |
| 普通金叉 | 52 | 28 | +35 |
| 普通死叉 | 28 | 52 | -35 |
| K/D > 80 | 35 | 45 | -25 |
| K/D < 20 | 55 | 28 | +25 |

### 9.4 布林带

| 状态 | bull% | bear% | bias |
|---|---|---|---|
| Close > 上轨 | 35 | 45 | -25 |
| Close < 下轨 | 52 | 28 | +30 |
| Close > 中轨 | 48 | 30 | +12 |
| Close < 中轨 | 30 | 48 | -12 |

---

## 10. 多因子选股（候选池筛选）

### 10.1 因子定义

| 因子 | 来源 | 方向 | 类别 |
|---|---|---|---|
| `f_momentum_5d` | (Close[t]/Close[t-5])-1 | + | 动量 |
| `f_momentum_60d` | (Close[t]/Close[t-60])-1 | + | 动量 |
| `f_volume_ratio` | spot 接口"量比" | + | 量能 |
| `f_turnover` | spot"换手率" | + | 量能 |
| `f_amount` | spot"成交额" | + | 量能 |
| `f_amplitude` | spot"振幅" | - | 波动 |
| `f_pe` | spot"PE-动态" | - | 估值 |
| `f_pb` | spot"PB" | - | 估值 |
| `f_market_cap` | spot"流通市值" | - | 市值 |
| `f_volatility_20d` | std(returns[-20:])×√252×100 | - | 波动 |
| `f_rsi_14` | 见 §2.3 | 0 | 技术（特殊处理：50 中位最佳） |
| `f_macd_hist` | 见 §2.2 | + | 技术 |
| `f_bb_position` | %B | 0 | 技术（0.5 中位最佳） |
| `f_ema_alignment` | 0-3 阶梯（见下） | + | 技术 |
| `f_northbound` | 北向 5d/20d 净流入打分 | + | 北向 |
| `f_limit_up_recent` | 近期涨停频率 | + | 动量（A 股专属） |
| `f_sector_strength` | 个股 vs 板块 20d 涨幅差 | + | 板块 |
| `f_dividend_yield` | DY 阶梯打分 | + | 股息 |
| `f_margin_flow` | 融资余额 5d/20d 变化率 | 0 | 量能（过热反向） |

EMA 排列阶梯：
```
score = 0
if EMA8 > EMA21:  score += 1
if EMA21 > EMA55: score += 1
if Close > EMA8:  score += 1
return float(score)            # 0-3
```

北向因子：
```
df = ak.stock_hsgt_individual_em(stock=code).head(25)
net_5d  = sum(df.head(5)["持股市值"].diff(-1))
net_20d = sum(df.head(20)["持股市值"].diff(-1))
score = 0
if net_5d > 1e8:   score += 0.6
elif net_5d < -1e8: score -= 0.6
if net_20d > 0 and net_5d > 0:   score += 0.4
elif net_20d < 0 and net_5d < 0: score -= 0.4
return clip(score, -1, 1)
```

涨停近期因子（用现成 K 线）：
```
threshold = 0.198 if code starts with 3/688/689 else 0.098
limit_up_mask = (Close.pct_change() >= threshold)
last_3  = sum(limit_up_mask[-3:])
last_5  = sum(limit_up_mask[-5:])
last_20 = sum(limit_up_mask[-20:])
if limit_up_mask[-1]:    return 0.8     # 昨日涨停
if last_5 >= 2:          return 0.7     # 5d 内连板妖股
if last_3 >= 1:          return 0.5
if last_20 == 0:         return 0.0
return 0.2
```

板块强度因子：
```
stock_20d  = stock_close[-1]/stock_close[-21]   - 1
sector_20d = sector_close[-1]/sector_close[-21] - 1
diff = stock_20d - sector_20d
return {
    diff >  0.10:  +1.0,
    diff >  0.05:  +0.6,
    diff >  0:     +0.3,
    diff > -0.05:  -0.2,
    diff > -0.10:  -0.6,
    else:          -1.0,
}
```

股息率因子：
```
DY > 6%: +1.0    DY > 4%: +0.6    DY > 3%: +0.3
DY > 2%:  0.0    DY <= 2%: -0.2
```

融资融券因子（注意 > +20% 不是满分，是反向风险信号）：
```
chg = (mean(融资余额[:5]) - mean(融资余额[:20])) / mean(融资余额[:20])
chg > 0.20:  +0.2     # 杠杆过热
chg > 0.05:  +0.5
chg > -0.05:  0.0
chg > -0.10: -0.3
else:        -0.5
```

### 10.2 标准化

```
for each factor:
    z = (raw - mean) / std         # 跨全市场
    if FACTOR_DIRECTION[factor] == -1: z = -z
    if factor == "f_rsi_14":            # 中位最佳
        z = -|raw - 50| / 25
    if factor == "f_bb_position":       # %B 中位最佳
        z = -|raw - 0.5| / 0.3
    if factor == "f_margin_flow":       # 已经是有方向标量，直接用
        z = raw
```

### 10.3 类别加权

```
WEIGHT_PRESETS = {
    "均衡型": {动量:0.16, 量能:0.16, 技术:0.20, 波动:0.10, 估值:0.08, 市值:0.05, 北向:0.10, 板块:0.10, 股息:0.05},
    "动量型": {动量:0.28, 量能:0.18, 技术:0.16, 波动:0.06, 估值:0.03, 市值:0.07, 北向:0.10, 板块:0.10, 股息:0.02},
    "价值型": {动量:0.05, 量能:0.10, 技术:0.10, 波动:0.12, 估值:0.30, 市值:0.05, 北向:0.08, 板块:0.05, 股息:0.15},
    "红利型": {动量:0.05, 量能:0.08, 技术:0.10, 波动:0.12, 估值:0.15, 市值:0.05, 北向:0.05, 板块:0.05, 股息:0.35},
}

# 每只股票的综合打分
category_z[c] = mean(z[f] for f in factors where FACTOR_CATEGORY[f] == c)
composite = Σ category_z[c] × W[c]
```

### 10.4 过滤

入选候选池前剔除：
```
- 名称包含 ST / *ST / 退市
- spot["成交额"] == 0（停牌）
- 价格非法（NaN, ≤0）
- PE 范围: PE 不在 (0, 500]
- PB 范围: PB 不在 (0, 50]
```

按 `composite` 倒序取 Top N（默认 50）作为候选池。

---

## 11. 回测引擎语义

### 11.1 资金管理

```
initial_capital = 100_000          # 默认 10 万
commission_rate = 0.001            # 0.1% 单边
slippage = 0.001                   # 0.1% 单边
position_pct = 0.95                # 95% 现金入场，5% 备用金
```

### 11.2 主循环（多头单标的）

```
for i in range(len(df)):
    update_equity_curve(i)

    if not in_position:
        if should_enter_long(i):
            entry_price = Close[i] × (1+slippage)
            shares      = capital × 0.95 / entry_price
            capital    -= shares × entry_price × commission_rate

            atr_now = ATR[i]
            stop_price       = dynamic_stop_loss_long(entry_price, atr_now, board, mcap)
            take_profit_1..3 = compute_resistance_levels(i, entry_price)
            in_position = True
            entry_index = i

    else:
        # 1. 强制出场
        if Close[i] <= stop_price:
            exit_at(i, exit_reason="止损")
        elif Close[i] >= take_profit_3:
            exit_at(i, exit_reason="止盈3")
        elif trend_strength_score(i) < 18:
            exit_at(i, exit_reason="强势空头")

        # 2. 信号出场
        elif n_sell_buckets(i) >= 2:
            exit_at(i, exit_reason="信号出场")

        # 3. 分批止盈（可选）
        elif Close[i] >= take_profit_2 and not tp2_hit:
            scale_out(0.30, exit_reason="止盈2")
        elif Close[i] >= take_profit_1 and not tp1_hit:
            scale_out(0.50, exit_reason="止盈1")

# 末尾强平
if in_position:
    exit_at(last_index, exit_reason="回测结束")
```

### 11.3 出场计算

```
def exit_at(i, exit_reason):
    exit_price   = Close[i] × (1 - slippage)
    gross_pnl    = (exit_price - entry_price) × shares
    commission   = shares × exit_price × commission_rate
    net_pnl      = gross_pnl - commission
    capital     += net_pnl
    record_trade(entry_date, exit_date, entry_price, exit_price, shares,
                 net_pnl, exit_reason)
    in_position = False
```

### 11.4 输出指标

每次回测输出（与现有 `BacktestResult` 一致）：

| 指标 | 公式 |
|---|---|
| total_return | (final_capital - initial) / initial |
| total_trades | trades 数量 |
| win_rate | winning_trades / total_trades |
| profit_factor | \|Σ profits / Σ losses\| |
| avg_profit | mean(P&L of winning trades) |
| avg_loss | mean(P&L of losing trades) |
| max_drawdown_pct | (peak_equity - trough_equity) / peak_equity |
| sharpe_ratio | √252 × mean(daily_ret) / std(daily_ret) |
| sortino_ratio | √252 × mean(excess) / std(downside) |
| calmar_ratio | annual_return / |max_dd| |

---

## 12. 端到端伪代码（实盘扫描+回测）

```
def end_to_end_backtest(start_date, end_date, universe="沪深300", style="均衡型"):
    # 1. 取所有股票元数据
    spot = ak.stock_zh_a_spot_em()
    universe_codes = filter_universe(spot, universe)

    # 2. 因子打分选 Top 50 候选（每月调仓）
    rebalance_dates = monthly_dates(start_date, end_date)
    portfolios = {}
    for d in rebalance_dates:
        # 计算每只股票的历史因子（含 5 个 A 股因子）
        scores = []
        for code in universe_codes:
            spot_factors = compute_spot_factors(spot.loc[code])
            hist_factors = compute_historical_factors(
                code, days=60,
                with_a_share_factors=True,
                stock_info=info_cache[code],
                sector_index_df=sector_index_cache[get_sector(code)],
            )
            all_factors = {**spot_factors, **hist_factors}
            zs = normalize(all_factors, universe_codes)
            composite = Σ category_mean(zs, c) × WEIGHT_PRESETS[style][c]
            scores.append((code, composite))
        portfolios[d] = top_n(scores, 50)

    # 3. 对每只候选分别回测
    results = []
    for code in flatten(portfolios.values()):
        df = fetch_ohlcv(code, start_date, end_date)
        df = add_all_indicators(df)
        meta = {"board": get_board(code, name_map[code]),
                "market_cap_yi": mcap_map[code],
                "market": "A股"}
        engine = BacktestEngine(
            initial_capital=100_000,
            use_atr_stop=True,
            board=meta["board"],
            market_cap_yi=meta["market_cap_yi"],
        )
        # 自定义策略：入场 = trend≥62 + n_buckets≥2，出场见 §11
        strategy = TrendStrengthStrategy()
        results.append(engine.run(df, strategy, ticker=code))

    # 4. 组合层面统计（等权或市值加权）
    portfolio_equity = compose_portfolio_equity(results, weighting="equal")
    portfolio_metrics = calc_metrics(portfolio_equity, market="A股")

    return {
        "individual": results,
        "portfolio": portfolio_metrics,
    }
```

---

## 13. 回测时的关键设置（默认值汇总）

```yaml
# 信号
trend_strength_threshold:  62        # 入场最低趋势分（"偏多"）
buy_buckets_threshold:     2         # 入场最少独立桶数
sell_buckets_threshold:    2         # 信号出场最少独立桶数
strong_short_exit:         18        # 趋势分跌破此值强制清仓

# 资金
initial_capital:           100000    # 10 万
position_pct:              0.95
commission_rate:           0.001
slippage:                  0.001

# ATR 止损
use_atr_stop:              true
atr_period:                14
board_mult:
  主板:    2.0
  沪深300: 1.8
  创业板:  2.5
  科创板:  2.5
  北交所:  3.0
  其他:    2.0
cap_adjust:                # 流通市值（亿）
  ">=500":  0.9
  ">=100":  1.0
  ">=30":   1.15
  "<30":    1.3
stop_min_pct:              0.02      # 硬下限
stop_max_pct:              0.12      # 硬上限
fallback_stop_pct:         0.05      # ATR 不可用时回退

# 止盈
take_profit_levels:        3         # 三档
scale_out_ratios:          [0.50, 0.30, 0.20]   # 三档分批
min_rr_to_enter:           1.0       # R/R < 1 不入场

# 风险指标
risk_free_rate:            "auto"    # 按市场自动选
risk_free_overrides:
  A股: 0.022
  美股: 0.043
  港股: 0.040

# 调仓
rebalance_frequency:       "monthly"
top_n_in_portfolio:        50
weighting:                 "equal"
```

---

## 14. 实施 Checklist（移植到外部回测系统）

按这个顺序实现，每步验收一个独立子系统：

- [ ] **§2 指标层**：用同样的 OHLCV 输入，与本项目 `add_all_indicators(df)` 比对最后 1 根 K 线的 EMA8/MACD/RSI/KDJ/ATR/BB——差异应 < 0.01%。
- [ ] **§3 趋势分**：构造 6 组测试用例（仅 EMA、仅 MACD、多维共振、纯空、平盘、放量上涨），逐一对比 score 和 level。
- [ ] **§4 信号桶**：构造"MACD 单事件"K 线，确认只算 1 票而非 2 票。
- [ ] **§6 ATR 止损**：测大盘低波（茅台）/ 创业板小盘 / 北交所微盘 / ST，确认分别落在 2% / 6-12% / 12% 上限 / None。
- [ ] **§7 止盈**：构造 3 档非重叠压力位，验证分批比例。
- [ ] **§9 概率表**：抽 5 个状态比对 bull%/bear% 数值。
- [ ] **§10 因子打分**：在沪深 300 上跑一轮，对比 Top 10 候选股票列表与本项目 `factor_screener.run_factor_screening()` 输出。
- [ ] **§11-12 回测**：对茅台 / 宁德时代 / 中芯国际 各跑 2018-2025，比对总收益、胜率、夏普三个指标，差异应 < 5%（滑点近似实现可能导致小差距）。

---

## 15. 已知简化与改进余地

- **不考虑停牌**：现引擎假设每天都能成交。实盘需在 OHLCV 中标记 `suspended=True` 并跳过。
- **不考虑除权除息**：默认前复权。如用后复权，止盈点位需相应调整。
- **不考虑融资融券和打新**：纯多头股票池回测。
- **没有持仓时间限制**：可加 max holding period（如 60 个交易日）。
- **趋势分基准 45 是静态**：理论上应跟踪沪深 300 长期分位（牛市基准 50+, 熊市 40-）。
- **板块强度因子需要外部板块指数**：目前需调用方预拉，未来可在 `sector_comparison.py` 里加自动路由。
- **没有择时层**：所有时间点都"满仓股票"。要做择时（VIX > 30 减仓、北向连续流出空仓）需要在 `should_enter_long` 之前加一层全局过滤。

---

> 版本：custom-model v2（2026-04 修订）
> 与代码同步：`modules/{analysis,signal_generator,indicator_interpreter,risk_management,risk_metrics,backtest,factor_screener,stock_search,technical}.py`
