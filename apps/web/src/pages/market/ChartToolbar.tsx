/**
 * 行情页工具栏：周期 / 周期类型切换 + 主图/副图指标开关（富途风格小按钮组，橙色激活态）
 */
import type { Period, Timeframe } from "@/api/types"
import { cn } from "@/lib/utils"

export interface MainToggles {
  emaInner: boolean
  emaOuter: boolean
  boll: boolean
  vwap: boolean
  signals: boolean
  fib: boolean
  gann: boolean
  elliott: boolean
  chan: boolean
}

export interface SubToggles {
  vol: boolean
  macd: boolean
  kdj: boolean
}

const PERIODS: Period[] = ["1M", "3M", "6M", "1Y", "2Y", "5Y"]

const TIMEFRAMES: { label: string; value: Timeframe }[] = [
  { label: "日线", value: "日线" },
  { label: "周线", value: "周线" },
  { label: "月线", value: "月线" },
  { label: "60分", value: "60分钟" },
  { label: "30分", value: "30分钟" },
  { label: "15分", value: "15分钟" },
  { label: "5分", value: "5分钟" },
]

const MAIN_ITEMS: {
  key: keyof MainToggles
  label: string
  title: string
  sectorOnly?: boolean
}[] = [
  { key: "emaInner", label: "EMA内", title: "EMA8 / EMA21 内隧道" },
  { key: "emaOuter", label: "EMA外", title: "EMA55 / EMA144 外隧道" },
  { key: "boll", label: "BOLL", title: "布林带 (20, 2)" },
  { key: "vwap", label: "VWAP", title: "成交量加权均价" },
  {
    key: "signals",
    label: "信号",
    title: "复合买卖信号箭头与确认理由（仅日线；含盘中预估）",
  },
  { key: "fib", label: "斐波那契", title: "斐波那契回撤位水平线（仅日线，来自技术分析引擎）" },
  { key: "gann", label: "江恩", title: "江恩八分位（青）+ 甘氏扇角度线（橙，仅日线）" },
  { key: "elliott", label: "波浪", title: "艾略特波浪：品红推动浪/紫色修正浪 + 浪标（仅日线）" },
  { key: "chan", label: "缠论", title: "缠论：蓝色笔/灰色中枢/红绿买卖点箭头（仅日线）" },
]

const SUB_ITEMS: { key: keyof SubToggles; label: string; title: string }[] = [
  { key: "vol", label: "VOL", title: "成交量副图" },
  { key: "macd", label: "MACD", title: "MACD (12, 26, 9)" },
  { key: "kdj", label: "KDJ", title: "KDJ (9, 3, 3)" },
]

function Tog({
  active,
  onClick,
  title,
  children,
}: {
  active: boolean
  onClick: () => void
  title?: string
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn(
        "h-6 rounded-sm px-1.5 text-[11px] leading-none transition-colors",
        active
          ? "bg-futu-orange/15 font-medium text-futu-orange"
          : "text-futu-sub hover:bg-futu-hover hover:text-futu-text"
      )}
    >
      {children}
    </button>
  )
}

function Divider() {
  return <span className="mx-1 h-4 w-px shrink-0 bg-futu-divider" />
}

const FIB_LOOKBACKS: { value: number; label: string }[] = [
  { value: 66, label: "近3月" },
  { value: 132, label: "近6月" },
  { value: 250, label: "近1年" },
  { value: 500, label: "近2年" },
  { value: 0, label: "全部" },
]

export function ChartToolbar({
  period,
  onPeriod,
  timeframe,
  onTimeframe,
  main,
  onMain,
  sub,
  onSub,
  fibLookback,
  onFibLookback,
}: {
  period: Period
  onPeriod: (p: Period) => void
  timeframe: Timeframe
  onTimeframe: (t: Timeframe) => void
  main: MainToggles
  onMain: (m: MainToggles) => void
  sub: SubToggles
  onSub: (s: SubToggles) => void
  fibLookback: number
  onFibLookback: (n: number) => void
}) {
  const intraday = !(["日线", "周线", "月线"] as Timeframe[]).includes(
    timeframe
  )
  return (
    <div className="flex flex-wrap items-center gap-x-0.5 gap-y-1 rounded border border-futu-border bg-futu-panel px-2 py-1.5">
      {intraday ? (
        <span
          className="rounded-sm border border-futu-teal/40 bg-futu-teal/10 px-1.5 py-0.5 text-[10px] text-futu-teal"
          title="当前免费原生分钟源只准入最近一个完整交易日，不伪造更长历史"
        >
          原生分时 · 最近交易日
        </span>
      ) : (
        PERIODS.map((p) => (
          <Tog key={p} active={period === p} onClick={() => onPeriod(p)} title={`近${p}`}>
            {p}
          </Tog>
        ))
      )}
      <Divider />
      {TIMEFRAMES.map((t) => (
        <Tog
          key={t.value}
          active={timeframe === t.value}
          onClick={() => onTimeframe(t.value)}
          title={t.value}
        >
          {t.label}
        </Tog>
      ))}
      <Divider />
      <span className="mr-0.5 text-[10px] text-futu-dim">主图</span>
      {MAIN_ITEMS.map((it) => (
        <Tog
          key={it.key}
          active={main[it.key]}
          onClick={() => onMain({ ...main, [it.key]: !main[it.key] })}
          title={it.title}
        >
          {it.label}
        </Tog>
      ))}
      {main.fib && (
        <select
          value={fibLookback}
          onChange={(e) => onFibLookback(Number(e.target.value))}
          title="斐波那契回撤区间（自动取窗口内最高/低点为起终点）"
          className="h-6 rounded-sm border border-futu-border bg-futu-elevated px-1 text-[11px] text-futu-orange outline-none"
        >
          {FIB_LOOKBACKS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
      <Divider />
      <span className="mr-0.5 text-[10px] text-futu-dim">副图</span>
      {SUB_ITEMS.map((it) => (
        <Tog
          key={it.key}
          active={sub[it.key]}
          onClick={() => onSub({ ...sub, [it.key]: !sub[it.key] })}
          title={it.title}
        >
          {it.label}
        </Tog>
      ))}
    </div>
  )
}
