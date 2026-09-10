import type { AnalysisComponentScore, AnalysisResult, Bar, BollPoint, ChartData, ChanState, DataMode, DataProvenance, FundamentalGroup, FundamentalSnapshotField, FundamentalSnapshotRequest, FundamentalSnapshotResponse, HealthResponse, KdjPoint, LinePoint, MacdPoint, Period, Quote, QuoteSnapshotRequest, QuoteSnapshotResponse, RealtimeSignalPreview, ResearchDataReference, ResearchEvidenceDimension, SharedInstrumentIdView, SharedInstrumentSearchItem, SharedInstrumentSearchResponse, Signal, SRLevel, StockRef, TechnicalStructureResponse, TechnicalStructureSectionKey, Timeframe, WatchlistEntryCreateRequest, WatchlistEntriesReorderRequest, WatchlistEntryListResponse, WatchlistEntryPatchRequest, WatchlistEntryResponse, WatchlistEntryView, WatchlistRole } from "./types"



export type SharedInstrumentId = SharedInstrumentIdView

export type { SharedInstrumentSearchItem, SharedInstrumentSearchResponse }



interface SharedBar {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
  amount: number | null
}



interface SharedIndicatorPoint {
  timestamp: string
  value: number
}



interface SharedIndicatorSeries {
  points: SharedIndicatorPoint[]
}



export interface SharedMarketBarsView {
  schema_version: "market-bars-view/v1"
  data_fingerprint: string
  data: {
    instrument: SharedInstrumentId
    timeframe: string
    purpose: string
    bars: SharedBar[]
    provider: string
    mode: string
    quality: string
    as_of: string
    fetched_at: string
    last_bar_at: string | null
    timezone: string
    adjustment: string
    freshness_seconds: number
    is_synthetic: boolean
    warnings: string[]
    request_id: string
    source_chain: string[]
  }
  indicator_profile: {
    name: string
    version: string
    formula_source: string
    ema_periods: number[]
    macd_fast: number
    macd_slow: number
    macd_signal: number
    bollinger_period: number
    bollinger_std_dev: number
    kdj_n: number
    kdj_m1: number
    kdj_m2: number
    rsi_period: number
    atr_period: number
  }
  indicators: {
    ema: {
      ema8: SharedIndicatorSeries
      ema13: SharedIndicatorSeries
      ema21: SharedIndicatorSeries
      ema55: SharedIndicatorSeries
      ema144: SharedIndicatorSeries
      ema169: SharedIndicatorSeries
      ema288: SharedIndicatorSeries
      ema338: SharedIndicatorSeries
    }
    macd: {
      dif: SharedIndicatorSeries
      dea: SharedIndicatorSeries
      hist: SharedIndicatorSeries
    }
    bollinger: {
      mid: SharedIndicatorSeries
      upper: SharedIndicatorSeries
      lower: SharedIndicatorSeries
    }
    kdj: {
      k: SharedIndicatorSeries
      d: SharedIndicatorSeries
      j: SharedIndicatorSeries
    }
    rsi: SharedIndicatorSeries
    atr: SharedIndicatorSeries
  }
}



export type SharedQuoteQuery = QuoteSnapshotRequest & {
  temporal_mode: "latest"
  as_of: null
  requested_at: string
  purpose: "research"
  max_live_age_seconds: number
}



export type SharedQuoteViewResponse = QuoteSnapshotResponse



export interface SharedDataQuery {
  instrument: SharedInstrumentId
  timeframe: "1m" | "5m" | "15m" | "30m" | "1h" | "1d" | "1w" | "1mo"
  purpose: "research"
  adjustment: "forward" | "none"
  start: string
  end: string
  as_of: string
}



export type SharedCoreTimeframe = SharedDataQuery["timeframe"]



export function buildChanStateKey(
  instrument: SharedInstrumentId,
  timeframe: SharedCoreTimeframe
): string {
  return [
    "chan-state-1",
    `market=${instrument.market}`,
    `exchange=${instrument.exchange}`,
    `asset_type=${instrument.asset_type}`,
    `symbol=${instrument.symbol}`,
    `currency=${instrument.currency}`,
    `timeframe=${timeframe}`,
  ].join("|")
}



export interface SharedResearchRequest {
  query: SharedDataQuery
  benchmark_query?: SharedDataQuery | null
  display_name?: string
  has_position: boolean
  provisional_quote?: QuoteSnapshotResponse["quote"] | null
}



export interface SharedTechnicalStructureRequest extends SharedResearchRequest {
  prior_state?: ChanState
}



interface SharedScoreComponent {
  name: string
  score: number
  weight: number
  enabled: boolean
  evidence: string[]
  warnings: string[]
}



interface SharedKeyLevel {
  kind: string
  price: number
  provenance: string
  status: "provisional" | "confirmed"
}



interface SharedInvalidation {
  description: string
  price: number | null
  confirmation: string
}



interface SharedAnalysisSnapshot {
  snapshot_id: string
  timeframe: string
  as_of: string
  bar_status: "provisional" | "confirmed"
  engine_version: string
  config_version: string
  component_scores: SharedScoreComponent[]
  evidence_score: number
  action: "long" | "hold" | "reduce" | "exit" | "cash"
  key_levels: SharedKeyLevel[]
  invalidation: SharedInvalidation[]
  explanations: string[]
  warnings: string[]
}



interface SharedResearchDataReference {
  request_id: string
  instrument: SharedInstrumentId
  timeframe: string
  purpose: string
  adjustment: "forward" | "backward" | "none"
  provider: string
  mode: string
  quality: "valid" | "stale" | "partial" | "unavailable"
  as_of: string
  fetched_at: string
  last_bar_at: string | null
  timezone: string
  freshness_seconds: number
  is_synthetic: boolean
  source_chain: string[]
  source_warnings: string[]
  bar_count: number
}



interface SharedSignalPoint {
  time: string
  kind: "buy" | "sell"
  price: number
  strength: string | null
  confidence_level?: "弱" | "中" | "中强" | "强" | null
  confidence: number | null
  confirmations: string[]
  confidence_components?: {
    version: "signal-evidence-v2"
    semantics: "signal_evidence_score_not_probability"
    independent_bucket_count: number
    bucket_score: number
    directional_trend_score: number
    trend_evidence_status: "available" | "insufficient_history"
  } | null
}



interface SharedTechnicalSummary {
  score_semantics: "evidence_score_not_probability"
  trend_strength: {
    score: number | null
    level: string | null
    signal_summary: string | null
  } | null
  multi_timeframe: {
    alignment: string | null
    dominant_trend: string | null
    summary: string | null
    alignment_score: number | null
    mtf_score: number | null
  } | null
  divergence: {
    active_count: number
    bullish_active: boolean
    bearish_active: boolean
    strongest_description: string | null
  } | null
  composite_signal: {
    current_signal: string | null
    strength: string | null
    recent_buy_count: number
    recent_sell_count: number
    signals: SharedSignalPoint[]
  } | null
  legacy_directional_evidence_status: "absent" | "present_uncalibrated"
}



export interface SharedResearchAnalysisResponse {
  schema_version: "research-view/v1"
  data_ref: SharedResearchDataReference
  snapshot: SharedAnalysisSnapshot
  market_view: SharedMarketBarsView
  technical_summary: SharedTechnicalSummary
  section_status: {
    data_ref: "available"
    snapshot: "available"
    market_view: "available"
    technical_summary: "available" | "partial" | "unavailable"
  }
  warnings: string[]
  realtime_signal_preview?: {
    schema_version: "realtime-signal-preview/v1"
    available: boolean
    status: "provisional"
    bar_time: string
    completed_through: string | null
    daily_request_id: string
    quote_request_id: string
    daily_provider: string
    quote_provider: string
    quote_mode: "live" | "delayed"
    quote_quality: "valid" | "stale"
    score_semantics: "signal_evidence_score_not_probability"
    signals: SharedSignalPoint[]
    warnings: string[]
    advisory_only: true
    formal_use_eligible: false
  } | null
}



const DATA_MODES = new Set<DataMode>([
  "live",
  "delayed",
  "cache",
  "fallback",
  "demo",
  "simulation",
])



export function normalizeDataMode(mode: string): DataMode {
  return DATA_MODES.has(mode as DataMode) ? (mode as DataMode) : "fallback"
}



export function searchItemMarket(
  item: SharedInstrumentSearchItem
): StockRef["market"] {
  const assetType = item.instrument.asset_type
  if (assetType === "index" || assetType === "sector_index") return "INDEX"
  if (assetType === "etf" || assetType === "fund") return "ETF"
  if (assetType === "futures") return "FUTURES"
  if (item.instrument.market === "CN") return "A"
  if (item.instrument.market === "HK") return "HK"
  return "US"
}



export function mapSearchResponse(
  response: SharedInstrumentSearchResponse
): StockRef[] {
  return dedupeInstrumentSearchItems(response.items).map((item) => ({
    code: item.instrument.symbol,
    name: item.display_name,
    market: searchItemMarket(item),
  }))
}



function codeToken(value: string): string {
  return value.trim().toUpperCase()
}



export function sharedInstrumentIdentityKey(
  instrument: SharedInstrumentId
): string {
  return [
    instrument.market,
    instrument.exchange,
    instrument.asset_type,
    instrument.currency,
    codeToken(instrument.symbol),
  ].join("|")
}



export function dedupeInstrumentSearchItems(
  items: readonly SharedInstrumentSearchItem[]
): SharedInstrumentSearchItem[] {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = sharedInstrumentIdentityKey(item.instrument)
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}



export function findWatchlistEntryByInstrument(
  response: WatchlistEntryListResponse,
  instrument: SharedInstrumentId
): WatchlistEntryView | null {
  const target = sharedInstrumentIdentityKey(instrument)
  return (
    response.items.find(
      (entry) =>
        !entry.deleted_at &&
        sharedInstrumentIdentityKey(entry.instrument) === target
    ) ?? null
  )
}



function symbolBase(value: string): string {
  return codeToken(value).split(".", 1)[0]
}



export function normalizeInstrumentSearchQuery(value: string): string {
  const token = codeToken(value)
  const match = /^(\d{6})\.(SS|SH|SZ)$/.exec(token)
  return match?.[1] ?? value.trim()
}



function exchangeFromCode(value: string): string | null {
  const match = /\.(SS|SH|SZ)$/.exec(codeToken(value))
  if (!match) return null
  return match[1] === "SZ" ? "SZSE" : "SSE"
}



export function findExactInstrument(
  response: SharedInstrumentSearchResponse,
  code: string
): SharedInstrumentSearchItem | null {
  const target = codeToken(code)
  const targetBase = symbolBase(code)
  const targetExchange = exchangeFromCode(code)
  const exactSymbols = dedupeInstrumentSearchItems(
    response.items.filter(
      (item) => codeToken(item.instrument.symbol) === target
    )
  )
  if (exactSymbols.length === 1) return exactSymbols[0]
  if (exactSymbols.length > 1) return null

  const compatible = dedupeInstrumentSearchItems(
    response.items.filter((item) => {
      const sameBase =
        symbolBase(item.legacy_code) === targetBase ||
        symbolBase(item.instrument.symbol) === targetBase
      const sameExchange =
        !targetExchange || item.instrument.exchange === targetExchange
      return sameBase && sameExchange
    })
  )
  return compatible.length === 1 ? compatible[0] : null
}



const PERIOD_MONTHS: Record<Period, number> = {
  "1M": 1,
  "3M": 3,
  "6M": 6,
  "1Y": 12,
  "2Y": 24,
  "5Y": 60,
}



export function buildSharedDataQuery(
  instrument: SharedInstrumentId,
  period: Period,
  timeframe: Timeframe,
  now = new Date()
): SharedDataQuery {
  const start = new Date(now)
  if (INTRADAY_TIMEFRAMES.has(timeframe)) {
    // The admitted free minute source is explicitly latest-session only.
    // Four calendar days includes Friday for a Monday pre-open query without
    // pretending that a longer period is available.
    start.setUTCDate(start.getUTCDate() - 4)
  } else {
    const originalDay = start.getUTCDate()
    start.setUTCDate(1)
    start.setUTCMonth(start.getUTCMonth() - PERIOD_MONTHS[period])
    const daysInTargetMonth = new Date(
      Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 0)
    ).getUTCDate()
    start.setUTCDate(Math.min(originalDay, daysInTargetMonth))
  }

  const timeframeMap = {
    "日线": "1d",
    "周线": "1w",
    "月线": "1mo",
    "60分钟": "1h",
    "30分钟": "30m",
    "15分钟": "15m",
    "5分钟": "5m",
  } as const
  const asOf = now.toISOString()
  const isIndex =
    instrument.asset_type === "index" || instrument.asset_type === "sector_index"
  return {
    instrument,
    timeframe: timeframeMap[timeframe],
    purpose: "research",
    adjustment:
      isIndex || INTRADAY_TIMEFRAMES.has(timeframe) ? "none" : "forward",
    start: start.toISOString(),
    end: asOf,
    as_of: asOf,
  }
}



export function buildSharedQuoteQuery(
  instrument: SharedInstrumentId,
  now = new Date()
): SharedQuoteQuery {
  return {
    instrument,
    purpose: "research",
    temporal_mode: "latest",
    as_of: null,
    requested_at: now.toISOString(),
    max_live_age_seconds: 90,
  }
}



function optionalFinite(
  value: number | null | undefined,
  label: string
): void {
  if (value !== null && value !== undefined && !Number.isFinite(value)) {
    throw new Error(`共享报价 ${label} 不是有限数值`)
  }
}



export function mapSharedQuoteResponse(
  response: SharedQuoteViewResponse,
  item: SharedInstrumentSearchItem,
  query: SharedQuoteQuery
): Quote {
  if (response.schema_version !== "quote-view/v2") {
    throw new Error(`不支持的共享报价契约：${response.schema_version}`)
  }
  const quote = response.quote
  if (!sameInstrument(quote.instrument, query.instrument)) {
    throw new Error("共享报价证券身份与请求不一致")
  }
  if (quote.purpose !== query.purpose) {
    throw new Error("共享报价用途与请求不一致")
  }
  if (
    query.temporal_mode !== "latest" ||
    query.as_of !== null ||
    quote.temporal_mode !== "latest"
  ) {
    throw new Error("共享报价未使用明确的 latest 时间语义")
  }
  const requestedAt = finiteInstant(
    query.requested_at,
    "quote requested_at"
  )
  if (
    quote.requested_at === null ||
    finiteInstant(quote.requested_at, "quote response requested_at") !==
      requestedAt
  ) {
    throw new Error("共享报价 requested_at 与请求不一致")
  }
  const asOf = finiteInstant(quote.as_of, "quote authoritative as_of")
  const fetchedAt = finiteInstant(quote.fetched_at, "quote fetched_at")
  const dataTime = finiteInstant(quote.data_time, "quote data_time")
  const providerUpdatedAt = finiteInstant(
    quote.provider_updated_at,
    "quote provider_updated_at"
  )
  if (requestedAt > asOf || asOf !== fetchedAt) {
    throw new Error("共享最新报价的请求时间、快照时点与抓取完成时间不自洽")
  }
  if (dataTime > asOf || providerUpdatedAt > asOf) {
    throw new Error("共享报价包含未来时间证据")
  }
  const computedFreshness = Math.max(0, (asOf - dataTime) / 1000)
  if (
    !Number.isFinite(quote.freshness_seconds) ||
    quote.freshness_seconds < 0 ||
    Math.abs(quote.freshness_seconds - computedFreshness) > 1
  ) {
    throw new Error("共享报价新鲜度与行情时间不一致")
  }
  const provider = quote.provider.trim().toLowerCase()
  if (!provider || provider === "unknown" || provider === "none") {
    throw new Error("共享报价缺少明确数据提供方")
  }
  if (quote.source_chain.length === 0) {
    throw new Error("共享报价缺少来源链")
  }
  if (quote.is_synthetic) {
    throw new Error("共享报价拒绝模拟或合成数据")
  }
  if (quote.mode !== "live" && quote.mode !== "delayed") {
    throw new Error(`共享报价返回了非原生数据模式：${quote.mode}`)
  }
  if (quote.quality !== "valid" && quote.quality !== "stale") {
    throw new Error(`共享报价返回了不可展示的数据质量：${quote.quality}`)
  }
  if (quote.currency !== query.instrument.currency) {
    throw new Error("共享报价币种与证券身份不一致")
  }
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: quote.timezone }).format(
      new Date(quote.data_time)
    )
  } catch {
    throw new Error("共享报价时区无效")
  }

  const requiredNumbers = [
    quote.last_price,
    quote.previous_close,
    quote.open,
    quote.high,
    quote.low,
    quote.change,
    quote.change_percent,
    quote.volume_lots,
    quote.amount,
    quote.freshness_seconds,
  ]
  if (requiredNumbers.some((value) => !Number.isFinite(value))) {
    throw new Error("共享报价包含非法数值")
  }
  if (
    quote.last_price <= 0 ||
    quote.previous_close <= 0 ||
    quote.open <= 0 ||
    quote.high <= 0 ||
    quote.low <= 0 ||
    quote.volume_lots < 0 ||
    quote.amount < 0 ||
    quote.high < Math.max(quote.open, quote.low, quote.last_price) ||
    quote.low > Math.min(quote.open, quote.high, quote.last_price)
  ) {
    throw new Error("共享报价不满足价格或成交约束")
  }
  const expectedChange = quote.last_price - quote.previous_close
  const expectedPercent = (expectedChange / quote.previous_close) * 100
  if (
    Math.abs(quote.change - expectedChange) > 0.011 ||
    Math.abs(quote.change_percent - expectedPercent) > 0.051
  ) {
    throw new Error("共享报价涨跌数据与价格不一致")
  }
  optionalFinite(quote.turnover_rate, "turnover_rate")
  optionalFinite(quote.amplitude, "amplitude")
  optionalFinite(quote.pe_dynamic, "pe_dynamic")
  optionalFinite(quote.pb, "pb")
  optionalFinite(quote.total_market_cap, "total_market_cap")
  optionalFinite(quote.float_market_cap, "float_market_cap")
  if (
    quote.mode === "live" &&
    (quote.quality !== "valid" || quote.session_status !== "open")
  ) {
    throw new Error("共享实时报价不在有效交易会话")
  }
  const expectedFormal =
    quote.quality === "valid" &&
    !quote.is_synthetic &&
    quote.trade_status_verified
  if (response.formal_use_eligible !== expectedFormal) {
    throw new Error("共享报价正式用途标记与证据不一致")
  }
  const requestId = quote.request_id?.trim()
  if (!requestId) {
    throw new Error("共享报价缺少 request_id")
  }
  const warnings = quote.warnings ?? []

  return {
    code: item.legacy_code,
    name: quote.display_name,
    price: quote.last_price,
    change: quote.change,
    changePct: quote.change_percent,
    open: quote.open,
    high: quote.high,
    low: quote.low,
    prevClose: quote.previous_close,
    volume: quote.volume_lots,
    amount: quote.amount,
    turnover: quote.turnover_rate ?? null,
    amplitude: quote.amplitude ?? null,
    pe: quote.pe_dynamic ?? null,
    pb: quote.pb ?? null,
    marketCap: quote.total_market_cap ?? null,
    high52w: null,
    low52w: null,
    time: quote.data_time,
    dataMode: quote.mode,
    quality: quote.quality,
    sessionStatus: quote.session_status,
    formalUseEligible: response.formal_use_eligible,
    sourceSnapshot: quote,
    provenance: {
      provider: quote.provider,
      temporalMode: quote.temporal_mode,
      requestedAt: quote.requested_at,
      asOf: quote.as_of,
      fetchedAt: quote.fetched_at,
      dataTime: quote.data_time,
      providerUpdatedAt: quote.provider_updated_at,
      dataTimeBasis: quote.data_time_basis,
      timezone: quote.timezone,
      freshnessSeconds: quote.freshness_seconds,
      isSynthetic: quote.is_synthetic,
      sourceChain: quote.source_chain,
      warnings,
      tradeStatusCode: quote.trade_status_code,
      tradeStatusVerified: quote.trade_status_verified,
      requestId,
    },
  }
}



export function buildSharedResearchRequest(
  item: SharedInstrumentSearchItem,
  provisionalQuote?: QuoteSnapshotResponse["quote"] | null,
  now = provisionalQuote ? new Date(provisionalQuote.as_of) : new Date()
): SharedResearchRequest {
  const baseQuery = buildSharedDataQuery(item.instrument, "2Y", "日线", now)
  const query = provisionalQuote
    ? {
        ...baseQuery,
        as_of: provisionalQuote.as_of,
        end: provisionalQuote.as_of,
      }
    : baseQuery

  return {
    query,
    benchmark_query: null,
    display_name: item.display_name,
    has_position: false,
    provisional_quote: provisionalQuote ?? null,
  }
}



export function buildSharedTechnicalStructureRequest(
  item: SharedInstrumentSearchItem,
  period: Period = "2Y",
  timeframe: Timeframe = "日线",
  now = new Date()
): SharedTechnicalStructureRequest {
  return {
    query: buildSharedDataQuery(item.instrument, period, timeframe, now),
    benchmark_query: null,
    display_name: item.display_name,
    has_position: false,
  }
}



type ChartTime = LinePoint["time"]


type ChartTimeMapper = (timestamp: string) => ChartTime



const INTRADAY_TIMEFRAMES = new Set<Timeframe>([
  "60分钟",
  "30分钟",
  "15分钟",
  "5分钟",
])


const WALL_CLOCK_FORMATTERS = new Map<string, Intl.DateTimeFormat>()



function wallClockUnix(timestamp: string, timezone: string): number {
  const instant = new Date(timestamp)
  if (!Number.isFinite(instant.getTime())) {
    throw new Error(`共享行情返回了无效时间：${timestamp}`)
  }
  let formatter = WALL_CLOCK_FORMATTERS.get(timezone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    })
    WALL_CLOCK_FORMATTERS.set(timezone, formatter)
  }
  const parts = Object.fromEntries(
    formatter
      .formatToParts(instant)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)])
  )
  const required = ["year", "month", "day", "hour", "minute", "second"]
  if (required.some((key) => !Number.isFinite(parts[key]))) {
    throw new Error(`共享行情无法按 ${timezone} 解释时间：${timestamp}`)
  }
  return Date.UTC(
    parts.year,
    parts.month - 1,
    parts.day,
    parts.hour,
    parts.minute,
    parts.second
  ) / 1000
}



function chartTime(
  timestamp: string,
  timeframe: Timeframe,
  timezone: string
): ChartTime {
  if (INTRADAY_TIMEFRAMES.has(timeframe)) {
    return wallClockUnix(timestamp, timezone)
  }
  const instant = new Date(timestamp)
  if (!Number.isFinite(instant.getTime())) {
    throw new Error(`共享行情返回了无效时间：${timestamp}`)
  }
  return timestamp.slice(0, 10)
}



function line(
  series: SharedIndicatorSeries,
  mapTime: ChartTimeMapper
): LinePoint[] {
  return series.points.map((point) => ({
    time: mapTime(point.timestamp),
    value: point.value,
  }))
}



function values(
  series: SharedIndicatorSeries,
  mapTime: ChartTimeMapper
): Map<ChartTime, number> {
  return new Map(
    series.points.map((point) => [mapTime(point.timestamp), point.value])
  )
}



function bollinger(
  view: SharedMarketBarsView,
  mapTime: ChartTimeMapper
): BollPoint[] {
  const upper = values(view.indicators.bollinger.upper, mapTime)
  const lower = values(view.indicators.bollinger.lower, mapTime)
  return view.indicators.bollinger.mid.points.flatMap((point) => {
    const time = mapTime(point.timestamp)
    const upperValue = upper.get(time)
    const lowerValue = lower.get(time)
    return upperValue === undefined || lowerValue === undefined
      ? []
      : [{ time, mid: point.value, upper: upperValue, lower: lowerValue }]
  })
}



function macd(
  view: SharedMarketBarsView,
  mapTime: ChartTimeMapper
): MacdPoint[] {
  const dea = values(view.indicators.macd.dea, mapTime)
  const hist = values(view.indicators.macd.hist, mapTime)
  return view.indicators.macd.dif.points.flatMap((point) => {
    const time = mapTime(point.timestamp)
    const deaValue = dea.get(time)
    const histValue = hist.get(time)
    return deaValue === undefined || histValue === undefined
      ? []
      : [{ time, dif: point.value, dea: deaValue, hist: histValue }]
  })
}



function kdj(
  view: SharedMarketBarsView,
  mapTime: ChartTimeMapper
): KdjPoint[] {
  const d = values(view.indicators.kdj.d, mapTime)
  const j = values(view.indicators.kdj.j, mapTime)
  return view.indicators.kdj.k.points.flatMap((point) => {
    const time = mapTime(point.timestamp)
    const dValue = d.get(time)
    const jValue = j.get(time)
    return dValue === undefined || jValue === undefined
      ? []
      : [{ time, k: point.value, d: dValue, j: jValue }]
  })
}



const FORMAL_MARKET_MODES = new Set(["live", "delayed", "cache"])



function sameInstrument(
  left: SharedInstrumentId,
  right: SharedInstrumentId
): boolean {
  return (
    left.symbol === right.symbol &&
    left.exchange === right.exchange &&
    left.market === right.market &&
    left.asset_type === right.asset_type &&
    left.currency === right.currency
  )
}



function finiteInstant(value: string, label: string): number {
  const instant = Date.parse(value)
  if (!Number.isFinite(instant)) {
    throw new Error(`共享行情 ${label} 不是有效时间`)
  }
  return instant
}



function validateMarketBarsView(
  view: SharedMarketBarsView,
  query: SharedDataQuery
): void {
  if (view.schema_version !== "market-bars-view/v1") {
    throw new Error(`不支持的共享行情契约：${view.schema_version}`)
  }
  if (!/^sha256:[0-9a-f]{64}$/i.test(view.data_fingerprint)) {
    throw new Error("共享行情缺少稳定的数据指纹")
  }
  if (!sameInstrument(view.data.instrument, query.instrument)) {
    throw new Error("共享行情证券身份与请求不一致")
  }
  if (
    view.data.timeframe !== query.timeframe ||
    view.data.purpose !== query.purpose ||
    view.data.adjustment !== query.adjustment
  ) {
    throw new Error("共享行情周期、用途或复权与请求不一致")
  }
  if (finiteInstant(view.data.as_of, "as_of") !== finiteInstant(query.as_of, "request as_of")) {
    throw new Error("共享行情 as_of 与请求不一致")
  }
  if (!FORMAL_MARKET_MODES.has(view.data.mode)) {
    throw new Error(`共享行情返回了非正式数据模式：${view.data.mode}`)
  }
  if (view.data.quality !== "valid") {
    throw new Error(`共享行情数据质量不可用：${view.data.quality}`)
  }
  if (view.data.is_synthetic) {
    throw new Error("共享行情拒绝展示模拟或合成数据")
  }
  const provider = view.data.provider.trim().toLowerCase()
  if (!provider || provider === "unknown" || provider === "none") {
    throw new Error("共享行情缺少明确数据提供方")
  }
  if (view.data.source_chain.length === 0) {
    throw new Error("共享行情缺少来源链")
  }
  if (view.data.bars.length === 0 || !view.data.last_bar_at) {
    throw new Error("共享行情未返回K线")
  }

  const start = finiteInstant(query.start, "request start")
  const upper = Math.min(
    finiteInstant(query.end, "request end"),
    finiteInstant(query.as_of, "request as_of")
  )
  let previous = Number.NEGATIVE_INFINITY
  for (const bar of view.data.bars) {
    const timestamp = finiteInstant(bar.timestamp, "bar timestamp")
    if (timestamp <= previous || timestamp < start || timestamp > upper) {
      throw new Error("共享行情K线时间越界、重复或未按升序排列")
    }
    previous = timestamp
    const values = [bar.open, bar.high, bar.low, bar.close, bar.volume]
    if (values.some((value) => !Number.isFinite(value)) || bar.volume < 0) {
      throw new Error("共享行情K线包含非法数值")
    }
    if (
      bar.open <= 0 ||
      bar.high <= 0 ||
      bar.low <= 0 ||
      bar.close <= 0 ||
      bar.high < Math.max(bar.open, bar.low, bar.close) ||
      bar.low > Math.min(bar.open, bar.high, bar.close)
    ) {
      throw new Error("共享行情K线不满足OHLC约束")
    }
  }
  const finalBarAt = finiteInstant(
    view.data.bars[view.data.bars.length - 1].timestamp,
    "final bar timestamp"
  )
  if (finiteInstant(view.data.last_bar_at, "last_bar_at") !== finalBarAt) {
    throw new Error("共享行情 last_bar_at 与末根K线不一致")
  }
}



function provenance(view: SharedMarketBarsView): DataProvenance {
  return {
    provider: view.data.provider,
    quality: view.data.quality,
    isSynthetic: view.data.is_synthetic,
    fingerprint: view.data_fingerprint,
    profile: {
      name: view.indicator_profile.name,
      version: view.indicator_profile.version,
      formulaSource: view.indicator_profile.formula_source,
    },
    sourceChain: view.data.source_chain,
    fetchedAt: view.data.fetched_at,
    lastBarAt: view.data.last_bar_at,
    freshnessSeconds: view.data.freshness_seconds,
    warnings: view.data.warnings,
  }
}



export function mapMarketBarsView(
  view: SharedMarketBarsView,
  item: SharedInstrumentSearchItem,
  period: Period,
  timeframe: Timeframe,
  query: SharedDataQuery
): ChartData {
  validateMarketBarsView(view, query)
  const mapTime: ChartTimeMapper = (timestamp) =>
    chartTime(timestamp, timeframe, view.data.timezone)
  const bars: Bar[] = view.data.bars.map((bar) => ({
    time: mapTime(bar.timestamp),
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
  }))
  return {
    code: item.legacy_code,
    name: item.display_name,
    timeframe,
    period,
    bars,
    indicators: {
      emaInner: {
        fast: line(view.indicators.ema.ema8, mapTime),
        slow: line(view.indicators.ema.ema21, mapTime),
      },
      emaOuter: {
        fast: line(view.indicators.ema.ema55, mapTime),
        slow: line(view.indicators.ema.ema144, mapTime),
      },
      boll: bollinger(view, mapTime),
      vwap: null,
      macd: macd(view, mapTime),
      kdj: kdj(view, mapTime),
      rsi: line(view.indicators.rsi, mapTime),
    },
    dataMode: normalizeDataMode(view.data.mode),
    provenance: provenance(view),
  }
}



const FORMAL_RESEARCH_MODES = new Set(["live", "delayed", "cache"] as const)



function formalResearchMode(
  mode: string
): ResearchDataReference["mode"] {
  if (!FORMAL_RESEARCH_MODES.has(mode as "live" | "delayed" | "cache")) {
    throw new Error(`共享研究返回了不可用于正式评分的数据模式：${mode}`)
  }
  return mode as ResearchDataReference["mode"]
}



function mapResearchDataReference(
  dataRef: SharedResearchDataReference
): ResearchDataReference {
  if (dataRef.quality !== "valid") {
    throw new Error(`共享研究返回了不可用于正式评分的数据质量：${dataRef.quality}`)
  }
  if (dataRef.is_synthetic) {
    throw new Error("共享研究拒绝展示模拟数据生成的正式评分")
  }
  return {
    requestId: dataRef.request_id,
    provider: dataRef.provider,
    mode: formalResearchMode(dataRef.mode),
    quality: dataRef.quality,
    asOf: dataRef.as_of,
    fetchedAt: dataRef.fetched_at,
    lastBarAt: dataRef.last_bar_at,
    timezone: dataRef.timezone,
    adjustment: dataRef.adjustment,
    freshnessSeconds: dataRef.freshness_seconds,
    isSynthetic: dataRef.is_synthetic,
    sourceChain: dataRef.source_chain,
    sourceWarnings: dataRef.source_warnings,
    barCount: dataRef.bar_count,
  }
}



const COMPONENT_LABELS: Record<string, string> = {
  trend: "趋势",
  momentum: "动量",
  volume: "量能",
  support: "支撑结构",
  risk_reward: "风险收益比",
  signal: "信号强度",
}



function mapComponentScores(
  components: SharedScoreComponent[]
): AnalysisComponentScore[] {
  return components.map((component) => ({
    key: component.name,
    label: COMPONENT_LABELS[component.name] ?? component.name,
    score: component.score,
    weight: component.weight,
    enabled: component.enabled,
    evidence: component.evidence,
    warnings: component.warnings,
  }))
}



function joinEvidence(parts: Array<string | null>): string {
  return parts.filter((part): part is string => Boolean(part)).join(" · ")
}



function mapEvidenceDimensions(
  summary: SharedTechnicalSummary
): ResearchEvidenceDimension[] {
  const dimensions: ResearchEvidenceDimension[] = []
  if (summary.trend_strength) {
    dimensions.push({
      key: "trend_strength",
      label: "趋势强度（子证据）",
      summary:
        joinEvidence([
          summary.trend_strength.level,
          summary.trend_strength.signal_summary,
        ]) || "共享核心未提供文字摘要",
      score: summary.trend_strength.score,
    })
  }
  if (summary.multi_timeframe) {
    dimensions.push({
      key: "multi_timeframe",
      label: "多周期一致性",
      summary:
        joinEvidence([
          summary.multi_timeframe.alignment,
          summary.multi_timeframe.dominant_trend,
          summary.multi_timeframe.summary,
        ]) || "共享核心未提供文字摘要",
      score: summary.multi_timeframe.alignment_score,
    })
  }
  if (summary.divergence) {
    const flags = [
      summary.divergence.bullish_active ? "看多背离" : null,
      summary.divergence.bearish_active ? "看空背离" : null,
    ]
    dimensions.push({
      key: "divergence",
      label: "背离证据",
      summary:
        joinEvidence([
          `活动背离 ${summary.divergence.active_count} 个`,
          ...flags,
          summary.divergence.strongest_description,
        ]) || "共享核心未提供背离摘要",
      score: null,
    })
  }
  if (summary.composite_signal) {
    const latestSignal = summary.composite_signal.signals.at(-1) ?? null
    const signalScoreSummary = latestSignal?.confidence === null || latestSignal?.confidence === undefined
      ? null
      : `最新信号证据分 ${latestSignal.confidence.toFixed(1)}/100（非概率）`
    dimensions.push({
      key: "composite_signal",
      label: "复合信号证据",
      summary:
        joinEvidence([
          summary.composite_signal.current_signal,
          latestSignal?.confidence_level ?? summary.composite_signal.strength,
          `近期买入 ${summary.composite_signal.recent_buy_count} / 卖出 ${summary.composite_signal.recent_sell_count}`,
          signalScoreSummary,
        ]) || "共享核心未提供复合信号摘要",
      score: latestSignal?.confidence ?? null,
    })
  }
  return dimensions
}



const ACTION_LABELS: Record<SharedAnalysisSnapshot["action"], string> = {
  long: "做多",
  hold: "持有",
  reduce: "减仓",
  exit: "退出",
  cash: "现金等待",
}



function mapCompositeSignal(
  signal: SharedSignalPoint,
  status: "confirmed" | "provisional"
): Signal {
  const label = signal.kind === "buy" ? "买入信号" : "卖出信号"
  const prefix = status === "provisional" ? "盘中预估" : ""
  const strength = signal.confidence_level
    ? `(${signal.confidence_level})`
    : signal.strength
      ? `(${signal.strength})`
      : ""
  const reasons = signal.confirmations.slice(0, 4).join(" + ")
  const components = signal.confidence_components
  const confidence = signal.confidence === null
    ? ""
    : components
      ? ` · 信号证据分 ${signal.confidence.toFixed(0)}/100（${components.independent_bucket_count} 个独立维度；${components.trend_evidence_status === "available" ? `趋势配合 ${components.directional_trend_score.toFixed(0)}` : "趋势样本不足"}；非概率）`
      : ` · 未校准信号值 ${signal.confidence.toFixed(0)}/100（非概率）`
  return {
    time: signal.time,
    kind: signal.kind,
    text: `${prefix}${label}${strength}${reasons ? `：${reasons}` : ""}${confidence}`,
    price: signal.price,
    strength: signal.strength,
    confidenceLevel: signal.confidence_level ?? null,
    confidence: signal.confidence,
    confirmations: [...signal.confirmations],
    status,
    confidenceComponents: components
      ? {
          version: components.version,
          semantics: components.semantics,
          independentBucketCount: components.independent_bucket_count,
          bucketScore: components.bucket_score,
          directionalTrendScore: components.directional_trend_score,
          trendEvidenceStatus: components.trend_evidence_status,
        }
      : null,
  }
}



function dateKeyInTimezone(value: string, timezone: string): string {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  })
  const parts = Object.fromEntries(
    formatter.formatToParts(new Date(value)).map((part) => [part.type, part.value])
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}



function mapRealtimeSignalPreview(
  response: SharedResearchAnalysisResponse,
  sourceQuote?: QuoteSnapshotResponse["quote"] | null
): RealtimeSignalPreview | null {
  const preview = response.realtime_signal_preview ?? null
  if (!preview) return null
  if (!sourceQuote) {
    throw new Error("共享研究返回了未请求的盘中预估信号")
  }
  if (
    preview.schema_version !== "realtime-signal-preview/v1" ||
    preview.status !== "provisional" ||
    preview.advisory_only !== true ||
    preview.formal_use_eligible !== false ||
    preview.score_semantics !== "signal_evidence_score_not_probability"
  ) {
    throw new Error("盘中预估信号的状态或用途边界无效")
  }
  if (
    preview.daily_request_id !== response.data_ref.request_id ||
    preview.daily_provider !== response.data_ref.provider ||
    preview.completed_through !== response.data_ref.last_bar_at
  ) {
    throw new Error("盘中预估信号未绑定当前完成日线")
  }
  if (
    preview.quote_request_id !== sourceQuote.request_id ||
    preview.quote_provider !== sourceQuote.provider ||
    preview.quote_mode !== sourceQuote.mode ||
    preview.quote_quality !== sourceQuote.quality
  ) {
    throw new Error("盘中预估信号未绑定当前实时报价")
  }
  const expectedBarTime = dateKeyInTimezone(
    sourceQuote.data_time,
    sourceQuote.timezone
  )
  if (preview.bar_time !== expectedBarTime) {
    throw new Error("盘中预估信号日期与实时报价不一致")
  }
  if (!preview.available && preview.signals.length > 0) {
    throw new Error("不可用的盘中预估包含了信号")
  }
  const signals = preview.signals.map((signal) => {
    if (signal.time.slice(0, 10) !== preview.bar_time) {
      throw new Error("盘中预估信号不属于临时日 K")
    }
    return mapCompositeSignal(signal, "provisional")
  })
  return {
    available: preview.available,
    status: "provisional",
    barTime: preview.bar_time,
    completedThrough: preview.completed_through,
    dailyRequestId: preview.daily_request_id,
    quoteRequestId: preview.quote_request_id,
    dailyProvider: preview.daily_provider,
    quoteProvider: preview.quote_provider,
    quoteMode: preview.quote_mode,
    quoteQuality: preview.quote_quality,
    scoreSemantics: preview.score_semantics,
    signals,
    warnings: [...preview.warnings],
    advisoryOnly: true,
    formalUseEligible: false,
  }
}



export function mapResearchAnalysisResponse(
  response: SharedResearchAnalysisResponse,
  item: SharedInstrumentSearchItem,
  sourceQuote?: QuoteSnapshotResponse["quote"] | null
): AnalysisResult {
  if (response.schema_version !== "research-view/v1") {
    throw new Error(`不支持的共享研究契约：${response.schema_version}`)
  }
  if (
    response.technical_summary.score_semantics !==
    "evidence_score_not_probability"
  ) {
    throw new Error("共享研究评分语义不明确，已拒绝展示")
  }
  const evidenceScore = response.snapshot.evidence_score
  if (!Number.isFinite(evidenceScore) || evidenceScore < 0 || evidenceScore > 100) {
    throw new Error("共享研究返回了无效的 evidence_score")
  }
  const rating = ACTION_LABELS[response.snapshot.action]
  if (!rating) {
    throw new Error(`共享研究返回了未知决策动作：${response.snapshot.action}`)
  }

  const dataRef = mapResearchDataReference(response.data_ref)
  const overlayLevels: SRLevel[] = response.snapshot.key_levels.flatMap((level) => {
    const kind = level.kind.toLowerCase()
    if (kind !== "support" && kind !== "resistance") return []
    return [
      {
        price: level.price,
        type: kind as "support" | "resistance",
        sources: level.provenance,
      },
    ]
  })
  const signals = (response.technical_summary.composite_signal?.signals ?? []).map(
    (signal) => mapCompositeSignal(signal, "confirmed")
  )
  const realtimeSignalPreview = mapRealtimeSignalPreview(response, sourceQuote)

  return {
    code: item.legacy_code,
    score: evidenceScore,
    evidenceScore,
    rating,
    summary: response.snapshot.explanations.join("；"),
    trend: [],
    supports: overlayLevels.filter((level) => level.type === "support"),
    resistances: overlayLevels.filter((level) => level.type === "resistance"),
    signals,
    buyPlan: null,
    engine: "shared",
    dataMode: dataRef.mode,
    dataTime: dataRef.lastBarAt ?? dataRef.asOf,
    scoreSemantics: "evidence_score_not_probability",
    schemaVersion: response.schema_version,
    snapshotId: response.snapshot.snapshot_id,
    action: response.snapshot.action,
    barStatus: response.snapshot.bar_status,
    asOf: response.snapshot.as_of,
    engineVersion: response.snapshot.engine_version,
    configVersion: response.snapshot.config_version,
    dataRef,
    componentScores: mapComponentScores(response.snapshot.component_scores),
    evidenceDimensions: mapEvidenceDimensions(response.technical_summary),
    keyLevels: response.snapshot.key_levels.map((level) => ({ ...level })),
    invalidations: response.snapshot.invalidation.map((item) => ({ ...item })),
    sectionStatus: {
      dataRef: response.section_status.data_ref,
      snapshot: response.section_status.snapshot,
      marketView: response.section_status.market_view,
      technicalSummary: response.section_status.technical_summary,
    },
    scoreWarnings: response.warnings,
    realtimeSignalPreview,
  }
}



export const TECHNICAL_STRUCTURE_SECTION_KEYS = [
  "support_resistance",
  "fibonacci",
  "gann",
  "elliott",
  "chan",
  "candlestick",
  "chart_patterns",
  "divergences",
  "volume_price",
  "multi_timeframe",
  "overlay",
] as const satisfies readonly TechnicalStructureSectionKey[]



const FORMAL_STRUCTURE_MODES = new Set(["live", "delayed", "cache"])


const SECTION_AVAILABILITY = new Set(["available", "partial", "unavailable"])


const SIGNAL_STATUS = new Set(["confirmed", "provisional"])


const FINGERPRINT_PATTERN = /^sha256:[0-9a-f]{64}$/


const FORBIDDEN_SOURCE_NAMES = new Set([
  "",
  "unknown",
  "fallback",
  "demo",
  "simulation",
  "synthetic",
])



type UnknownObject = Record<string, unknown>



function objectAt(value: unknown, path: string): UnknownObject {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${path} 必须是对象`)
  }
  return value as UnknownObject
}



function stringAt(value: unknown, path: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${path} 必须是非空字符串`)
  }
  return value
}



function finiteAt(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${path} 必须是有限数值`)
  }
  return value
}



function instantAt(value: unknown, path: string): number {
  const timestamp = stringAt(value, path)
  const instant = Date.parse(timestamp)
  if (!Number.isFinite(instant)) {
    throw new Error(`${path} 必须是有效时间`)
  }
  return instant
}



function stringArrayAt(value: unknown, path: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${path} 必须是字符串数组`)
  }
  return value as string[]
}



function assertInstrument(
  value: unknown,
  expected: SharedInstrumentId,
  path: string
): void {
  const instrument = objectAt(value, path)
  const fields = ["symbol", "exchange", "market", "asset_type", "currency"] as const
  for (const field of fields) {
    if (instrument[field] !== expected[field]) {
      throw new Error(`${path}.${field} 与精确搜索结果不一致`)
    }
  }
}



function assertOptionalFiniteFields(value: UnknownObject, path: string): void {
  const fields = [
    "price",
    "end_price",
    "high",
    "low",
    "value",
    "reference_value",
    "confidence",
    "neckline_price",
    "target_price",
    "stop_price",
  ] as const
  for (const field of fields) {
    const candidate = value[field]
    if (candidate !== null && candidate !== undefined) {
      finiteAt(candidate, `${path}.${field}`)
    }
  }
  if (
    value.confidence !== null &&
    value.confidence !== undefined &&
    ((value.confidence as number) < 0 || (value.confidence as number) > 100)
  ) {
    throw new Error(`${path}.confidence 必须位于 0—100`)
  }
  if (
    value.count !== null &&
    value.count !== undefined &&
    (!Number.isInteger(value.count) || (value.count as number) < 0)
  ) {
    throw new Error(`${path}.count 必须是非负整数`)
  }
}



function assertStructureItem(value: unknown, path: string): void {
  const item = objectAt(value, path)
  stringAt(item.kind, `${path}.kind`)
  const source = stringAt(item.source, `${path}.source`).trim().toLowerCase()
  const method = stringAt(item.method_version, `${path}.method_version`)
  if (FORBIDDEN_SOURCE_NAMES.has(source) || /(?:^|[-_/])(fallback|demo)(?:$|[-_/])/i.test(method)) {
    throw new Error(`${path} 包含禁止进入正式技术结构的来源或方法`)
  }
  if (typeof item.status !== "string" || !SIGNAL_STATUS.has(item.status)) {
    throw new Error(`${path}.status 无效`)
  }
  if (typeof item.confirmed !== "boolean" || typeof item.provisional !== "boolean") {
    throw new Error(`${path} 缺少确认/暂定标志`)
  }
  if (item.score_eligible !== false || item.alert_eligible !== false) {
    throw new Error(`${path} 不得进入评分或告警`)
  }
  const expectedConfirmed = item.status === "confirmed"
  if (item.confirmed !== expectedConfirmed || item.provisional === expectedConfirmed) {
    throw new Error(`${path} 的 status/confirmed/provisional 相互矛盾`)
  }
  assertOptionalFiniteFields(item, path)
  stringArrayAt(item.tags, `${path}.tags`)
  if (!Array.isArray(item.points)) {
    throw new Error(`${path}.points 必须是数组`)
  }
  item.points.forEach((point, index) => {
    const pointObject = objectAt(point, `${path}.points[${index}]`)
    finiteAt(pointObject.value, `${path}.points[${index}].value`)
    if (
      pointObject.time !== null &&
      pointObject.time !== undefined &&
      typeof pointObject.time !== "string"
    ) {
      throw new Error(`${path}.points[${index}].time 无效`)
    }
  })
}



function assertChanState(
  value: unknown,
  expectedStateKey: string,
  continuity: unknown
): void {
  const state = objectAt(value, "technical_structure.current_state")
  if (
    state.version !== "chan-state-1" ||
    state.methodVersion !== "chan-local-3" ||
    state.reliabilityVersion !== "chan-reliability-1"
  ) {
    throw new Error("技术结构 current_state 版本无效")
  }
  if (state.stateKey !== expectedStateKey || state.persistenceEligible !== true) {
    throw new Error("技术结构 current_state 与精确证券身份/周期不一致")
  }
  if (
    typeof state.contentHash !== "string" ||
    !/^chan-state-[0-9a-f]{20}$/.test(state.contentHash)
  ) {
    throw new Error("技术结构 current_state 缺少有效的 Chan 校验值")
  }
  if (typeof state.prefixStable !== "boolean") {
    throw new Error("技术结构 current_state 缺少 prefixStable")
  }
  const status = state.continuityStatus
  const expectedContinuity =
    status === "continued"
      ? "continued"
      : status === "initialized"
        ? "initialized"
        : status === "invalid_prior_rejected" ||
            status === "conflict_prior_rejected"
          ? "rejected"
          : null
  if (expectedContinuity === null || continuity !== expectedContinuity) {
    throw new Error("技术结构 continuity 与 current_state 不一致")
  }
  const streams = objectAt(
    state.confirmedStreams,
    "technical_structure.current_state.confirmedStreams"
  )
  for (const key of ["stableBi", "segment", "sameLevel"] as const) {
    if (!Array.isArray(streams[key])) {
      throw new Error(`技术结构 current_state.${key} 必须是数组`)
    }
    streams[key].forEach((candidate, index) => {
      const structure = objectAt(
        candidate,
        `technical_structure.current_state.${key}[${index}]`
      )
      if (
        structure.validated !== true ||
        structure.confirmed !== true ||
        structure.status !== "confirmed" ||
        typeof structure.structureId !== "string"
      ) {
        throw new Error(`技术结构 current_state.${key}[${index}] 不是已确认结构`)
      }
    })
  }
}



/**
 * Admit a generated OpenAPI transport into the formal React path.  fetch()
 * only parses JSON, so this boundary deliberately rechecks the data discipline
 * that matters to the UI instead of trusting a TypeScript cast.
 */
export function validateTechnicalStructureResponse(
  value: unknown,
  item: SharedInstrumentSearchItem,
  request: SharedResearchRequest
): TechnicalStructureResponse {
  const root = objectAt(value, "technical_structure")
  if (root.schema_version !== "technical-structure-view/v1") {
    throw new Error(`不支持的技术结构契约：${String(root.schema_version)}`)
  }
  if (typeof root.data_fingerprint !== "string" || !FINGERPRINT_PATTERN.test(root.data_fingerprint)) {
    throw new Error("技术结构缺少有效的数据指纹")
  }
  const engine = stringAt(root.engine, "technical_structure.engine")
  if (/(?:^|[-_/])(fallback|demo|simulation)(?:$|[-_/])/i.test(engine)) {
    throw new Error("技术结构引擎处于降级或模拟模式")
  }
  stringArrayAt(root.warnings, "technical_structure.warnings")

  const dataRef = objectAt(root.data_ref, "technical_structure.data_ref")
  assertInstrument(dataRef.instrument, item.instrument, "technical_structure.data_ref.instrument")
  if (dataRef.timeframe !== request.query.timeframe) {
    throw new Error("技术结构周期与请求不一致")
  }
  assertChanState(
    root.current_state,
    buildChanStateKey(item.instrument, request.query.timeframe),
    root.continuity
  )
  if (dataRef.purpose !== request.query.purpose || dataRef.adjustment !== request.query.adjustment) {
    throw new Error("技术结构的用途或复权方式与请求不一致")
  }
  const dataAsOf = instantAt(dataRef.as_of, "technical_structure.data_ref.as_of")
  if (dataAsOf !== instantAt(request.query.as_of, "technical_structure.request.as_of")) {
    throw new Error("技术结构 as_of 与请求不一致")
  }
  if (typeof dataRef.mode !== "string" || !FORMAL_STRUCTURE_MODES.has(dataRef.mode)) {
    throw new Error(`技术结构数据模式不可用于正式分析：${String(dataRef.mode)}`)
  }
  if (dataRef.quality !== "valid") {
    throw new Error(`技术结构数据质量非 valid：${String(dataRef.quality)}`)
  }
  if (dataRef.is_synthetic !== false) {
    throw new Error("模拟数据不得进入正式技术结构")
  }
  const provider = stringAt(dataRef.provider, "technical_structure.data_ref.provider")
    .trim()
    .toLowerCase()
  if (FORBIDDEN_SOURCE_NAMES.has(provider)) {
    throw new Error("技术结构数据提供方不明或被禁用")
  }
  const sourceChain = stringArrayAt(
    dataRef.source_chain,
    "technical_structure.data_ref.source_chain"
  )
  if (
    sourceChain.length === 0 ||
    sourceChain.some((source) => FORBIDDEN_SOURCE_NAMES.has(source.trim().toLowerCase()))
  ) {
    throw new Error("技术结构数据来源链缺失或包含禁用来源")
  }
  const freshness = finiteAt(
    dataRef.freshness_seconds,
    "technical_structure.data_ref.freshness_seconds"
  )
  if (freshness < 0) throw new Error("技术结构数据新鲜度不得为负数")
  if (!Number.isInteger(dataRef.bar_count) || (dataRef.bar_count as number) < 1) {
    throw new Error("技术结构至少需要一根 K 线")
  }
  stringAt(dataRef.request_id, "technical_structure.data_ref.request_id")
  instantAt(dataRef.fetched_at, "technical_structure.data_ref.fetched_at")
  if (dataRef.last_bar_at !== null && dataRef.last_bar_at !== undefined) {
    instantAt(dataRef.last_bar_at, "technical_structure.data_ref.last_bar_at")
  }
  stringAt(dataRef.timezone, "technical_structure.data_ref.timezone")
  const lineageRequestIds = stringArrayAt(
    dataRef.lineage_request_ids,
    "technical_structure.data_ref.lineage_request_ids"
  )
  if (!lineageRequestIds.includes(dataRef.request_id as string)) {
    throw new Error("技术结构数据请求未绑定到快照血缘")
  }

  const snapshot = objectAt(root.snapshot, "technical_structure.snapshot")
  assertInstrument(snapshot.instrument, item.instrument, "technical_structure.snapshot.instrument")
  if (
    snapshot.timeframe !== dataRef.timeframe ||
    instantAt(snapshot.as_of, "technical_structure.snapshot.as_of") !== dataAsOf
  ) {
    throw new Error("技术结构快照与数据引用不一致")
  }
  const evidenceScore = finiteAt(
    snapshot.evidence_score,
    "technical_structure.snapshot.evidence_score"
  )
  if (evidenceScore < 0 || evidenceScore > 100) {
    throw new Error("技术结构证据分超出 0—100")
  }

  const sections = objectAt(root.sections, "technical_structure.sections")
  const sectionStatus = objectAt(
    root.section_status,
    "technical_structure.section_status"
  )
  for (const key of TECHNICAL_STRUCTURE_SECTION_KEYS) {
    const section = objectAt(sections[key], `technical_structure.sections.${key}`)
    if (section.name !== key) {
      throw new Error(`技术结构分区 ${key} 名称不一致`)
    }
    if (typeof section.status !== "string" || !SECTION_AVAILABILITY.has(section.status)) {
      throw new Error(`技术结构分区 ${key} 状态无效`)
    }
    if (sectionStatus[key] !== section.status) {
      throw new Error(`技术结构分区 ${key} 状态摘要不一致`)
    }
    stringArrayAt(section.warnings, `technical_structure.sections.${key}.warnings`)
    if (!Array.isArray(section.items)) {
      throw new Error(`技术结构分区 ${key}.items 必须是数组`)
    }
    section.items.forEach((structureItem, index) =>
      assertStructureItem(
        structureItem,
        `technical_structure.sections.${key}.items[${index}]`
      )
    )
  }

  return value as TechnicalStructureResponse
}



export function technicalStructureLevels(
  response: TechnicalStructureResponse
): SRLevel[] {
  return response.sections.support_resistance.items.flatMap((item) => {
    if (
      item.price === null ||
      item.price === undefined ||
      (item.role !== "support" && item.role !== "resistance")
    ) {
      return []
    }
    return [
      {
        price: item.price,
        type: item.role,
        sources: [item.source, item.method_version].filter(Boolean).join(" · "),
      },
    ]
  })
}



export function buildSharedFundamentalRequest(
  item: SharedInstrumentSearchItem,
  now = new Date()
): FundamentalSnapshotRequest {
  return {
    query: {
      instrument: item.instrument,
      purpose: "research",
      as_of: now.toISOString(),
      max_market_age_seconds: 7 * 24 * 60 * 60,
      max_financial_age_seconds: 200 * 24 * 60 * 60,
      required_filter_fields: ["ROE", "debt_ratio"],
      minimum_verified_financial_fields: 2,
    },
  }
}



const FUNDAMENTAL_BASES = new Set([
  "market_snapshot",
  "financial_statement",
  "derived_financial_statement",
  "provider_metadata",
])


const FUNDAMENTAL_FIELD_STATUSES = new Set(["verified", "unverified"])



function sameStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}



function assertFundamentalRaw(
  value: unknown,
  path: string,
  depth = 0
): void {
  if (depth > 16) throw new Error(`${path} 嵌套过深`)
  const raw = objectAt(value, path)
  const kind = raw.kind
  if (![
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "object",
  ].includes(String(kind))) {
    throw new Error(`${path}.kind 无效`)
  }
  const populated = {
    string: typeof raw.string_value === "string",
    integer: typeof raw.integer_value === "number" && Number.isInteger(raw.integer_value),
    number: typeof raw.number_value === "number" && Number.isFinite(raw.number_value),
    boolean: typeof raw.boolean_value === "boolean",
    array: Array.isArray(raw.items) && raw.items.length > 0,
    object: Array.isArray(raw.entries) && raw.entries.length > 0,
  }
  if (!populated[kind as keyof typeof populated] || Object.values(populated).filter(Boolean).length !== 1) {
    throw new Error(`${path} 必须且只能包含与 kind 匹配的原始证据`)
  }
  if (kind === "array") {
    ;(raw.items as unknown[]).forEach((item, index) =>
      assertFundamentalRaw(item, `${path}.items[${index}]`, depth + 1)
    )
  }
  if (kind === "object") {
    const keys = new Set<string>()
    ;(raw.entries as unknown[]).forEach((entry, index) => {
      const record = objectAt(entry, `${path}.entries[${index}]`)
      const key = stringAt(record.key, `${path}.entries[${index}].key`)
      if (keys.has(key)) throw new Error(`${path} 包含重复原始证据键 ${key}`)
      keys.add(key)
      assertFundamentalRaw(record.value, `${path}.entries[${index}].value`, depth + 1)
    })
  }
}



function assertFundamentalField(
  value: unknown,
  path: string,
  asOf: number,
  evidenceTimes: number[]
): { name: string; basis: string; status: string } {
  const field = objectAt(value, path)
  const name = stringAt(field.name, `${path}.name`)
  stringAt(field.source_field, `${path}.source_field`)
  assertFundamentalRaw(field.raw_value, `${path}.raw_value`)
  if (
    typeof field.normalized_value !== "string" &&
    typeof field.normalized_value !== "number" &&
    typeof field.normalized_value !== "boolean"
  ) {
    throw new Error(`${path}.normalized_value 类型无效`)
  }
  if (typeof field.normalized_value === "number" && !Number.isFinite(field.normalized_value)) {
    throw new Error(`${path}.normalized_value 必须是有限数值`)
  }
  if (typeof field.normalized_value === "string" && field.normalized_value.trim() === "") {
    throw new Error(`${path}.normalized_value 不得是空字符串`)
  }
  if (field.unit !== null && field.unit !== undefined) {
    stringAt(field.unit, `${path}.unit`)
  }
  const basis = stringAt(field.basis, `${path}.basis`)
  const status = stringAt(field.status, `${path}.status`)
  if (!FUNDAMENTAL_BASES.has(basis)) throw new Error(`${path}.basis 无效`)
  if (!FUNDAMENTAL_FIELD_STATUSES.has(status)) throw new Error(`${path}.status 无效`)

  const timestamps: Record<"period_end" | "published_at" | "market_time", number | null> = {
    period_end: null,
    published_at: null,
    market_time: null,
  }
  for (const key of Object.keys(timestamps) as (keyof typeof timestamps)[]) {
    const candidate = field[key]
    if (candidate !== null && candidate !== undefined) {
      const instant = instantAt(candidate, `${path}.${key}`)
      if (instant > asOf) throw new Error(`${path}.${key} 晚于 as_of`)
      timestamps[key] = instant
    }
  }
  if (
    timestamps.period_end !== null &&
    timestamps.published_at !== null &&
    timestamps.published_at < timestamps.period_end
  ) {
    throw new Error(`${path}.published_at 早于 period_end`)
  }
  if (
    status === "verified" &&
    basis === "market_snapshot" &&
    timestamps.market_time === null
  ) {
    throw new Error(`${path} 的已核验市场快照缺少 market_time`)
  }
  if (
    status === "verified" &&
    (basis === "financial_statement" || basis === "derived_financial_statement") &&
    (timestamps.period_end === null || timestamps.published_at === null)
  ) {
    throw new Error(`${path} 的已核验财报字段缺少报告期或发布时间`)
  }
  if (timestamps.published_at !== null) evidenceTimes.push(timestamps.published_at)
  if (timestamps.market_time !== null) evidenceTimes.push(timestamps.market_time)
  return { name, basis, status }
}



function assertIanaTimezone(value: unknown, path: string): string {
  const timezone = stringAt(value, path)
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: timezone }).format(0)
  } catch {
    throw new Error(`${path} 必须是有效 IANA 时区`)
  }
  return timezone
}



export function validateFundamentalSnapshotResponse(
  value: unknown,
  item: SharedInstrumentSearchItem,
  request: FundamentalSnapshotRequest
): FundamentalSnapshotResponse {
  const root = objectAt(value, "fundamental_snapshot")
  assertInstrument(root.instrument, item.instrument, "fundamental_snapshot.instrument")
  if (root.purpose !== request.query.purpose || root.use_case !== "risk_and_quality_filter") {
    throw new Error("基本面快照用途与请求不一致")
  }
  const requestAsOf = instantAt(request.query.as_of, "fundamental_snapshot.request.as_of")
  const snapshotAsOf = instantAt(root.as_of, "fundamental_snapshot.as_of")
  if (snapshotAsOf !== requestAsOf) throw new Error("基本面快照 as_of 与请求不一致")
  stringAt(root.snapshot_id, "fundamental_snapshot.snapshot_id")
  stringAt(root.engine_version, "fundamental_snapshot.engine_version")
  if (typeof root.formal_use_eligible !== "boolean") {
    throw new Error("fundamental_snapshot.formal_use_eligible 必须是布尔值")
  }
  const formalUseEligible = root.formal_use_eligible

  const data = objectAt(root.data, "fundamental_snapshot.data")
  assertInstrument(data.instrument, item.instrument, "fundamental_snapshot.data.instrument")
  if (data.purpose !== request.query.purpose) throw new Error("基本面数据用途不一致")
  const dataAsOf = instantAt(data.as_of, "fundamental_snapshot.data.as_of")
  if (dataAsOf !== snapshotAsOf) throw new Error("基本面数据 as_of 与快照不一致")
  const provider = stringAt(data.provider, "fundamental_snapshot.data.provider")
    .trim()
    .toLowerCase()
  if (FORBIDDEN_SOURCE_NAMES.has(provider)) throw new Error("基本面数据提供方不明或被禁用")
  if (typeof data.mode !== "string" || !FORMAL_STRUCTURE_MODES.has(data.mode)) {
    throw new Error(`基本面数据模式不可用于正式研究：${String(data.mode)}`)
  }
  const displayOnlyPartial = !formalUseEligible && data.quality === "partial"
  if (data.quality !== "valid" && !displayOnlyPartial) {
    throw new Error(`基本面数据质量不可展示：${String(data.quality)}`)
  }
  if (data.is_synthetic !== false) throw new Error("模拟数据不得进入正式基本面快照")
  assertIanaTimezone(data.timezone, "fundamental_snapshot.data.timezone")
  const currency = stringAt(data.currency, "fundamental_snapshot.data.currency")
  if (!/^[A-Z]{3}$/.test(currency) || currency !== item.instrument.currency) {
    throw new Error("基本面币种无效或与证券标识不一致")
  }
  const sourceChain = stringArrayAt(data.source_chain, "fundamental_snapshot.data.source_chain")
  if (
    sourceChain.length === 0 ||
    sourceChain.some((source) => FORBIDDEN_SOURCE_NAMES.has(source.trim().toLowerCase()))
  ) {
    throw new Error("基本面来源链缺失或包含禁用来源")
  }
  stringArrayAt(data.warnings, "fundamental_snapshot.data.warnings")
  stringAt(data.request_id, "fundamental_snapshot.data.request_id")
  instantAt(data.fetched_at, "fundamental_snapshot.data.fetched_at")
  if (data.data_time_basis !== "latest_field_evidence_time") {
    throw new Error("基本面 data_time_basis 无效")
  }
  const dataTime = instantAt(data.data_time, "fundamental_snapshot.data.data_time")
  if (dataTime > dataAsOf) throw new Error("基本面 data_time 晚于 as_of")
  const freshness = finiteAt(data.freshness_seconds, "fundamental_snapshot.data.freshness_seconds")
  if (freshness < 0 || Math.abs(freshness - (dataAsOf - dataTime) / 1000) > 1) {
    throw new Error("基本面 freshness_seconds 与 as_of/data_time 不一致")
  }

  if (!Array.isArray(data.fields) || data.fields.length === 0) {
    throw new Error("基本面快照缺少字段证据")
  }
  const names = new Set<string>()
  const verifiedFinancial: string[] = []
  const evidenceTimes: number[] = []
  data.fields.forEach((field, index) => {
    const meta = assertFundamentalField(
      field,
      `fundamental_snapshot.data.fields[${index}]`,
      dataAsOf,
      evidenceTimes
    )
    if (names.has(meta.name)) throw new Error(`基本面字段重复：${meta.name}`)
    names.add(meta.name)
    if (
      meta.status === "verified" &&
      (meta.basis === "financial_statement" || meta.basis === "derived_financial_statement")
    ) {
      verifiedFinancial.push(meta.name)
    }
  })
  if (evidenceTimes.length === 0 || Math.max(...evidenceTimes) !== dataTime) {
    throw new Error("基本面 data_time 不是最新字段证据时间")
  }

  if (displayOnlyPartial) {
    return value as FundamentalSnapshotResponse
  }

  const coverage = objectAt(root.coverage, "fundamental_snapshot.coverage")
  const required = stringArrayAt(coverage.required_fields, "fundamental_snapshot.coverage.required_fields")
  const verified = stringArrayAt(
    coverage.verified_financial_fields,
    "fundamental_snapshot.coverage.verified_financial_fields"
  )
  const missing = stringArrayAt(
    coverage.missing_required_fields,
    "fundamental_snapshot.coverage.missing_required_fields"
  )
  if (required.length === 0 || new Set(required).size !== required.length) {
    throw new Error("基本面 coverage.required_fields 必须非空且唯一")
  }
  if (!sameStrings(required, request.query.required_filter_fields)) {
    throw new Error("基本面覆盖字段与请求不一致")
  }
  const expectedVerified = verifiedFinancial.sort()
  if (!sameStrings(verified, expectedVerified)) throw new Error("基本面已核验财报字段清单不一致")
  const expectedMissing = required.filter((name) => !verified.includes(name))
  if (!sameStrings(missing, expectedMissing)) throw new Error("基本面缺失覆盖字段清单不一致")
  if (
    !Number.isInteger(coverage.minimum_verified_financial_fields) ||
    (coverage.minimum_verified_financial_fields as number) < 1 ||
    coverage.minimum_verified_financial_fields !== request.query.minimum_verified_financial_fields
  ) {
    throw new Error("基本面最低覆盖门槛无效")
  }
  const expectedPassed =
    expectedMissing.length === 0 &&
    verified.length >= (coverage.minimum_verified_financial_fields as number)
  if (coverage.passed !== expectedPassed || coverage.passed !== true) {
    throw new Error("基本面 coverage 未通过或与字段证据不一致")
  }
  return value as FundamentalSnapshotResponse
}



export const FUNDAMENTAL_GROUP_ORDER: readonly FundamentalGroup[] = [
  "valuation",
  "profitability",
  "growth",
  "health",
  "scale",
  "metadata",
]



export const FUNDAMENTAL_GROUP_LABELS: Record<FundamentalGroup, string> = {
  valuation: "估值",
  profitability: "盈利能力",
  growth: "成长",
  health: "财务健康",
  scale: "规模",
  metadata: "基础信息",
}



const FUNDAMENTAL_LABELS: Record<string, string> = {
  pe: "市盈率 PE",
  pe_ttm: "市盈率 PE-TTM",
  pb: "市净率 PB",
  pb_mrq: "市净率 PB-MRQ",
  ps: "市销率 PS",
  ps_ttm: "市销率 PS-TTM",
  dividend_yield: "股息率",
  roe: "净资产收益率 ROE",
  net_margin: "净利率",
  gross_margin: "毛利率",
  op_margin: "营业利润率",
  rev_growth: "营收增长",
  debt_ratio: "资产负债率",
  current_ratio: "流动比率",
  quick_ratio: "速动比率",
  total_cash: "现金总额",
  total_debt: "有息负债",
  total_liabilities: "负债总额",
  total_assets: "资产总额",
  equity: "归属股东权益",
  revenue: "营业收入",
  net_income: "归母净利润",
  market_cap: "总市值",
  shares: "总股本",
  current_price: "当前价格",
  prev_close: "前收盘价",
  "52w_high": "52 周最高",
  "52w_low": "52 周最低",
  name: "公司名称",
  sector: "板块",
  industry: "行业",
  currency: "币种",
}



export function fundamentalFieldLabel(name: string): string {
  return FUNDAMENTAL_LABELS[name.toLowerCase()] ?? name
}



export function fundamentalGroupForField(name: string): FundamentalGroup {
  const key = name.toLowerCase()
  if (["pe", "pe_ttm", "pb", "pb_mrq", "ps", "ps_ttm", "dividend_yield"].includes(key)) {
    return "valuation"
  }
  if (["roe", "net_margin", "gross_margin", "op_margin", "net_income"].includes(key)) {
    return "profitability"
  }
  if (key.includes("growth")) return "growth"
  if (["debt_ratio", "current_ratio", "quick_ratio", "total_cash", "total_debt", "total_liabilities"].includes(key)) {
    return "health"
  }
  if (["market_cap", "revenue", "total_assets", "equity", "shares"].includes(key)) {
    return "scale"
  }
  return "metadata"
}



export function formatFundamentalNormalized(field: FundamentalSnapshotField): string {
  const value = field.normalized_value
  if (typeof value === "boolean") return value ? "是" : "否"
  if (typeof value === "number") {
    if (field.unit === "ratio") {
      return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value * 100)}%`
    }
    if (field.unit === "multiple") {
      return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)} 倍`
    }
    if (field.unit === "currency_per_share") {
      return `¥${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value)}`
    }
    if (field.unit === "currency" && Math.abs(value) >= 100_000_000) {
      return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value / 100_000_000)} 亿元`
    }
    if (field.unit === "shares" && Math.abs(value) >= 100_000_000) {
      return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value / 100_000_000)} 亿股`
    }
    return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 }).format(value)
  }
  return value
}



function intAt(value: unknown, path: string): number {
  const num = finiteAt(value, path)
  if (!Number.isInteger(num) || num < 0) {
    throw new Error(`${path} 必须是非负整数`)
  }
  return num
}



function assertOnlyKeys(
  value: UnknownObject,
  allowed: readonly string[],
  path: string
): void {
  const allowedKeys = new Set(allowed)
  const unexpected = Object.keys(value).filter((key) => !allowedKeys.has(key))
  if (unexpected.length > 0) {
    throw new Error(`${path} 包含未声明字段：${unexpected.join(", ")}`)
  }
}



function booleanAt(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${path} 必须是严格布尔值`)
  }
  return value
}



// ── Shared Watchlist V1：纯配置 CRUD + fail-closed 运行时校验 ──────
// Watchlist 不携带报价，不暗示监控或预警已启用，也不触发任何交易副作用。

const WATCHLIST_ROLES = new Set<WatchlistRole>([
  "core",
  "watchlist",
  "etf",
  "index",
])


const WATCHLIST_CN_CASH_EXCHANGES = new Set(["SSE", "SZSE", "BSE"])


const WATCHLIST_CN_INDEX_EXCHANGES = new Set([
  "SSE",
  "SZSE",
  "BSE",
  "OTHER",
])



function validateWatchlistAssignment(
  instrument: SharedInstrumentId,
  role: WatchlistRole,
  path = "watchlist"
): void {
  if (instrument.market !== "CN" || instrument.currency !== "CNY") {
    throw new Error(
      `${path} V1 只接受中国内地 CNY 证券；XBI/美股兼容仍待 Shared 市场契约扩展`
    )
  }
  if (role === "core" || role === "watchlist") {
    if (
      instrument.asset_type !== "stock" ||
      !WATCHLIST_CN_CASH_EXCHANGES.has(instrument.exchange)
    ) {
      throw new Error(`${path}.${role} 只接受 SSE/SZSE/BSE 的 A 股公司股票`)
    }
    return
  }
  if (role === "etf") {
    if (
      instrument.asset_type !== "etf" ||
      !WATCHLIST_CN_CASH_EXCHANGES.has(instrument.exchange)
    ) {
      throw new Error(`${path}.etf 只接受中国内地 ETF`)
    }
    return
  }
  if (
    (instrument.asset_type !== "index" &&
      instrument.asset_type !== "sector_index") ||
    !WATCHLIST_CN_INDEX_EXCHANGES.has(instrument.exchange)
  ) {
    throw new Error(`${path}.index 只接受中国内地指数或行业指数`)
  }
}



export function watchlistRoleForInstrument(
  instrument: SharedInstrumentId
): WatchlistRole | null {
  if (instrument.market !== "CN" || instrument.currency !== "CNY") {
    return null
  }
  if (
    instrument.asset_type === "stock" &&
    WATCHLIST_CN_CASH_EXCHANGES.has(instrument.exchange)
  ) {
    return "watchlist"
  }
  if (
    instrument.asset_type === "etf" &&
    WATCHLIST_CN_CASH_EXCHANGES.has(instrument.exchange)
  ) {
    return "etf"
  }
  if (
    (instrument.asset_type === "index" ||
      instrument.asset_type === "sector_index") &&
    WATCHLIST_CN_INDEX_EXCHANGES.has(instrument.exchange)
  ) {
    return "index"
  }
  return null
}



export function buildSharedWatchlistCreateRequest(
  candidate: SharedInstrumentSearchItem,
  role: WatchlistRole,
  note = ""
): WatchlistEntryCreateRequest {
  if (!WATCHLIST_ROLES.has(role)) {
    throw new Error("自选分组不在 Shared V1 白名单")
  }
  validateWatchlistAssignment(candidate.instrument, role)
  const displayName = candidate.display_name.trim()
  const normalizedNote = note.trim()
  if (!displayName || displayName.length > 128) {
    throw new Error("证券显示名称必须是 1–128 个字符")
  }
  if (normalizedNote.length > 256) {
    throw new Error("自选备注不得超过 256 个字符")
  }
  return {
    instrument: { ...candidate.instrument },
    role,
    display_name: displayName,
    note: normalizedNote,
  }
}



export function buildSharedWatchlistPatchRequest(
  expectedRevision: number,
  changes: Omit<WatchlistEntryPatchRequest, "expected_revision">
): WatchlistEntryPatchRequest {
  if (!Number.isInteger(expectedRevision) || expectedRevision < 1) {
    throw new Error("expected_revision 必须至少为 1")
  }
  const entries = Object.entries(changes).filter(([, value]) => value !== undefined)
  if (entries.length === 0) {
    throw new Error("自选更新至少需要一个字段")
  }
  const allowed = new Set(["role", "display_name", "note", "pinned"])
  if (entries.some(([key]) => !allowed.has(key))) {
    throw new Error("自选更新包含未声明字段")
  }
  if (entries.some(([, value]) => value === null)) {
    throw new Error("自选更新字段不得为 null")
  }
  const role = changes.role
  if (role !== undefined && (role === null || !WATCHLIST_ROLES.has(role))) {
    throw new Error("自选分组不在 Shared V1 白名单")
  }
  const displayName = changes.display_name
  if (displayName !== undefined) {
    if (
      displayName === null ||
      !displayName.trim() ||
      displayName.trim().length > 128
    ) {
      throw new Error("证券显示名称必须是 1–128 个字符")
    }
  }
  const note = changes.note
  if (note !== undefined && (note === null || note.trim().length > 256)) {
    throw new Error("自选备注不得超过 256 个字符")
  }
  if (changes.pinned !== undefined && typeof changes.pinned !== "boolean") {
    throw new Error("自选置顶状态必须是布尔值")
  }
  return { expected_revision: expectedRevision, ...changes }
}



export function buildSharedWatchlistReorderRequest(
  scope: WatchlistEntriesReorderRequest["scope"],
  role: WatchlistRole,
  orderedItems: WatchlistEntriesReorderRequest["ordered_items"]
): WatchlistEntriesReorderRequest {
  if (scope !== "securities" && scope !== "sectors") {
    throw new Error("自选排序范围必须是个股或板块")
  }
  if (!WATCHLIST_ROLES.has(role)) {
    throw new Error("自选分组不在 Shared V1 白名单")
  }
  if (orderedItems.length < 1 || orderedItems.length > 100) {
    throw new Error("自选排序必须包含 1–100 个条目")
  }
  const normalized = orderedItems.map((item) => {
    const entryId = item.entry_id.trim()
    if (!entryId || entryId.length > 64) {
      throw new Error("自选排序条目标识无效")
    }
    if (!Number.isInteger(item.expected_revision) || item.expected_revision < 1) {
      throw new Error("自选排序版本必须至少为 1")
    }
    return { entry_id: entryId, expected_revision: item.expected_revision }
  })
  if (new Set(normalized.map((item) => item.entry_id)).size !== normalized.length) {
    throw new Error("自选排序不得包含重复条目")
  }
  return { scope, role, ordered_items: normalized }
}



function validateWatchlistInstrumentAt(
  value: unknown,
  path: string
): SharedInstrumentId {
  const instrument = objectAt(value, path)
  assertOnlyKeys(
    instrument,
    ["symbol", "exchange", "market", "asset_type", "currency"],
    path
  )
  const symbol = stringAt(instrument.symbol, `${path}.symbol`)
  const exchange = String(instrument.exchange)
  const market = String(instrument.market)
  const assetType = String(instrument.asset_type)
  const currency = stringAt(instrument.currency, `${path}.currency`)
  if (
    !["SSE", "SZSE", "BSE", "HKEX", "NASDAQ", "NYSE", "AMEX", "OTHER"].includes(
      exchange
    ) ||
    !["CN", "HK", "US", "GLOBAL"].includes(market) ||
    ![
      "stock",
      "etf",
      "index",
      "sector_index",
      "fund",
      "futures",
      "fx",
      "unknown",
    ].includes(assetType)
  ) {
    throw new Error(`${path} 含有未知证券枚举值`)
  }
  return {
    symbol,
    exchange: exchange as SharedInstrumentId["exchange"],
    market: market as SharedInstrumentId["market"],
    asset_type: assetType as SharedInstrumentId["asset_type"],
    currency,
  }
}



function validateWatchlistEntryAt(
  value: unknown,
  path: string
): WatchlistEntryView {
  const entry = objectAt(value, path)
  assertOnlyKeys(
    entry,
    [
      "entry_id",
      "instrument",
      "role",
      "display_name",
      "note",
      "pinned",
      "sort_order",
      "created_at",
      "updated_at",
      "revision",
      "deleted_at",
    ],
    path
  )
  stringAt(entry.entry_id, `${path}.entry_id`)
  const instrument = validateWatchlistInstrumentAt(
    entry.instrument,
    `${path}.instrument`
  )
  const role = String(entry.role) as WatchlistRole
  if (!WATCHLIST_ROLES.has(role)) {
    throw new Error(`${path}.role 不在 Shared V1 白名单`)
  }
  validateWatchlistAssignment(instrument, role, path)
  const displayName = stringAt(entry.display_name, `${path}.display_name`)
  if (displayName.length > 128) {
    throw new Error(`${path}.display_name 不得超过 128 个字符`)
  }
  if (typeof entry.note !== "string" || entry.note.length > 256) {
    throw new Error(`${path}.note 必须是至多 256 个字符的字符串`)
  }
  const pinned = entry.pinned === undefined ? false : entry.pinned
  if (typeof pinned !== "boolean") {
    throw new Error(`${path}.pinned 必须是布尔值`)
  }
  const sortOrder =
    entry.sort_order === undefined
      ? 0
      : intAt(entry.sort_order, `${path}.sort_order`)
  if (sortOrder < 0 || sortOrder > 10_000) {
    throw new Error(`${path}.sort_order 必须位于 0–10000`)
  }
  const createdAt = instantAt(entry.created_at, `${path}.created_at`)
  const updatedAt = instantAt(entry.updated_at, `${path}.updated_at`)
  if (updatedAt < createdAt) {
    throw new Error(`${path}.updated_at 不得早于 created_at`)
  }
  if (entry.deleted_at !== null && entry.deleted_at !== undefined) {
    const deletedAt = instantAt(entry.deleted_at, `${path}.deleted_at`)
    if (deletedAt < createdAt || deletedAt !== updatedAt) {
      throw new Error(`${path}.deleted_at 必须等于删除更新时间`)
    }
  }
  if (intAt(entry.revision, `${path}.revision`) < 1) {
    throw new Error(`${path}.revision 必须至少为 1`)
  }
  return {
    ...entry,
    pinned,
    sort_order: sortOrder,
  } as unknown as WatchlistEntryView
}



function validateWatchlistBoundary(root: UnknownObject, path: string): void {
  const expected = {
    configuration_only: true,
    monitoring_active: false,
    alert_rule_created: false,
    market_data_requested: false,
    trade_created: false,
  } as const
  for (const [key, value] of Object.entries(expected)) {
    if (booleanAt(root[key], `${path}.${key}`) !== value) {
      throw new Error(`${path}.${key} 必须为 ${String(value)}`)
    }
  }
  const warnings = root.warnings ?? []
  stringArrayAt(warnings, `${path}.warnings`)
}



export function validateWatchlistListResponse(
  value: unknown
): WatchlistEntryListResponse {
  const root = objectAt(value, "watchlistList")
  assertOnlyKeys(
    root,
    [
      "schema_version",
      "total",
      "items",
      "limits",
      "warnings",
      "configuration_only",
      "monitoring_active",
      "alert_rule_created",
      "market_data_requested",
      "trade_created",
    ],
    "watchlistList"
  )
  if (root.schema_version !== "watchlist-list/v1") {
    throw new Error("watchlistList.schema_version 必须是 watchlist-list/v1")
  }
  validateWatchlistBoundary(root, "watchlistList")
  const total = intAt(root.total, "watchlistList.total")
  if (!Array.isArray(root.items) || root.items.length !== total) {
    throw new Error("watchlistList.total 必须等于 items.length")
  }
  const ids = new Set<string>()
  const instruments = new Set<string>()
  const normalizedItems: WatchlistEntryView[] = []
  let orderingSupported = true
  let coreStockCount = 0
  let activeStockCount = 0
  root.items.forEach((value, index) => {
    const transportEntry = objectAt(
      value,
      `watchlistList.items[${index}]`
    )
    if (
      typeof transportEntry.pinned !== "boolean" ||
      typeof transportEntry.sort_order !== "number"
    ) {
      orderingSupported = false
    }
    const entry = validateWatchlistEntryAt(
      value,
      `watchlistList.items[${index}]`
    )
    normalizedItems.push(entry)
    if (entry.deleted_at !== null && entry.deleted_at !== undefined) {
      throw new Error(`watchlistList.items[${index}] 不得包含已删除条目`)
    }
    const key = sharedInstrumentIdentityKey(entry.instrument)
    if (ids.has(entry.entry_id) || instruments.has(key)) {
      throw new Error("watchlistList 不得包含重复 entry_id 或证券身份")
    }
    ids.add(entry.entry_id)
    instruments.add(key)
    if (entry.role === "core") coreStockCount += 1
    if (entry.role === "core" || entry.role === "watchlist") {
      activeStockCount += 1
    }
  })
  const limits = objectAt(root.limits, "watchlistList.limits")
  assertOnlyKeys(
    limits,
    ["max_core_stocks", "max_active_stocks", "max_active_entries"],
    "watchlistList.limits"
  )
  if (
    limits.max_core_stocks !== 10 ||
    limits.max_active_stocks !== 30 ||
    limits.max_active_entries !== 100
  ) {
    throw new Error("watchlistList.limits 与 Shared V1 上限不一致")
  }
  if (coreStockCount > 10) {
    throw new Error("watchlistList.items 超过 10 个核心股票上限")
  }
  if (activeStockCount > 30) {
    throw new Error("watchlistList.items 超过 30 个活动股票上限")
  }
  if (total > 100) {
    throw new Error("watchlistList.items 超过 100 个活动条目上限")
  }
  return {
    ...root,
    items: normalizedItems,
    ordering_supported: orderingSupported,
  } as unknown as WatchlistEntryListResponse
}



export function validateWatchlistResponse(
  value: unknown,
  expectedAction: WatchlistEntryResponse["action"]
): WatchlistEntryResponse {
  const root = objectAt(value, "watchlistEntry")
  assertOnlyKeys(
    root,
    [
      "schema_version",
      "action",
      "entry",
      "warnings",
      "configuration_only",
      "monitoring_active",
      "alert_rule_created",
      "market_data_requested",
      "trade_created",
    ],
    "watchlistEntry"
  )
  if (root.schema_version !== "watchlist-entry/v1") {
    throw new Error("watchlistEntry.schema_version 必须是 watchlist-entry/v1")
  }
  if (root.action !== expectedAction) {
    throw new Error(`watchlistEntry.action 必须是 ${expectedAction}`)
  }
  validateWatchlistBoundary(root, "watchlistEntry")
  const entry = validateWatchlistEntryAt(root.entry, "watchlistEntry.entry")
  if (expectedAction === "deleted") {
    if (entry.deleted_at === null || entry.deleted_at === undefined) {
      throw new Error("deleted 响应必须携带 deleted_at")
    }
  } else if (entry.deleted_at !== null && entry.deleted_at !== undefined) {
    throw new Error(`${expectedAction} 响应不得携带 deleted_at`)
  }
  return root as unknown as WatchlistEntryResponse
}



export interface SharedProviderHealthInfo {
  provider: string
  healthy: boolean
  production_ready: boolean
  checked_at: string
  message: string | null
}



export interface SharedWorkerHealthInfo {
  status: "online" | "stale" | "offline"
  heartbeat_at: string | null
  detail: string | null
}



export interface SharedHealthResponse {
  status: "ok" | "degraded"
  service: "custom-model-api"
  api_version: "v1"
  analysis_ready: boolean
  providers: SharedProviderHealthInfo[]
  worker: SharedWorkerHealthInfo
  composition_error: string | null
}



export function validateSharedHealthResponse(raw: unknown): HealthResponse {
  if (!raw || typeof raw !== "object") {
    throw new Error("health response must be an object")
  }
  const data = raw as Partial<SharedHealthResponse>
  if (data.service !== "custom-model-api" || data.api_version !== "v1") {
    throw new Error("health response service or api_version mismatch")
  }
  if (data.status !== "ok" && data.status !== "degraded") {
    throw new Error("health response status must be ok or degraded")
  }
  if (typeof data.analysis_ready !== "boolean") {
    throw new Error("health response analysis_ready must be a boolean")
  }
  if (!data.worker || typeof data.worker !== "object") {
    throw new Error("health response worker must be an object")
  }
  if (!new Set(["online", "stale", "offline"]).has(data.worker.status)) {
    throw new Error("health response worker status is unsupported")
  }
  if (
    data.worker.heartbeat_at !== null &&
    typeof data.worker.heartbeat_at !== "string"
  ) {
    throw new Error("health response worker heartbeat_at must be a string or null")
  }
  if (data.worker.detail !== null && typeof data.worker.detail !== "string") {
    throw new Error("health response worker detail must be a string or null")
  }
  const providers = Array.isArray(data.providers) ? data.providers : []
  const sources = providers.map((p) => String(p.provider || "")).filter(Boolean)
  const isHealthy = data.status === "ok" && data.analysis_ready
  const mode: DataMode = isHealthy ? "live" : "fallback"
  const time =
    providers.find((p) => p.checked_at)?.checked_at ?? new Date().toISOString()
  return {
    ok: isHealthy,
    mode,
    sources,
    time,
    status: data.status,
    analysisReady: data.analysis_ready,
    worker: {
      status: data.worker.status,
      heartbeatAt: data.worker.heartbeat_at,
      detail: data.worker.detail,
    },
  }
}
