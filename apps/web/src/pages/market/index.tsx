/**
 * 行情页（K线 + 指标 + 右侧信息面板）— Market_UI
 * 布局：左 Watchlist ｜ 中：报价头部条 + 指标工具栏 + K线图 ｜ 右：300px 信息面板
 */
import { useCallback, useEffect, useState } from "react"
import { api, useApi } from "@/api/client"
import { technicalStructureLevels } from "@/api/shared"
import type { Period, Timeframe } from "@/api/types"
import { useStock } from "@/state/stock"
import { Watchlist } from "@/components/layout/Watchlist"
import { QuoteHeader } from "./QuoteHeader"
import { ChartToolbar, type MainToggles, type SubToggles } from "./ChartToolbar"
import { ChartView } from "./ChartView"
import { RightPanel } from "./RightPanel"
import { withIntradayDisplayBar } from "./intradayDisplayBar"
import { useRefreshableQuote } from "./useRefreshableQuote"

interface MarketPageProps {
  exportMode?: boolean
  onExportSettled?: () => void
}

export default function MarketPage({
  exportMode = false,
  onExportSettled,
}: MarketPageProps) {
  const { currentCode, currentName, setStock } = useStock()
  const fundamentalApplicable = !currentCode.trim().toUpperCase().endsWith(".TI")
  const [period, setPeriod] = useState<Period>(exportMode ? "2Y" : "1Y")
  const [timeframe, setTimeframe] = useState<Timeframe>("日线")
  const [mainToggles, setMainToggles] = useState<MainToggles>(
    exportMode
      ? {
          emaInner: true,
          emaOuter: true,
          boll: true,
          vwap: true,
          signals: true,
          fib: true,
          gann: true,
          elliott: true,
          chan: true,
        }
      : {
          emaInner: true,
          emaOuter: true,
          boll: false,
          vwap: false,
          signals: true,
          fib: false,
          gann: false,
          elliott: false,
          chan: false,
        }
  )
  const [subToggles, setSubToggles] = useState<SubToggles>(
    exportMode
      ? { vol: true, macd: true, kdj: true }
      : { vol: true, macd: true, kdj: false }
  )

  const chart = useApi(
    () => api.chart(currentCode, period, timeframe),
    `chart:${currentCode}:${period}:${timeframe}`
  )
  const chartFingerprint = chart.data?.provenance?.fingerprint ?? null
  const chartReload = chart.reload
  const [chartRefreshCheck, setChartRefreshCheck] = useState<{
    code: string
    fingerprint: string | null
  } | null>(null)
  const onQuoteRefreshed = useCallback(() => {
    setChartRefreshCheck({
      code: currentCode,
      fingerprint: chartFingerprint,
    })
    chartReload()
  }, [chartFingerprint, chartReload, currentCode])
  const quote = useRefreshableQuote(currentCode, onQuoteRefreshed)
  const displayChart = withIntradayDisplayBar(chart.data, quote.data)
  const analysisQuoteKey = quote.data?.provenance.requestId ?? (
    quote.loading ? "waiting-for-quote" : "confirmed-only"
  )
  const analysis = useApi(
    quote.loading ? null : () => api.analysis(currentCode, quote.data ?? undefined),
    `analysis:${currentCode}:${analysisQuoteKey}`
  )
  const realtimePreview = analysis.data?.realtimeSignalPreview ?? null
  const chartSignals = [
    ...(analysis.data?.signals ?? []),
    ...(timeframe === "日线" ? (realtimePreview?.signals ?? []) : []),
  ]
  const fundamental = useApi(
    fundamentalApplicable ? () => api.fundamental(currentCode) : null,
    `shared-fundamental:${currentCode}:${fundamentalApplicable}`
  )

  // Shared technical overlays are queried with the exact same period as the
  // chart.  A response is admitted to the canvas only when both fingerprints
  // match, so two independently fetched bar series can never be mixed.
  const needOverlay =
    mainToggles.fib || mainToggles.gann || mainToggles.elliott || mainToggles.chan
  const tech = useApi(
    needOverlay && timeframe === "日线"
      ? () => api.tech(currentCode, period, timeframe)
      : null,
    `shared-tech-overlay:${currentCode}:${period}:${needOverlay}:${timeframe}`
  )
  // The old independent Fibonacci request is gone.  The selector remains a
  // chart-window shortcut: changing it updates the chart period, keeping the
  // chart and shared structure query identical.
  const [fibLookback, setFibLookback] = useState(250)
  const currentTech = tech.data
  const overlayFingerprintMatches = Boolean(
    currentTech &&
    chartFingerprint &&
    currentTech.data_fingerprint === chartFingerprint
  )

  let overlayNotice: string | null = null
  if (needOverlay && timeframe !== "日线") {
    overlayNotice = "结构叠加层仅支持日线，当前未绘制。"
  } else if (needOverlay && tech.error) {
    overlayNotice = `技术叠加未绘制：${tech.error}`
  } else if (needOverlay && tech.loading) {
    overlayNotice = "共享技术结构计算中，确认同一数据批次后绘制。"
  } else if (needOverlay && currentTech && !chartFingerprint) {
    overlayNotice = "行情图缺少批次标识，已阻止技术结构混屏。"
  } else if (needOverlay && currentTech && !overlayFingerprintMatches) {
    overlayNotice = "技术结构与行情 K 线的数据批次不一致，已阻止混屏。"
  } else if (
    needOverlay &&
    mainToggles.chan &&
    currentTech &&
    overlayFingerprintMatches
  ) {
    overlayNotice =
      currentTech.continuity === "continued"
        ? "缠论连续状态：已续接。"
        : currentTech.continuity === "rejected"
          ? "缠论连续状态：旧状态已拒绝，已重新初始化。"
          : "缠论连续状态：已初始化。"
  } else if (needOverlay && !currentTech && !tech.loading) {
    overlayNotice = "共享技术结构不可用，未回退旧版叠加层。"
  }
  let chartRefreshNotice: string | null = null
  if (chartRefreshCheck?.code === currentCode) {
    if (chart.loading) {
      chartRefreshNotice = "最新报价已返回，正在同步当前 K 线一次…"
    } else if (chart.error) {
      chartRefreshNotice = `最新报价已保留；图表同步失败：${chart.error}`
    } else if (chart.data) {
      chartRefreshNotice =
        chartFingerprint === chartRefreshCheck.fingerprint
          ? displayChart.provisionalBarTime
            ? "完成日线源尚未结算，已用最新报价更新临时K线。"
            : "图表源暂无新数据，已保留当前 K 线。"
          : "当前 K 线已同步最新数据。"
    }
  }
  let realtimePreviewNotice: string | null = null
  const previewSession = quote.data?.sourceSnapshot?.session_status
  const previewLabel = previewSession === "midday_break" ? "午间11:30快照预估"
    : previewSession === "after_close" ? "收盘15:00快照预估" : "盘中预估"
  if (displayChart.provisionalBarTime && analysis.loading) {
    realtimePreviewNotice = `${previewLabel}信号计算中…`
  } else if (displayChart.provisionalBarTime && analysis.error) {
    realtimePreviewNotice = `预买/预卖计算未完成：${analysis.error}`
  } else if (displayChart.provisionalBarTime && realtimePreview?.available) {
    if (realtimePreview.signals.length > 0) {
      const labels = realtimePreview.signals.map((signal) => {
        const direction = signal.kind === "buy" ? "预买" : "预卖"
        const level = signal.confidenceLevel ?? signal.strength ?? ""
        const score = signal.confidence === null || signal.confidence === undefined
          ? ""
          : ` ${signal.confidence.toFixed(0)}/100（非概率）`
        return `${direction}${level ? `·${level}` : ""}${score}`
      })
      realtimePreviewNotice = `${previewLabel}：${labels.join(" / ")}；收盘前可能变化。`
    } else {
      realtimePreviewNotice =
        `${previewLabel}：当前未形成买卖信号（需至少 2 个独立确认维度）；随实时价量变化。`
    }
  } else if (
    displayChart.provisionalBarTime &&
    realtimePreview &&
    !realtimePreview.available
  ) {
    const reasons: Record<string, string> = {
      "realtime signal preview requires a valid quote": "报价尚未满足新鲜度要求",
      "realtime signal preview requires verified trade status": "交易状态尚未核验",
      "midday quote is not the verified 11:30 session snapshot": "缺少已核验的午间11:30快照",
      "after-close quote is not the verified 15:00 snapshot": "缺少已核验的收盘15:00快照",
      "completed daily data already covers the quote date": "当日日线已经确认，无需盘中预估",
    }
    const reason = realtimePreview.warnings.map((warning) => reasons[warning]).find(Boolean)
    realtimePreviewNotice = `${previewLabel}暂不可用${reason ? `：${reason}` : ""}；确认信号仍截至上一根完成日 K。`
  }
  const combinedOverlayNotice =
    [
      chartRefreshNotice,
      displayChart.notice,
      realtimePreviewNotice,
      overlayNotice,
    ]
      .filter(Boolean)
      .join("；") || null

  // 报价 / 图表返回的权威名称回填全局状态
  useEffect(() => {
    const name = quote.data?.name || chart.data?.name
    if (name && name !== currentName) setStock(currentCode, name)
  }, [quote.data, chart.data, currentCode, currentName, setStock])

  const marketExportSettled =
    !quote.loading &&
    Boolean(quote.data || quote.error) &&
    !chart.loading &&
    Boolean(chart.data || chart.error) &&
    !analysis.loading &&
    Boolean(analysis.data || analysis.error) &&
    (!fundamentalApplicable ||
      (!fundamental.loading && Boolean(fundamental.data || fundamental.error))) &&
    (!needOverlay ||
      (!tech.loading && Boolean(tech.data || tech.error)))
  useEffect(() => {
    if (exportMode && marketExportSettled) onExportSettled?.()
  }, [exportMode, marketExportSettled, onExportSettled])

  // 支撑/压力位画到主图（红压绿撑虚线）
  const levels = needOverlay
    ? overlayFingerprintMatches && currentTech
      ? technicalStructureLevels(currentTech)
      : null
    : analysis.data
      ? [...analysis.data.supports, ...analysis.data.resistances]
      : null

  const onFibLookback = (lookback: number) => {
    setFibLookback(lookback)
    const nextPeriod: Period =
      lookback === 66 ? "3M" :
      lookback === 132 ? "6M" :
      lookback === 250 ? "1Y" :
      lookback === 500 ? "2Y" : "5Y"
    setPeriod(nextPeriod)
  }

  return (
    <>
      {!exportMode && <Watchlist />}
      <div className="flex min-w-0 flex-1 flex-col gap-2 overflow-hidden p-2">
        <QuoteHeader
          quote={quote.data}
          loading={quote.loading}
          error={quote.error}
          onRetry={quote.reload}
          fallbackCode={currentCode}
          fallbackName={currentName}
          onRefresh={quote.refresh}
          refreshPhase={quote.refreshPhase}
          refreshError={quote.refreshError}
          cooldownRemaining={quote.cooldownRemaining}
          refreshedAt={quote.refreshedAt}
        />
        <ChartToolbar
          period={period}
          onPeriod={setPeriod}
          timeframe={timeframe}
          onTimeframe={setTimeframe}
          main={mainToggles}
          onMain={setMainToggles}
          sub={subToggles}
          onSub={setSubToggles}
          fibLookback={fibLookback}
          onFibLookback={onFibLookback}
        />
        <ChartView
          data={displayChart.data}
          loading={chart.loading}
          error={chart.error}
          onRetry={chart.reload}
          main={mainToggles}
          sub={subToggles}
          levels={levels}
          signals={chartSignals}
          technical={overlayFingerprintMatches ? currentTech : null}
          provisionalBarTime={displayChart.provisionalBarTime}
          overlayNotice={combinedOverlayNotice}
        />
      </div>
      <RightPanel
        quote={quote}
        analysis={analysis}
        fundamental={fundamental}
        fundamentalApplicable={fundamentalApplicable}
      />
    </>
  )
}
