# 策略参数与评分系统总览

本文档逐层列出本项目所有"会影响买卖决策"的参数、阈值、权重和打分公式，按"重要性"分级。每条都带源文件行号，方便你直接跳进去改。

> 注：行号以代码现在的状态为准；如果之后大改文件可能会漂移。

---

## 0. 速查表（最常调的 12 个旋钮）

| 想调什么 | 文件 / 行 | 当前值 | 影响 |
|---|---|---|---|
| 趋势强度阈值（多头/空头分界） | `modules/analysis.py:721-735` | 75 / 55 / 45 / 25 | "强势多头/偏多/中性/偏空/强势空头"切档 |
| 买入需要的最少确认数 | `modules/signal_generator.py:201` | 2（隐含于`min(95, 40+n*15)`） | 越大越保守 |
| 量能放大判定 | `modules/signal_generator.py:98` | 1.5×MA5 | "放量"门槛 |
| 缩量上涨（背离）判定 | `modules/signal_generator.py:160` | 0.7×MA5 | 缩量阈值 |
| RSI 超买/超卖 | `modules/signal_generator.py:72,134` | 30 / 70 | 经典阈值，A 股可考虑 25/75 |
| KDJ 金/死叉位置过滤 | `modules/signal_generator.py:79,141` | <50（金叉） / >50（死叉） | 避免高位金叉 |
| 默认止损 / 止盈 | `modules/backtest.py:109-110, 469-470 等` | 5% / 12-15% | 所有策略默认 |
| 滑点 / 佣金 | `modules/backtest.py:294-295` | 0.1% / 0.1% | 回测真实度 |
| 初始仓位占比 | `modules/backtest.py:340` | 95% | 是否留备用金 |
| 因子打分权重预设 | `modules/factor_screener.py:22-35` | 均衡/动量/价值三套 | 选股风格 |
| 涨停龙头门槛 | `modules/dragon_screener.py:24-25, 65` | 9.8% / 19.8%；扫描 250d，≥10 次 | 主板/创业板 |
| 多周期权重 | `modules/multi_timeframe.py` / `modules/elliott_wave.py:32` | 周 0.35 / 日 0.30 / 4h 0.20 / 1h 0.15 | MTF 共振权重 |

---

## Tier A — 真正决定买卖的核心模块

### A1. `modules/analysis.py` — 主分析编排

`run_full_analysis(df, info, timeframe)` 是顶层入口，吐出趋势、信号、目标价、置信度。

**趋势强度评分（基准 50，封顶 0-100）** —— `calculate_trend_strength` @ [analysis.py:663-742](../modules/analysis.py#L663)

| 因子 | 满分 | 规则 |
|---|---|---|
| EMA 排列 | ±25 | `多头 +20`，`内层多 +5`；`空头 -20`，`内层空 -5` |
| MACD | ±15 | 金叉 +15 / 死叉 -15 |
| KDJ | ±9 | `kdj_strength × 3`（kdj_strength 取 -3..+3） |
| BB 位置 | ±5 | 突破上轨 +5 / 跌破下轨 -5；中轨上 +3 / 中轨下 -3 |

**等级切档** @ analysis.py:721-735

```
score >= 75: 强势多头   #0ECB81
score >= 55: 偏多       #52B788
score >= 45: 中性震荡   #FFD700
score >= 25: 偏空       #FFA500
其他:        强势空头   #F6465D
```

> 调参建议：基准 50 + EMA 占 25 分，意味着 EMA 单一信号就能把分数推到 70+，可能过于偏重 EMA。如果你想让 MACD/KDJ 共同决定，把 EMA 多头从 +20 降到 +15、把 MACD 从 +15 提到 +20。

**支撑/压力加权聚合** @ [analysis.py:404-487](../modules/analysis.py#L404)

权重（越大越重要）：
```
Fib       3.0
EMA 内    2.5
EMA 外    2.0
BB 上/下  2.0
VWAP      1.5
近期高/低 1.0
```
取距离当前价最近的 4 个，按权重做加权均值，输出"买入区/卖出区"。

**止损/止盈/仓位建议** @ [analysis.py:489-658](../modules/analysis.py#L489)

```
take_profit_1 = 最近一档压力
take_profit_2 = 第二档压力
take_profit_3 = 第三档压力
stop_loss     = min(supports) - ATR  或  recent_low - 0.5×ATR
              且必须 >= 入场价的 92%（硬性下限）
```

**置信度评分**（0-100，封顶 100）@ analysis.py:577-613
```
+20: 至少 3 个支撑位     -10 to +20: 趋势对齐
+15: 至少 2 个压力位      +25: R/R >= 2
+10: 单一支撑位           +15: R/R >= 1.5
+10: BB 中轨上            +15: BB 跌破下轨（均值回归）
```

**仓位建议** analysis.py:568-575
```
R/R >= 3:   20-25%
R/R >= 2:   15-20%
R/R >= 1.5: 10-15%
其他:       <10%
```

**短线趋势二级评分** analysis.py:1126-1318（用于 1h/4h 周期）：
```
+15: 价 > EMA8 且 EMA8 上行
+10: BB 宽度处于历史 10% 分位（squeeze）
+8:  突破 BB 上轨
+10/-10: MACD 柱扩张/收缩
+15/-15: MACD 金叉/死叉
+10/-10: KDJ J<0 / J>100
±8:  连续三根同向
±5:  高低点逐步抬升/降低
-3:  ATR > 3%（高波动惩罚）
```

---

### A2. `modules/signal_generator.py` — 多指标共振信号

**买入确认列表**（满足 ≥2 条触发买入）@ [signal_generator.py:47-107](../modules/signal_generator.py#L47)

| 条件 | 阈值 | 行 |
|---|---|---|
| EMA 8×21 金叉 | EMA8 上穿 EMA21 | 58-59 |
| MACD 柱由负转正 | 历史负→当前正 | 63-64 |
| MACD 金叉 | DIF 上穿 DEA | 67-68 |
| RSI 超卖反弹 | 由 ≤30 反弹至 >30 | 72-73 |
| KDJ 低位金叉 | K 上穿 D 且 K < **50** | 76-80 |
| 布林下轨反弹 | 前一根 Low ≤ 下轨，当前 Close > 下轨 | 84-85 |
| 接近支撑 | `|Close-Support|/Support < 1%` | 88-94 |
| 放量 | Vol > **1.5×Vol_MA5** | 97-99 |
| TD 买入信号 | TD setup == 9 | 102-105 |

**卖出确认列表** @ [signal_generator.py:110-163](../modules/signal_generator.py#L110)

| 条件 | 阈值 |
|---|---|
| EMA 8×21 死叉 | EMA8 下穿 EMA21 |
| MACD 柱转负 | 历史正→当前负 |
| MACD 死叉 | DIF 下穿 DEA |
| RSI 超买回落 | 由 ≥**70** 回落至 <70 |
| KDJ 高位死叉 | K 下穿 D 且 K > **50** |
| 布林上轨回落 | 前一根 High ≥ 上轨，当前 Close < 上轨 |
| 接近压力 | `|Close-Resistance|/Resistance < 1%` |
| 缩量上涨（背离） | Close > 前一根 且 Vol < **0.7×Vol_MA5** |

**置信度公式** @ signal_generator.py:201
```python
confidence = min(95, 40 + len(confirmations) * 15)
# 1 个: 55%；2 个: 70%；3 个: 85%；4+ 个: 95%
```

**仓位分类**（dip-buy / breakout / breakdown / bounce）@ signal_generator.py:332-449

| 类型 | 价格 | RSI | 量比 | MACD | EMA |
|---|---|---|---|---|---|
| 低吸（dip-buy） | 距支撑 ≤ 2% | <35 | <0.8 | hist 收敛 | — |
| 突破（breakout） | 上破前压力 | — | >1.5× | hist 扩张 | EMA8>21 |
| 破位（breakdown） | 下破前支撑 | <40 且下行 | >1.2× | — | EMA8<21 |
| 反弹（bounce） | 距支撑 ≤ 3% 且上行 | 30-45 上行 | 量能恢复 | — | — |

`confidence = min(95, 30 + len(reasons) * 20)`

> 调参建议：买/卖确认条件目前是平权的，但"MACD 柱转正 + 金叉"会被记两次，等于双倍权重；"RSI 30 反弹"很常见所以容易达成。如果想让信号更"贵"，可以把 RSI 反弹的阈值从 30 收紧到 25，把 KDJ 金叉的位置过滤从 <50 收紧到 <30。

---

### A3. `modules/backtest.py` — 策略回测

**回测引擎参数** @ [backtest.py:289-460](../modules/backtest.py#L289)

| 参数 | 默认值 | 行 | 说明 |
|---|---|---|---|
| `initial_capital` | 100,000 | 293 | 初始资金 |
| `commission_rate` | 0.001 (0.1%) | 294 | 单边佣金 |
| `slippage` | 0.001 (0.1%) | 295 | 单边滑点 |
| 仓位占比 | 95% | 340 | `shares = capital * 0.95 / entry` |

**进出场公式**（逐笔）：
```
entry_price = price * (1 + slippage)    # 滑点加价
exit_price  = price * (1 - slippage)
commission  = shares * price * 0.001    # 双边都算
gross_pnl   = (exit - entry) * shares
net_pnl     = gross_pnl - 2 * commission
```

**各策略默认止损/止盈**

| 策略 | 止损 | 止盈 | 行 |
|---|---|---|---|
| EMA 隧道（趋势） | 5% | 15% | 109-110 |
| 布林（均值回归） | 5% | — | 176 |
| KDJ（超买超卖） | 5% | — | 229 |
| MACD | 5% | 12% | 467 |
| RSI 反转 | 5% | — | 509（默认超卖 30 / 超买 70） |
| 一目均衡表 | 5% | 15% | 588 |
| TD 序列 | 4% | 10% | 643 |

**指标公式（核心）**

```
win_rate         = winning_trades / total_trades
profit_factor    = |Σ profits / Σ losses|
max_drawdown_pct = (peak - trough) / peak
sharpe_ratio     = √252 × mean(daily_returns) / std(daily_returns)
```

> 调参建议：默认 5% 止损对 A 股个股偏紧（A 股日内 ±10% 涨跌停板），可以根据 ATR 动态化：`stop = entry - 2*ATR`。当前所有策略都是"百分比止损"，没有用 ATR——如果想做正经的波动率自适应，需要重写 `_check_exit` 这一层。

---

### A4. `modules/factor_screener.py` — 多因子选股

**因子方向** @ [factor_screener.py:57-72](../modules/factor_screener.py#L57)

| 因子 | 方向 | 含义 |
|---|---|---|
| 5d 动量 | +1 | 越大越好 |
| 60d 动量 | +1 | 越大越好 |
| 量比 | +1 | 大 = 资金活跃 |
| 换手率 | +1 | 大 = 流动性好 |
| 振幅 | -1 | 小 = 稳定 |
| PE / PB | -1 | 小 = 便宜 |
| 市值 | -1 | 小盘偏好 |
| 20d 波动率 | -1 | 小 = 稳定 |
| RSI 14 | 0 | 中位最佳（特殊处理） |
| MACD hist | +1 | 正动量 |
| BB 位置 | 0 | 中轨最佳（特殊处理） |
| EMA 排列 | +1 | 多头优先 |

**权重预设** @ factor_screener.py:22-35

| 类型 | 动量 | 量能 | 技术 | 波动 | 估值 | 市值 |
|---|---|---|---|---|---|---|
| 均衡型 | 0.20 | 0.20 | 0.25 | 0.15 | 0.10 | 0.10 |
| 动量型 | 0.35 | 0.20 | 0.20 | 0.10 | 0.05 | 0.10 |
| 价值型 | 0.20 | 0.15 | 0.15 | 0.15 | **0.30** | 0.05 |

**过滤规则** @ factor_screener.py:129-184
```
排除 ST/退市
排除 trading_amount == 0（停牌）
排除非法价格
PE 范围: 0 < PE <= 500
PB 范围: 0 < PB <= 50
全市场 z-score 标准化后再加权
```

> 调参建议：均衡型的"技术"权重 0.25 高于动量 0.20，跟"动量型"反而冲突；价值型的估值权重 0.30 占主导但仍带 0.20 动量——如果想做纯价值，可以把动量降到 0。

---

### A5. `modules/dragon_screener.py` — 涨停龙头

**关键参数** @ [dragon_screener.py:24-66](../modules/dragon_screener.py#L24)

```
主板涨停门槛   9.8%   line 24
创业板涨停门槛 19.8%  line 25
扫描天数       250    line 66 (一年交易日)
最少涨停次数   10     line 65（默认；UI 可改）
```

调用 `ak.stock_zt_pool_em(date)` 逐日扫描，按代码聚合 → 过滤 ≥ N 次 → 输出 `代码/名称/板块/涨停次数/最大连板/最近涨停日`。

---

## Tier B — 指标解读 & 形态识别

### B1. `modules/indicator_interpreter.py` — 偏向打分（每个指标独立）

每个指标返回 `(bias_score: -100..100, bull_pct, bear_pct)`，最终被 `interpret_all_indicators` 聚合。

**RSI** @ [indicator_interpreter.py:13-77](../modules/indicator_interpreter.py#L13)
| 区间 | bias | bull% | bear% |
|---|---|---|---|
| >80 | -70 | 20 | 65 |
| 70-80 | -30 | 35 | 45 |
| 50-70 | +30 | 55 | 25 |
| 30-50 | -10 | 35 | 40 |
| 20-30 | +50 | 60 | 25 |
| <20 | +80 | 70 | 15 |
| 跨越 50 上行 | bias +20 |
| 跨越 50 下行 | bias -20 |

**MACD**
| 状态 | bias | bull% |
|---|---|---|
| 金叉 + 0 轴上 | +80 | 72 |
| 金叉 + 0 轴下 | +50 | 58 |
| 死叉 + 0 轴下 | -80 | 13 |
| 死叉 + 0 轴上 | -50 | 25 |
| 红柱扩张 | +40 | 60 |
| 红柱收缩 | +15 | 45 |
| 绿柱 | -40 | 22 |

**KDJ**
| 状态 | bias |
|---|---|
| J>100 + 死叉 | -75 |
| J>100 | -50 |
| J<0 + 金叉 | +75 |
| J<0 | +50 |
| 金叉 | +45 |
| 死叉 | -45 |
| K/D >80 | -30 |
| K/D <20 | +30 |

**Bollinger**（基于 %B）
| 状态 | bias |
|---|---|
| Close > 上轨 | -35 |
| Close < 下轨 | +35 |
| Close > 中轨 | +15 |
| Close < 中轨 | -15 |

> 调参建议：bull%/bear% 加和不到 100% 是有意为之（剩余是"震荡"），但有些表里 bull% 偏高（金叉+0轴上 = 72%，可能过于乐观）。如果偏好保守，可以把"金叉+0轴上"从 72 降到 65。

---

### B2. `modules/divergence_detector.py` — 背离检测

**Swing 窗口** = **5** 根 K 线 @ divergence_detector.py:14, 26
**最小跨度** = 5 根 K 线 @ 52, 94, 134

| 类型 | 含义 | 信号 |
|---|---|---|
| Regular Bearish | 价格 HH，指标 LH | 卖（顶背离） |
| Regular Bullish | 价格 LL，指标 HH | 买（底背离） |
| Hidden Bearish | 价格 LH，指标 HH | 卖（隐藏顶） |
| Hidden Bullish | 价格 HH，指标 LL | 买（隐藏底） |

> 调参建议：window=5 对短周期（小时线）够用，但日线推荐 8-10。可以做成参数。

---

### B3. `modules/candlestick_patterns.py` — K 线形态

**形态评分表** @ [candlestick_patterns.py:13-37](../modules/candlestick_patterns.py#L13)

| 形态 | 类型 | 方向 | 分值 |
|---|---|---|---|
| 锤子 Hammer | 反转 | 看涨 | +50 |
| 看涨吞没 | 反转 | 看涨 | +60 |
| 早晨之星 | 反转 | 看涨 | +65 |
| 三白兵 | 反转 | 看涨 | +60 |
| 刺透 | 反转 | 看涨 | +40 |
| 看涨孕线 | 反转 | 看涨 | +30 |
| 蜻蜓十字 | 反转 | 看涨 | +35 |
| 倒锤 | 反转 | 看涨 | +35 |
| 流星 | 反转 | 看跌 | -50 |
| 看跌吞没 | 反转 | 看跌 | -60 |
| 黄昏之星 | 反转 | 看跌 | -65 |
| 乌云盖顶 | 反转 | 看跌 | -40 |
| 三只乌鸦 | 反转 | 看跌 | -60 |
| 看跌孕线 | 反转 | 看跌 | -30 |
| 上吊 | 反转 | 看跌 | -40 |
| 墓碑十字 | 反转 | 看跌 | -35 |
| 升势三法 | 持续 | 看涨 | +30 |
| 降势三法 | 持续 | 看跌 | -30 |
| 十字星 | 中性 | — | 0 |

**形态识别阈值**（关键）
```
Hammer: 下影线 >= 实体 × 2，上影线 <= 实体 × 0.5，实体 >= 区间的 10%，且处于下跌趋势
Shooting Star: 上影线 >= 实体 × 2，下影线 <= 实体 × 0.5，处于上涨趋势
```

---

### B4. `modules/elliott_wave.py` — 艾略特波浪

**理想斐波比例** @ [elliott_wave.py:15-22](../modules/elliott_wave.py#L15)
```python
W2 retrace:    [0.50, 0.618, 0.786]
W3 extension:  [1.618, 2.0, 2.618]
W4 retrace:    [0.236, 0.382, 0.50]
W5 extension:  [0.618, 1.0, 1.618]
B retrace:     [0.50, 0.618, 0.786]
C extension:   [0.618, 1.0, 1.618]
TOLERANCE = 0.08  # ±8% 匹配容差
```

**波次偏向** @ elliott_wave.py:27-36
```
impulse_W1: +1.0    corrective_A: -1.0
impulse_W2: -0.5    corrective_B: +0.3
impulse_W3: +2.0    corrective_C: -1.5
impulse_W4: -0.5
impulse_W5: +0.5
```

**多周期权重**：周线 0.35 / 日线 0.30 / 4h 0.20 / 1h 0.15

---

### B5. 量价分析 / 支撑压力

**量价关系** @ volume_price_analysis.py:13-82
```
放量上涨 (vol↑, price↑):  bias +30
缩量上涨 (vol↓, price↑):  背离, bias -25
放量下跌 (vol↑, price↓):  危险, bias -40
缩量下跌 (vol↓, price↓):  企稳, bias +10

vol_ratio = current_vol / vol_ma20
> 1.5: 放量
< 0.7: 缩量
```

---

## Tier C — 上下文 / 过滤器

### C1. `modules/market_sentiment.py` — 市场情绪

**VIX 分档** @ market_sentiment.py:34-45
```
> 30: 恐慌
> 20: 焦虑
> 12: 正常
其他: 贪婪
```

**北向资金** @ market_sentiment.py:107-122
```
近 5d 净流入 > 50 亿:  持续大幅流入
> 0:                  小幅净流入
> -50:                小幅净流出
其他:                 持续大幅流出
```

---

### C2. `modules/multi_timeframe.py` — 多周期共振

**单周期评分（基础 50，封顶 ±100）**
```
EMA 完全多头: +30        EMA 完全空头: -30        EMA 部分:    ±10
MACD 强势多: +20         MACD 强势空: -20         MACD 趋弱:   ±5
RSI 超买:    +10         RSI 超卖:    -10         强势/弱势:   ±15
```

**等级**
```
score > 25:  多头
score < -25: 空头
其他:        震荡
strength = |score|
```

**周期权重**：周 0.35 / 日 0.30 / 4h 0.20 / 1h 0.15

---

### C3. `modules/risk_metrics.py` — 风险指标

**核心阈值**
```
VaR(95%):    historic returns 的 5 百分位
risk_free_rate: 0.03 (3%)              [line 32, 65]
Beta:        Cov(stock, bench) / Var(bench)
Alpha:       annual_stock - (rf + Beta × (annual_bench - rf))
Sortino:     √252 × excess_mean / downside_std
Calmar:      annual_return / |max_drawdown|
Vol cone:    rolling 30/60/120/252d，画 10/25/50/75/90 分位
```

> 调参建议：3% 无风险利率写死了，2025-2026 美债 4-5%、A 股逆回购 1.6-1.8%——按市场调。

---

### C4. 基本面估值定位（来自 `analysis.py:170-202`）

**有 PE 历史百分位时**
```
< 0.2:  历史低估区   绿
< 0.4:  偏低估       浅绿
< 0.6:  历史中位     黄
< 0.8:  偏高估       橙
其他:   历史高估区   红
```

**无百分位回退**
```
PE < 10:  低 PE      绿
PE < 20:  合理       浅绿
PE < 30:  中等       黄
PE < 50:  偏高       橙
其他:     高估       红
```

---

### C5. 预警阈值（`modules/alerts.py`）

来自 [app.py:3180-3239](../app.py#L3180) 的 UI 默认值：

| 预警 | UI 默认 | 备注 |
|---|---|---|
| 价格上破 | 当前价 × 1.05 | 5% 上方 |
| 价格下破 | 当前价 × 0.95 | 5% 下方 |
| 量价突破倍数 | 2.0× | 范围 1.5-5.0 |
| KDJ/RSI/CCI/WR | 用各模块默认阈值 | 见 indicator_interpreter |

---

## 推荐的调参顺序（如果你想做"自家风格"）

1. **第一步**：定风格。看 `factor_screener.py` 的三个权重预设，先决定均衡 / 动量 / 价值 偏向哪边。
2. **第二步**：调止损/止盈。`backtest.py` 里默认 5% 止损，先用你的常用品种回测对比 3% / 5% / 8% / `2×ATR` 四档，看夏普和最大回撤。
3. **第三步**：调信号确认数。`signal_generator.py` 的 `confidence = 40 + n*15` —— 如果回测里假信号多，把买/卖触发条件从 ≥2 提到 ≥3。
4. **第四步**：调趋势打分权重。`analysis.py` 的 `calculate_trend_strength` 把 EMA 主导（25 分）调整为更均衡（如 EMA 18 / MACD 18 / KDJ 9 / BB 5）。
5. **第五步**：根据品种分类做差异化。比如低波动蓝筹（茅台）和高波动小盘（创业板）应该用不同的 ATR 倍数和 RSI 阈值——目前是一刀切。

---

## 已知会"看起来好"但实际偏乐观的地方

| 位置 | 问题 | 建议 |
|---|---|---|
| `analysis.py:721` | 趋势分基准 50 + EMA +25 → EMA 单一信号就到 75（强势多头） | 把 EMA 权重降到 ±18 |
| `signal_generator.py:67-68` | "MACD 柱转正" + "MACD 金叉"会同时触发，等于双重计数 | 二选一或合并为 1 条 |
| `indicator_interpreter.py:80-156` | "金叉 + 0 轴上" bull% = 72，单一信号不应这么自信 | 降到 60-65 |
| `backtest.py` 全局 5% 止损 | A 股日内 ±10% 限幅下太紧，正常波动会高频止损 | 改 ATR-based |
| `dragon_screener.py:65` | 默认要求 250d 内 ≥10 次涨停 | 现实中"妖股"3-5 次也算，建议默认 5 |
| `risk_metrics.py:32,65` | 无风险利率硬编 3% | 改为参数化或读取实时国债收益率 |
