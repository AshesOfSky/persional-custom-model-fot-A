/**
 * K 线图：lightweight-charts v5 多窗格（主图蜡烛+EMA隧道/BOLL/VWAP，
 * 副图 VOL/MACD/KDJ），暗色富途风格，十字线 OHLC 图例，
 * 分析结果买卖信号与支撑/压力位 priceLine 虚线标注，
 * 技术分析叠加层（斐波那契回撤/江恩八分位+甘氏扇/艾略特波浪/缠论笔中枢，仅日线）。
 */
import { useCallback, useEffect, useRef, useState } from "react"
import {
  CandlestickSeries,
  ColorType,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IPriceLine,
  type IRange,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts"
import type {
  ChartData,
  Signal,
  SRLevel,
  TechnicalStructureItem,
  TechnicalStructureResponse,
} from "@/api/types"
import { fmtPct, fmtPrice, fmtTime, fmtVolume, trendClass } from "@/lib/fmt"
import { EmptyState, ErrorState, LoadingState } from "@/components/terminal/States"
import { cn } from "@/lib/utils"
import type { MainToggles, SubToggles } from "./ChartToolbar"

const COLORS = {
  up: "#f54345",
  down: "#19b36b",
  upFill: "rgba(245, 67, 69, 0.6)",
  downFill: "rgba(25, 179, 107, 0.6)",
  macdUp: "rgba(245, 67, 69, 0.75)",
  macdDown: "rgba(25, 179, 107, 0.75)",
  ema8: "#f5c542",
  ema21: "#42a5f5",
  ema55: "#ab47bc",
  ema144: "#26a69a",
  bollUpper: "#ab47bc",
  bollMid: "#f5c542",
  bollLower: "#42a5f5",
  vwap: "#ff6d00",
  dif: "#f5c542",
  dea: "#42a5f5",
  k: "#f5c542",
  d: "#42a5f5",
  j: "#ab47bc",
  fib: "#c9a227",
  gannStrong: "#26a69a",
  gannWeak: "rgba(38, 166, 154, 0.45)",
  gannFan: "#ff6d00",
  ewImpulse: "#ec407a",
  ewCorrective: "#ab47bc",
  ewSub: "#7c8db0",
  ewTarget: "#ff6d00",
  chanBi: "#42a5f5",
  chanStableBi: "#26a69a",
  chanSegment: "#ec407a",
  chanZs: "#8a91a6",
  signalBuy: "#22c55e",
  signalSell: "#ff4d57",
  signalPreviewBuy: "#f59e0b",
  signalPreviewSell: "#a78bfa",
}

interface LegendState {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  prevClose: number | null
}

type MainGroups = Record<keyof MainToggles, ISeriesApi<"Line">[]>

function emptyGroups(): MainGroups {
  return {
    emaInner: [], emaOuter: [], boll: [], vwap: [],
    signals: [],
    fib: [], gann: [], elliott: [], chan: [],
  }
}

type MarkerGroup = { api: ISeriesMarkersPluginApi<Time>; data: SeriesMarker<Time>[] }

interface AxisLevelPosition {
  key: string
  level: SRLevel
  y: number
}

interface ChartOverlay {
  fib: { ratio: string | null; label: string; price: number }[]
  gannOctaves: { label: string; price: number; strong: boolean }[]
  gannFan: { points: { time: string; value: number }[] }[]
  elliottSegments: {
    kind: "impulse" | "corrective" | "sub"
    points: { time: string; price: number }[]
  }[]
  elliottLabels: {
    time: string
    label: string
    kind: "impulse" | "corrective" | "sub"
    dir: "up" | "down" | null
    provisional: boolean
  }[]
  elliottTargets: { price: number; label: string }[]
  chanBi: {
    points: { time: string; price: number }[]
    confirmed: boolean
  }[]
  chanStableBi: { points: { time: string; price: number }[] }[]
  chanSegments: { points: { time: string; price: number }[] }[]
  chanActiveBi: { points: { time: string; price: number }[] } | null
  chanZhongshu: {
    startTime: string
    endTime: string
    zg: number
    zd: number
    confirmed: boolean
  }[]
  chanSignals: {
    time: string
    label: string
    type: "buy" | "sell"
    confirmed: boolean
  }[]
}

function structurePoints(
  item: TechnicalStructureItem
): { time: string; price: number }[] {
  const points = item.points.flatMap((point) =>
    point.time ? [{ time: point.time.slice(0, 10), price: point.value }] : []
  )
  if (points.length >= 2) return points
  if (
    item.time &&
    item.end_time &&
    item.price !== null &&
    item.price !== undefined &&
    item.end_price !== null &&
    item.end_price !== undefined
  ) {
    return [
      { time: item.time.slice(0, 10), price: item.price },
      { time: item.end_time.slice(0, 10), price: item.end_price },
    ]
  }
  return []
}

function chartOverlay(technical: TechnicalStructureResponse | null): ChartOverlay | null {
  if (!technical) return null
  const items = technical.sections.overlay.items
  const byKind = (kind: string) => items.filter((item) => item.kind === kind)
  const elliottKind = (value: string | null | undefined): "impulse" | "corrective" | "sub" =>
    value === "impulse" || value === "corrective" ? value : "sub"
  const chanItems = technical.sections.chan.items

  return {
    fib: byKind("fibonacci_level").flatMap((item) =>
      item.price === null || item.price === undefined
        ? []
        : [{ ratio: item.ratio ?? null, label: item.label || item.ratio || "Fib", price: item.price }]
    ),
    gannOctaves: byKind("gann_octave").flatMap((item) =>
      item.price === null || item.price === undefined
        ? []
        : [{ label: item.label || "Gann", price: item.price, strong: item.tags.includes("strong") }]
    ),
    gannFan: byKind("gann_fan").flatMap((item) => {
      const points = structurePoints(item).map((point) => ({ time: point.time, value: point.price }))
      return points.length >= 2 ? [{ points }] : []
    }),
    elliottSegments: byKind("elliott_segment").flatMap((item) => {
      const points = structurePoints(item)
      return points.length >= 2 ? [{ kind: elliottKind(item.role), points }] : []
    }),
    elliottLabels: byKind("elliott_label").flatMap((item) =>
      item.time && item.price !== null && item.price !== undefined
        ? [{
            time: item.time.slice(0, 10),
            label: item.label || "?",
            kind: elliottKind(item.role),
            dir: item.direction === "up" || item.direction === "down" ? item.direction : null,
            provisional: item.provisional,
          }]
        : []
    ),
    elliottTargets: byKind("elliott_target").flatMap((item) =>
      item.price === null || item.price === undefined
        ? []
        : [{ price: item.price, label: item.label || "投射目标" }]
    ),
    chanBi: byKind("chan_bi").flatMap((item) => {
      const points = structurePoints(item)
      return points.length >= 2 ? [{ points, confirmed: item.confirmed }] : []
    }),
    chanStableBi: chanItems
      .filter((item) => item.kind === "stable_bi" && item.confirmed)
      .flatMap((item) => {
        const points = structurePoints(item)
        return points.length >= 2 ? [{ points }] : []
      }),
    chanSegments: chanItems
      .filter((item) => item.kind === "segment" && item.confirmed)
      .flatMap((item) => {
        const points = structurePoints(item)
        return points.length >= 2 ? [{ points }] : []
      }),
    chanActiveBi: (() => {
      const active = byKind("chan_active_bi")[0]
      if (!active) return null
      const points = structurePoints(active)
      return points.length >= 2 ? { points } : null
    })(),
    chanZhongshu: byKind("chan_zhongshu").flatMap((item) =>
      item.time &&
      item.end_time &&
      item.high !== null &&
      item.high !== undefined &&
      item.low !== null &&
      item.low !== undefined
        ? [{
            startTime: item.time.slice(0, 10),
            endTime: item.end_time.slice(0, 10),
            zg: item.high,
            zd: item.low,
            confirmed: item.confirmed,
          }]
        : []
    ),
    chanSignals: byKind("chan_signal").flatMap((item) => {
      if (!item.time || item.price === null || item.price === undefined) return []
      const role = (item.role || "").toLowerCase()
      return [{
        time: item.time.slice(0, 10),
        label: item.label || role || "缠论观察项",
        type: role.startsWith("buy") ? "buy" as const : "sell" as const,
        confirmed: item.confirmed,
      }]
    }),
  }
}

/** 日/周/月传 'YYYY-MM-DD'；分钟线传 unix 秒（服务器已按北京墙钟=UTC 输出，不再转换） */
function toTime(t: string | number): Time {
  return typeof t === "number" ? (t as UTCTimestamp) : t
}

function sortKey(t: string | number): number {
  return typeof t === "number" ? t : Date.parse(`${t}T00:00:00Z`) / 1000
}

/** 把 2 点线段裁剪到 [tmin, tmax] 窗口（线性插值端点值），完全在窗外返回 null */
function clipSeg(
  t0: string, v0: number, t1: string, v1: number,
  tmin: string, tmax: string
): { time: string; value: number }[] | null {
  const k0 = sortKey(t0)
  const k1 = sortKey(t1)
  const kmin = sortKey(tmin)
  const kmax = sortKey(tmax)
  const at = (k: number) => v0 + ((v1 - v0) * (k - k0)) / (k1 - k0 || 1)
  const a = Math.max(Math.min(k0, k1), kmin)
  const b = Math.min(Math.max(k0, k1), kmax)
  if (a >= b) return null
  const timeOf = (k: number): string =>
    k === k0 ? t0 : k === k1 ? t1 : k === kmin ? tmin : tmax
  return [
    { time: timeOf(a), value: at(a) },
    { time: timeOf(b), value: at(b) },
  ]
}

/** 图例时间：分钟线用 UTC 方法直读（北京墙钟） */
function fmtLegendTime(t: string | number): string {
  if (typeof t === "string") return t
  const d = new Date(t * 1000)
  const p = (n: number) => String(n).padStart(2, "0")
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
}

const DATA_MODE: Record<string, { label: string; cls: string }> = {
  live: { label: "实时", cls: "text-futu-teal border-futu-teal/40" },
  delayed: { label: "延迟", cls: "text-futu-teal border-futu-teal/40" },
  cache: { label: "缓存", cls: "text-futu-gold border-futu-gold/40" },
  fallback: { label: "降级", cls: "text-futu-orange border-futu-orange/40" },
  demo: { label: "演示", cls: "text-futu-purple border-futu-purple/40" },
  simulation: { label: "模拟", cls: "text-futu-orange border-futu-orange/40" },
}

function Overlay({ children }: { children: React.ReactNode }) {
  return (
    <div className="absolute inset-0 z-20 flex flex-col justify-center bg-futu-panel">
      {children}
    </div>
  )
}

export function ChartView({
  data,
  loading,
  error,
  onRetry,
  main,
  sub,
  levels,
  signals,
  technical,
  provisionalBarTime,
  overlayNotice,
}: {
  data: ChartData | null
  loading: boolean
  error: string | null
  onRetry: () => void
  main: MainToggles
  sub: SubToggles
  levels: SRLevel[] | null
  signals: Signal[]
  technical: TechnicalStructureResponse | null
  provisionalBarTime: string | null
  overlayNotice: string | null
}) {

  const containerRef = useRef<HTMLDivElement | null>(null)
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const mainGroupsRef = useRef<MainGroups>(emptyGroups())
  const markersRef = useRef<Partial<Record<"signal" | "elliott" | "chan", MarkerGroup>>>({})
  const priceLinesRef = useRef<IPriceLine[]>([])
  const fibLinesRef = useRef<{ line: IPriceLine; title: string }[]>([])
  const rangeRef = useRef<IRange<number> | null>(null)
  const lastDataRef = useRef<ChartData | null>(null)
  const mainRef = useRef(main)
  const levelsRef = useRef(levels)
  const [legend, setLegend] = useState<LegendState | null>(null)
  const [activeSignals, setActiveSignals] = useState<Signal[]>([])
  const [axisLevels, setAxisLevels] = useState<AxisLevelPosition[]>([])
  const [hoveredLevelKey, setHoveredLevelKey] = useState<string | null>(null)

  // The chart construction effect deliberately does not rebuild for display
  // toggles or late-arriving support/resistance levels. Keep the imperative
  // callbacks on the latest values without mutating refs during render.
  useEffect(() => {
    mainRef.current = main
  }, [main])

  useEffect(() => {
    levelsRef.current = levels
  }, [levels])



  const syncAxisLevels = useCallback((
    series: ISeriesApi<"Candlestick">,
    lvls: SRLevel[] | null
  ) => {
    setAxisLevels(
      (lvls ?? []).flatMap((level, index) => {
        const y = series.priceToCoordinate(level.price)
        return y === null
          ? []
          : [{ key: `${level.type}:${level.price}:${index}`, level, y }]
      })
    )
  }, [])

  /** 支撑/压力 priceLine（红压绿撑虚线） */
  const applyPriceLines = useCallback((
    series: ISeriesApi<"Candlestick">,
    lvls: SRLevel[] | null
  ) => {
    priceLinesRef.current.forEach((pl) => series.removePriceLine(pl))
    priceLinesRef.current = []
    lvls?.forEach((l) => {
      if (!Number.isFinite(l.price)) return
      const pl = series.createPriceLine({
        price: l.price,
        color: l.type === "support" ? COLORS.down : COLORS.up,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `${l.type === "support" ? "支撑" : "压力"} · ${l.sources.slice(0, 14)}`,
      })
      priceLinesRef.current.push(pl)
    })
    syncAxisLevels(series, lvls)
  }, [syncAxisLevels])

  // ── 建图（数据 / 副图结构变化时重建；同一数据仅切副图时保留缩放） ──
  useEffect(() => {
    const el = containerRef.current
    if (!el || !data || data.bars.length === 0) return

    const bars = [...data.bars].sort((a, b) => sortKey(a.time) - sortKey(b.time))
    const isMinute = data.timeframe.endsWith("分钟")

    const chart = createChart(el, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "#10141b" },
        textColor: "#8a91a6",
        attributionLogo: false,
        fontSize: 11,
      },
      grid: {
        vertLines: { color: "#1d2230" },
        horzLines: { color: "#1d2230" },
      },
      rightPriceScale: { borderColor: "#2a2f3e" },
      timeScale: {
        borderColor: "#2a2f3e",
        rightOffset: 3,
        timeVisible: isMinute,
        secondsVisible: false,
      },
      crosshair: {
        vertLine: { color: "#3a4156", labelBackgroundColor: "#2a2f3e" },
        horzLine: { color: "#3a4156", labelBackgroundColor: "#2a2f3e" },
      },
      localization: { locale: "zh-CN" },
    })

    // 主图：蜡烛
    const candle = chart.addSeries(
      CandlestickSeries,
      {
        upColor: COLORS.up,
        downColor: COLORS.down,
        borderUpColor: COLORS.up,
        borderDownColor: COLORS.down,
        wickUpColor: COLORS.up,
        wickDownColor: COLORS.down,
        priceLineVisible: false,
      },
      0
    )
    candle.setData(
      bars.map((b) => ({
        time: toTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      }))
    )
    candleRef.current = candle
    mainGroupsRef.current = emptyGroups()

    // 主图指标线（显隐由 main 开关控制，切换无需重建）
    const addMainLine = (
      group: keyof MainToggles,
      pts: { time: string | number; value: number }[] | null | undefined,
      color: string
    ) => {
      if (!pts || pts.length === 0) return
      const s = chart.addSeries(
        LineSeries,
        {
          color,
          lineWidth: 1,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
          visible: mainRef.current[group],
        },
        0
      )
      s.setData(pts.map((p) => ({ time: toTime(p.time), value: p.value })))
      mainGroupsRef.current[group].push(s)
    }
    const ind = data.indicators
    addMainLine("emaInner", ind.emaInner?.fast, COLORS.ema8)
    addMainLine("emaInner", ind.emaInner?.slow, COLORS.ema21)
    addMainLine("emaOuter", ind.emaOuter?.fast, COLORS.ema55)
    addMainLine("emaOuter", ind.emaOuter?.slow, COLORS.ema144)
    if (ind.boll && ind.boll.length > 0) {
      addMainLine("boll", ind.boll.map((p) => ({ time: p.time, value: p.upper })), COLORS.bollUpper)
      addMainLine("boll", ind.boll.map((p) => ({ time: p.time, value: p.mid })), COLORS.bollMid)
      addMainLine("boll", ind.boll.map((p) => ({ time: p.time, value: p.lower })), COLORS.bollLower)
    }
    addMainLine("vwap", ind.vwap, COLORS.vwap)

    // ── 技术分析叠加层（斐波那契/江恩/波浪/缠论，仅日线坐标有效） ──
    const ov = chartOverlay(technical)
    if (ov && data.timeframe === "日线" && bars.length > 1) {
      const tmin = String(bars[0].time)
      const tmax = String(bars[bars.length - 1].time)
      const kmin = sortKey(bars[0].time)
      const kmax = sortKey(bars[bars.length - 1].time)
      const inRange = (t: string) => {
        const k = sortKey(t)
        return k >= kmin && k <= kmax
      }
      const addSeg = (
        group: keyof MainToggles,
        pts: { time: string; value: number }[],
        color: string,
        style: LineStyle = LineStyle.Solid,
        width: 1 | 2 | 3 = 1
      ) => {
        const s = chart.addSeries(
          LineSeries,
          {
            color,
            lineWidth: width,
            lineStyle: style,
            priceLineVisible: false,
            lastValueVisible: false,
            crosshairMarkerVisible: false,
            visible: mainRef.current[group],
            // 叠加线（甘氏扇/斐波那契等）不参与价格轴自动缩放，避免拉爆K线视野
            autoscaleInfoProvider: () => null,
          },
          0
        )
        s.setData(pts.map((p) => ({ time: p.time, value: p.value })))
        mainGroupsRef.current[group].push(s)
      }
      const hline = (
        group: keyof MainToggles,
        price: number,
        color: string,
        style: LineStyle
      ) =>
        addSeg(
          group,
          [
            { time: tmin, value: price },
            { time: tmax, value: price },
          ],
          color,
          style
        )

      // 江恩：八分位水平线（强区实点/弱区淡点）+ 甘氏扇斜线
      ov.gannOctaves.forEach((o) =>
        hline("gann", o.price, o.strong ? COLORS.gannStrong : COLORS.gannWeak,
              o.strong ? LineStyle.Dotted : LineStyle.SparseDotted)
      )
      ov.gannFan.forEach((f) => {
        const [p0, p1] = f.points
        const pts = clipSeg(p0.time, p0.value, p1.time, p1.value, tmin, tmax)
        if (pts) addSeg("gann", pts, COLORS.gannFan)
      })
      // 艾略特波浪：推动浪（品红粗实线）/ 修正浪（紫色虚线）/ 细分浪（灰蓝细虚线）+ 投射目标
      ov.elliottSegments.forEach((seg) => {
        const [p0, p1] = seg.points
        const pts = clipSeg(p0.time, p0.price, p1.time, p1.price, tmin, tmax)
        if (!pts) return
        if (seg.kind === "impulse")
          addSeg("elliott", pts, COLORS.ewImpulse, LineStyle.Solid, 3)
        else if (seg.kind === "corrective")
          addSeg("elliott", pts, COLORS.ewCorrective, LineStyle.Dashed, 2)
        else
          addSeg("elliott", pts, COLORS.ewSub, LineStyle.Dashed, 1)
      })
      ov.elliottTargets.forEach((t) =>
        hline("elliott", t.price, COLORS.ewTarget, LineStyle.SparseDotted)
      )
      // 缠论：当前简化引擎的结构笔/中枢均可能重绘；未确认结构统一使用虚线。
      ov.chanBi.forEach((b) => {
        const [p0, p1] = b.points
        const pts = clipSeg(p0.time, p0.price, p1.time, p1.price, tmin, tmax)
        if (pts)
          addSeg(
            "chan",
            pts,
            COLORS.chanBi,
            b.confirmed ? LineStyle.Solid : LineStyle.Dashed,
            2
          )
      })
      // Prefix-stable Chan structures are deliberately distinct from the
      // legacy provisional bi/zhongshu/signal overlays above.
      ov.chanStableBi.forEach((b) => {
        const [p0, p1] = b.points
        const pts = clipSeg(p0.time, p0.price, p1.time, p1.price, tmin, tmax)
        if (pts) addSeg("chan", pts, COLORS.chanStableBi, LineStyle.Solid, 2)
      })
      ov.chanSegments.forEach((segment) => {
        const [p0, p1] = segment.points
        const pts = clipSeg(p0.time, p0.price, p1.time, p1.price, tmin, tmax)
        if (pts) addSeg("chan", pts, COLORS.chanSegment, LineStyle.Solid, 3)
      })
      if (ov.chanActiveBi) {
        const [p0, p1] = ov.chanActiveBi.points
        const pts = clipSeg(p0.time, p0.price, p1.time, p1.price, tmin, tmax)
        if (pts) addSeg("chan", pts, COLORS.chanBi, LineStyle.Dashed, 1)
      }
      ov.chanZhongshu.forEach((z) => {
        const zg = clipSeg(z.startTime, z.zg, z.endTime, z.zg, tmin, tmax)
        const zd = clipSeg(z.startTime, z.zd, z.endTime, z.zd, tmin, tmax)
        const style = z.confirmed ? LineStyle.Dashed : LineStyle.SparseDotted
        if (zg) addSeg("chan", zg, COLORS.chanZs, style)
        if (zd) addSeg("chan", zd, COLORS.chanZs, style)
      })

      // 标记：波浪枢轴（沿浪向箭头+浪标；细分浪灰色小箭头）、缠论买卖点（红绿箭头+文字）
      const ewMarkers: SeriesMarker<Time>[] = ov.elliottLabels
        .filter((l) => inRange(l.time))
        .sort((a, b) => sortKey(a.time) - sortKey(b.time))
        .map((l) => {
          const up = l.dir !== "down"
          return {
            time: l.time as Time,
            position: up ? ("belowBar" as const) : ("aboveBar" as const),
            color: l.kind === "corrective" ? COLORS.ewCorrective
                 : l.kind === "sub" ? COLORS.ewSub : COLORS.ewImpulse,
            shape: (up ? "arrowUp" : "arrowDown") as "arrowUp" | "arrowDown",
            text: l.provisional ? `${l.label}?` : l.label,
            size: (l.kind === "sub" ? 0 : 1) as 0 | 1,
          }
        })
      const chanMarkers: SeriesMarker<Time>[] = ov.chanSignals
        .filter((s) => inRange(s.time))
        .sort((a, b) => sortKey(a.time) - sortKey(b.time))
        .map((s) => ({
          time: s.time as Time,
          position: s.type === "buy" ? "belowBar" : "aboveBar",
          color: s.type === "buy" ? COLORS.down : COLORS.up,
          shape: s.type === "buy" ? "arrowUp" : "arrowDown",
          text: s.confirmed ? s.label : `${s.label}?`,
          size: 1,
        }))
      markersRef.current = {
        elliott: {
          api: createSeriesMarkers(candle, mainRef.current.elliott ? ewMarkers : []),
          data: ewMarkers,
        },
        chan: {
          api: createSeriesMarkers(candle, mainRef.current.chan ? chanMarkers : []),
          data: chanMarkers,
        },
      }
    } else {
      markersRef.current = {}
    }

    const barTimes = new Set(bars.map((bar) => String(toTime(bar.time))))
    const toDailySignalTime = (time: string) => toTime(time.slice(0, 10))
    const signalMarkers: SeriesMarker<Time>[] = data.timeframe === "日线"
      ? signals
          .filter((signal) => barTimes.has(String(toDailySignalTime(signal.time))))
          .sort((left, right) => sortKey(left.time) - sortKey(right.time))
          .map((signal) => {
            const provisional = signal.status === "provisional"
            const direction = signal.kind === "buy" ? "买" : "卖"
            return {
              time: toDailySignalTime(signal.time),
              position: signal.kind === "buy" ? "belowBar" : "aboveBar",
              color: provisional
                ? signal.kind === "buy"
                  ? COLORS.signalPreviewBuy
                  : COLORS.signalPreviewSell
                : signal.kind === "buy"
                  ? COLORS.signalBuy
                  : COLORS.signalSell,
              shape: signal.kind === "buy" ? "arrowUp" : "arrowDown",
              text: `${provisional ? "预" : ""}${direction}${signal.confidenceLevel || signal.strength ? `·${signal.confidenceLevel ?? signal.strength}` : ""}`,
              size: 1,
            } as SeriesMarker<Time>
          })
      : []
    markersRef.current.signal = {
      api: createSeriesMarkers(
        candle,
        mainRef.current.signals ? signalMarkers : []
      ),
      data: signalMarkers,
    }

    // 副图窗格
    let pane = 1
    if (sub.vol) {
      const vol = chart.addSeries(
        HistogramSeries,
        {
          priceFormat: { type: "volume" },
          priceLineVisible: false,
          lastValueVisible: false,
        },
        pane
      )
      vol.setData(
        bars.map((b) => ({
          time: toTime(b.time),
          value: b.volume,
          color: b.close >= b.open ? COLORS.upFill : COLORS.downFill,
        }))
      )
      pane++
    }
    if (sub.macd && ind.macd && ind.macd.length > 0) {
      const macdPts = ind.macd
      const hist = chart.addSeries(
        HistogramSeries,
        { priceLineVisible: false, lastValueVisible: false },
        pane
      )
      hist.setData(
        macdPts.map((p) => ({
          time: toTime(p.time),
          value: p.hist,
          color: p.hist >= 0 ? COLORS.macdUp : COLORS.macdDown,
        }))
      )
      const dif = chart.addSeries(
        LineSeries,
        { color: COLORS.dif, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
        pane
      )
      dif.setData(macdPts.map((p) => ({ time: toTime(p.time), value: p.dif })))
      const dea = chart.addSeries(
        LineSeries,
        { color: COLORS.dea, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
        pane
      )
      dea.setData(macdPts.map((p) => ({ time: toTime(p.time), value: p.dea })))
      pane++
    }
    if (sub.kdj && ind.kdj && ind.kdj.length > 0) {
      const kdjPts = ind.kdj
      const addKdj = (key: "k" | "d" | "j", color: string) => {
        const s = chart.addSeries(
          LineSeries,
          { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false },
          pane
        )
        s.setData(kdjPts.map((p) => ({ time: toTime(p.time), value: p[key] })))
      }
      addKdj("k", COLORS.k)
      addKdj("d", COLORS.d)
      addKdj("j", COLORS.j)
      pane++
    }

    // 主图窗格占更大高度
    chart.panes().forEach((p, i) => p.setStretchFactor(i === 0 ? 3 : 1))

    // 支撑/压力虚线
    applyPriceLines(candle, levelsRef.current)

    // 斐波那契回撤位 priceLine（右侧标注 "38.2% · 11.25"，仅日线）
    // 注意：lightweight-charts 的 priceLine 标题仅在 axisLabelVisible=true 时渲染
    fibLinesRef.current = []
    if (ov && data.timeframe === "日线") {
      ov.fib.forEach((l) => {
        if (!Number.isFinite(l.price)) return
        const title = `${l.label} · ${l.price.toFixed(2)}`
        const pl = candle.createPriceLine({
          price: l.price,
          color: COLORS.fib,
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: mainRef.current.fib,
          axisLabelColor: COLORS.fib,
          lineVisible: mainRef.current.fib,
          title,
        })
        fibLinesRef.current.push({ line: pl, title })
      })
    }

    // 十字线 OHLC 图例
    const indexByKey = new Map<string, number>()
    bars.forEach((b, i) => indexByKey.set(String(b.time), i))
    const signalsByTime = new Map<string, Signal[]>()
    signals.forEach((signal) => {
      const key = signal.time.slice(0, 10)
      signalsByTime.set(key, [...(signalsByTime.get(key) ?? []), signal])
    })
    const buildLegend = (i: number): LegendState => {
      const b = bars[i]
      return {
        time: fmtLegendTime(b.time),
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
        volume: b.volume,
        prevClose: i > 0 ? bars[i - 1].close : null,
      }
    }
    setLegend(buildLegend(bars.length - 1))
    setActiveSignals([])
    const onMove = (param: MouseEventParams) => {
      const t = param.time
      const idx = t === undefined ? undefined : indexByKey.get(String(t))
      setLegend(buildLegend(idx === undefined ? bars.length - 1 : idx))
      setActiveSignals(
        t === undefined ? [] : signalsByTime.get(String(t).slice(0, 10)) ?? []
      )
    }
    chart.subscribeCrosshairMove(onMove)
    const refreshAxisLevels = () => {
      syncAxisLevels(candle, levelsRef.current)
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(refreshAxisLevels)

    // 恢复缩放（仅切副图、数据未变时）或自适应
    const saved = rangeRef.current
    if (saved && lastDataRef.current === data) {
      chart.timeScale().setVisibleLogicalRange(saved)
    } else {
      chart.timeScale().fitContent()
    }
    refreshAxisLevels()
    rangeRef.current = null
    lastDataRef.current = data

    return () => {
      chart.unsubscribeCrosshairMove(onMove)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(refreshAxisLevels)
      rangeRef.current = chart.timeScale().getVisibleLogicalRange()
      chart.remove()
      candleRef.current = null
      mainGroupsRef.current = emptyGroups()
      markersRef.current = {}
      priceLinesRef.current = []
      fibLinesRef.current = []
    }
  }, [
    applyPriceLines,
    data,
    signals,
    sub.kdj,
    sub.macd,
    sub.vol,
    syncAxisLevels,
    technical,
  ])

  // ── 主图指标/叠加层显隐（不重建图表） ──
  useEffect(() => {
    const g = mainGroupsRef.current
    ;(Object.keys(g) as (keyof MainToggles)[]).forEach((k) => {
      g[k].forEach((s) => s.applyOptions({ visible: main[k] }))
    })
    const mk = markersRef.current
    mk.signal?.api.setMarkers(main.signals ? mk.signal.data : [])
    mk.elliott?.api.setMarkers(main.elliott ? mk.elliott.data : [])
    mk.chan?.api.setMarkers(main.chan ? mk.chan.data : [])
    fibLinesRef.current.forEach(({ line, title }) =>
      line.applyOptions({
        lineVisible: main.fib,
        axisLabelVisible: main.fib,
        title: main.fib ? title : "",
      })
    )
  }, [main])

  // ── 支撑/压力线更新（分析数据可能晚于图表到达） ──
  useEffect(() => {
    if (candleRef.current) applyPriceLines(candleRef.current, levels)
  }, [applyPriceLines, levels])

  // ── 凡总板块图层更新（按钮关闭时立即移除全部点位） ──


  const hasBars = !!data && data.bars.length > 0
  const pctChg =
    legend && legend.prevClose
      ? ((legend.close - legend.prevClose) / legend.prevClose) * 100
      : null
  const legendCls = trendClass(pctChg ?? (legend ? legend.close - legend.open : null))
  const mode = data ? DATA_MODE[data.dataMode] : undefined

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden rounded border border-futu-border bg-futu-panel">
      <div ref={containerRef} className="absolute inset-0" />

      {/* 十字线 OHLC 图例 */}
      {hasBars && legend && (
        <div className="pointer-events-none absolute left-2 top-1.5 z-10 flex max-w-[calc(100%-5rem)] flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px]">
          <span className="tnum text-futu-dim">{legend.time}</span>
          <span className="text-futu-dim">
            开 <span className={cn("tnum", legendCls)}>{fmtPrice(legend.open)}</span>
          </span>
          <span className="text-futu-dim">
            高 <span className={cn("tnum", legendCls)}>{fmtPrice(legend.high)}</span>
          </span>
          <span className="text-futu-dim">
            低 <span className={cn("tnum", legendCls)}>{fmtPrice(legend.low)}</span>
          </span>
          <span className="text-futu-dim">
            收 <span className={cn("tnum", legendCls)}>{fmtPrice(legend.close)}</span>
          </span>
          <span className={cn("tnum", legendCls)}>{fmtPct(pctChg)}</span>
          <span className="tnum text-futu-dim">量 {fmtVolume(legend.volume)}</span>
        </div>
      )}

      {hasBars && main.signals && activeSignals.length > 0 && (
        <div className="pointer-events-none absolute left-2 top-7 z-20 max-w-[min(32rem,calc(100%-6rem))] space-y-1 rounded border border-futu-border bg-futu-panel/95 px-2 py-1.5 text-[10px] leading-relaxed shadow-lg">
          {activeSignals.map((signal, index) => (
            <div key={`${signal.status}:${signal.kind}:${signal.time}:${index}`}>
              <span
                className={
                  signal.status === "provisional"
                    ? "text-futu-orange"
                    : signal.kind === "buy"
                      ? "text-futu-down"
                      : "text-futu-up"
                }
              >
                {signal.status === "provisional" ? "盘中预估" : ""}
                {signal.kind === "buy" ? "买入" : "卖出"} · {signal.time}
              </span>
              {signal.status === "provisional" && (
                <span className="ml-1 rounded border border-futu-orange/40 px-1 text-futu-orange">
                  未确认
                </span>
              )}
              <span className="tnum ml-2 text-futu-text">{fmtPrice(signal.price)}</span>
              <div className="text-futu-sub">{signal.text}</div>
            </div>
          ))}
        </div>
      )}

      {hasBars && axisLevels.map(({ key, level, y }) => (
        <div
          key={key}
          className="absolute right-0 z-30 h-5 w-[78px] -translate-y-1/2 cursor-help"
          style={{ top: y }}
          aria-label={`${level.type === "support" ? "支撑" : "压力"} ${level.price}，来源 ${level.sources}`}
          onMouseEnter={() => setHoveredLevelKey(key)}
          onMouseLeave={() => setHoveredLevelKey(null)}
        >
          {hoveredLevelKey === key && (
            <div className="pointer-events-none absolute right-[82px] top-1/2 w-64 -translate-y-1/2 rounded border border-futu-border bg-futu-panel/98 px-2.5 py-2 text-[10px] leading-relaxed shadow-xl">
              <div className={level.type === "support" ? "font-medium text-futu-down" : "font-medium text-futu-up"}>
                {level.type === "support" ? "支撑位" : "压力位"} · <span className="tnum">{fmtPrice(level.price)}</span>
              </div>
              <div className="mt-1 text-futu-sub">来源与原因：{level.sources}</div>
              {level.distancePct !== undefined && (
                <div className="tnum mt-1 text-futu-dim">距现价 {fmtPct(level.distancePct)}</div>
              )}
            </div>
          )}
        </div>
      ))}

      {/* 数据模式角标 */}
      {hasBars && mode && (
        <span
          className={cn(
            "absolute right-2 top-1.5 z-10 rounded-sm border px-1 py-px text-[10px]",
            mode.cls
          )}
        >
          {mode.label}
        </span>
      )}

      {hasBars && data?.provenance && (
        <div
          className="absolute bottom-2 right-2 z-10 max-w-[58%] rounded border border-futu-divider bg-futu-panel/92 px-2 py-1 text-right text-[9px] leading-4 text-futu-dim shadow"
          title={[
            ...data.provenance.sourceChain,
            ...data.provenance.warnings,
          ].join("\n")}
        >
          <div>
            {data.provenance.provider} · {data.provenance.quality} · {provisionalBarTime
              ? `完成K线 ${fmtTime(data.provenance.lastBarAt)} · 临时K线 ${provisionalBarTime}`
              : `末根K线 ${fmtTime(data.provenance.lastBarAt)}`}
          </div>
          {data.provenance.warnings[0] && (
            <div className="truncate text-futu-gold">
              {data.provenance.warnings[0]}
            </div>
          )}
        </div>
      )}

      {hasBars && overlayNotice && (
        <div className="absolute bottom-2 left-2 z-10 max-w-[calc(100%-1rem)] rounded border border-futu-gold/40 bg-futu-panel/95 px-2 py-1 text-[10px] leading-relaxed text-futu-gold shadow">
          {overlayNotice}
        </div>
      )}

      {loading && !data && (
        <Overlay>
          <LoadingState text="K线加载中…" />
        </Overlay>
      )}
      {error && !data && (
        <Overlay>
          <ErrorState error={error} onRetry={onRetry} />
        </Overlay>
      )}
      {!loading && !error && data && data.bars.length === 0 && (
        <Overlay>
          <EmptyState text="该周期暂无K线数据" />
        </Overlay>
      )}
    </div>
  )
}
