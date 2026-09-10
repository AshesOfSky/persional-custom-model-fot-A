import type { components as OpenApiComponents } from "./generated/schema"



/**
 * Custom-Model 行情分析终端 — API 契约类型
 * 前后端共同遵守，与 server/contract.md 一致。修改需同步。
 */

// ── 通用 ─────────────────────────────────────────────────────────
export type DataMode =
  | "live"
  | "delayed"
  | "cache"
  | "fallback"
  | "demo"
  | "simulation"



/** 兼容接口计算引擎；共享研究在 AnalysisResult 上单独声明。 */
export type EngineKind = "v5" | "fallback"



export type WorkerHealthStatus = "online" | "stale" | "offline"



export interface WorkerHealth {
  status: WorkerHealthStatus
  heartbeatAt: string | null
  detail: string | null
}



export interface HealthResponse {
  ok: boolean
  mode: DataMode
  sources: string[]
  time: string // ISO
  status?: "ok" | "degraded"
  analysisReady?: boolean
  worker: WorkerHealth
}



export interface StockRef {
  code: string
  name: string
  market: "A" | "HK" | "US" | "INDEX" | "ETF" | "FUTURES"
}



// ── 行情 ─────────────────────────────────────────────────────────
export interface Quote {
  code: string
  name: string
  price: number
  change: number
  changePct: number // 百分数，如 2.35 表示 +2.35%
  open: number
  high: number
  low: number
  prevClose: number
  volume: number // 手
  amount: number // 元
  turnover: number | null // 换手率 %
  amplitude: number | null // 振幅 %
  pe: number | null
  pb: number | null
  marketCap: number | null // 元
  high52w: number | null
  low52w: number | null
  time: string // provider/source quote time, ISO
  dataMode: "live" | "delayed"
  quality: "valid" | "stale"
  sessionStatus:
    | "open"
    | "midday_break"
    | "after_close"
    | "pre_open"
    | "non_trading_day_unverified"
  formalUseEligible: boolean
  provenance: QuoteProvenance
  sourceSnapshot: QuoteSnapshotResponse["quote"]
}



export interface QuoteProvenance {
  provider: string
  temporalMode: "exact" | "latest"
  requestedAt: string | null
  asOf: string
  fetchedAt: string
  dataTime: string
  providerUpdatedAt: string
  dataTimeBasis:
    | "provider_timestamp"
    | "cn_midday_close"
    | "cn_session_close"
    | "cn_prior_session_close"
  timezone: string
  freshnessSeconds: number
  isSynthetic: boolean
  sourceChain: string[]
  warnings: string[]
  tradeStatusCode: number
  tradeStatusVerified: boolean
  requestId: string
}



export type QuoteSnapshotRequest = OpenApiComponents["schemas"]["QuoteQuery"]


export type QuoteSnapshotResponse =
  OpenApiComponents["schemas"]["QuoteViewResponse"]


export type QuoteRefreshResponse =
  OpenApiComponents["schemas"]["QuoteRefreshResponse"]



export interface QuoteRefreshResult {
  quote: Quote
  providerUnchanged: boolean
  cooldownSeconds: number
  origin: "manual_refresh"
  cacheBypassed: true
  alertEventCreated: false
  externalNotificationSent: false
  tradeCreated: false
}



export type Period = "1M" | "3M" | "6M" | "1Y" | "2Y" | "5Y"


export type Timeframe =
  | "日线"
  | "周线"
  | "月线"
  | "60分钟"
  | "30分钟"
  | "15分钟"
  | "5分钟"



/** time: 日/周/月为 "YYYY-MM-DD"；分钟线为 unix 秒（按北京时间墙钟，前端直接展示） */
export interface Bar {
  time: string | number
  open: number
  high: number
  low: number
  close: number
  volume: number
}



export interface LinePoint {
  time: string | number
  value: number
}


export interface BollPoint {
  time: string | number
  mid: number
  upper: number
  lower: number
}


export interface MacdPoint {
  time: string | number
  dif: number
  dea: number
  hist: number
}


export interface KdjPoint {
  time: string | number
  k: number
  d: number
  j: number
}



export interface ChartIndicators {
  emaInner: { fast: LinePoint[]; slow: LinePoint[] } | null // EMA8 / EMA21
  emaOuter: { fast: LinePoint[]; slow: LinePoint[] } | null // EMA55 / EMA144
  boll: BollPoint[] | null
  vwap: LinePoint[] | null
  macd: MacdPoint[] | null
  kdj: KdjPoint[] | null
  rsi: LinePoint[] | null // RSI14
}



export interface DataProvenance {
  provider: string
  quality: string
  isSynthetic: boolean
  fingerprint: string
  profile: {
    name: string
    version: string
    formulaSource: string
  }
  sourceChain: string[]
  fetchedAt: string
  lastBarAt: string | null
  freshnessSeconds: number
  warnings: string[]
}



export interface ChartData {
  code: string
  name: string
  timeframe: Timeframe
  period: Period
  bars: Bar[]
  indicators: ChartIndicators
  dataMode: DataMode
  provenance?: DataProvenance
}



// ── 分析 ─────────────────────────────────────────────────────────
export interface SRLevel {
  price: number
  type: "support" | "resistance"
  strength?: number // 兼容旧接口；共享快照不虚构强度
  sources: string
  distancePct?: number // 兼容旧接口；共享快照不在前端推导
}



export interface Signal {
  time: string
  kind: "buy" | "sell" | "warn"
  text: string
  price: number | null
  strength?: string | null
  confidenceLevel?: "弱" | "中" | "中强" | "强" | null
  confidence?: number | null
  confirmations?: string[]
  status: ResearchSignalStatus
  confidenceComponents?: {
    version: "signal-evidence-v2"
    semantics: "signal_evidence_score_not_probability"
    independentBucketCount: number
    bucketScore: number
    directionalTrendScore: number
    trendEvidenceStatus: "available" | "insufficient_history"
  } | null
}



export interface TrendItem {
  label: string
  value: string
  direction: "up" | "down" | "flat"
}



export interface BuyPlan {
  entry: number | null
  stop: number | null
  target: number | null
  positionPct: number | null
  note: string
}



export type ResearchAction = "long" | "hold" | "reduce" | "exit" | "cash"


export type ResearchSignalStatus = "provisional" | "confirmed"



export interface RealtimeSignalPreview {
  available: boolean
  status: "provisional"
  barTime: string
  completedThrough: string | null
  dailyRequestId: string
  quoteRequestId: string
  dailyProvider: string
  quoteProvider: string
  quoteMode: "live" | "delayed"
  quoteQuality: "valid" | "stale"
  scoreSemantics: "signal_evidence_score_not_probability"
  signals: Signal[]
  warnings: string[]
  advisoryOnly: true
  formalUseEligible: false
}



export interface ResearchDataReference {
  requestId: string
  provider: string
  mode: "live" | "delayed" | "cache"
  quality: "valid" | "stale" | "partial" | "unavailable"
  asOf: string
  fetchedAt: string
  lastBarAt: string | null
  timezone: string
  adjustment: "forward" | "backward" | "none"
  freshnessSeconds: number
  isSynthetic: boolean
  sourceChain: string[]
  sourceWarnings: string[]
  barCount: number
}



export interface AnalysisComponentScore {
  key: string
  label: string
  score: number
  weight: number
  enabled: boolean
  evidence: string[]
  warnings: string[]
}



export interface ResearchEvidenceDimension {
  key: "trend_strength" | "multi_timeframe" | "divergence" | "composite_signal"
  label: string
  summary: string
  score: number | null
}



export interface ResearchKeyLevel {
  kind: string
  price: number
  provenance: string
  status: ResearchSignalStatus
}



export interface ResearchInvalidation {
  description: string
  price: number | null
  confirmation: string
}



export interface ResearchSectionStatus {
  dataRef: "available"
  snapshot: "available"
  marketView: "available"
  technicalSummary: "available" | "partial" | "unavailable"
}



export interface AnalysisResult {
  code: string
  /** @deprecated 过渡别名；与 evidenceScore 指向同一个 snapshot.evidence_score。 */
  score: number
  /** 共享快照的未校准证据分，不是上涨概率。 */
  evidenceScore: number
  rating: string // 共享快照 action 的展示标签
  summary: string
  trend: TrendItem[]
  supports: SRLevel[]
  resistances: SRLevel[]
  signals: Signal[]
  buyPlan: BuyPlan | null
  engine: EngineKind | "shared"
  dataMode: DataMode
  dataTime: string
  scoreSemantics: "evidence_score_not_probability"
  schemaVersion: "research-view/v1"
  snapshotId: string
  action: ResearchAction
  barStatus: ResearchSignalStatus
  asOf: string
  engineVersion: string
  configVersion: string
  dataRef: ResearchDataReference
  componentScores: AnalysisComponentScore[]
  evidenceDimensions: ResearchEvidenceDimension[]
  keyLevels: ResearchKeyLevel[]
  invalidations: ResearchInvalidation[]
  sectionStatus: ResearchSectionStatus
  scoreWarnings: string[]
  realtimeSignalPreview: RealtimeSignalPreview | null
}



// ── 基本面 ───────────────────────────────────────────────────────
export type FundamentalSnapshotRequest =
  OpenApiComponents["schemas"]["FundamentalSnapshotRequest"]


export type FundamentalSnapshotResponse =
  OpenApiComponents["schemas"]["FundamentalSnapshotResponse"]


export type FundamentalSnapshotField =
  OpenApiComponents["schemas"]["FundamentalFieldView"]


export type FundamentalGroup =
  | "valuation"
  | "profitability"
  | "growth"
  | "health"
  | "scale"
  | "metadata"



export type SharedInstrumentIdView =
  OpenApiComponents["schemas"]["InstrumentId"]


export type SharedInstrumentSearchItem =
  OpenApiComponents["schemas"]["InstrumentSearchItem"]


export type SharedInstrumentSearchResponse =
  OpenApiComponents["schemas"]["InstrumentSearchResponse"]



// ── Shared 自选配置（价格不属于配置；需要时独立请求 Quote） ───────
export type WatchlistEntryCreateRequest =
  OpenApiComponents["schemas"]["WatchlistEntryCreateRequest"]


export type WatchlistEntryPatchRequest =
  OpenApiComponents["schemas"]["WatchlistEntryPatchRequest"]


export type WatchlistEntriesReorderRequest =
  OpenApiComponents["schemas"]["WatchlistEntriesReorderRequest"]


export type WatchlistEntryResponse =
  OpenApiComponents["schemas"]["WatchlistEntryResponse"]


export type WatchlistEntryListResponse =
  OpenApiComponents["schemas"]["WatchlistEntryListResponse"] & {
    /** 客户端探测到常驻 API 已返回 pinned/sort_order 后才开放排序控件。 */
    ordering_supported: boolean
  }


export type WatchlistEntryView =
  OpenApiComponents["schemas"]["WatchlistEntryView"]


export type WatchlistRole = OpenApiComponents["schemas"]["WatchlistRole"]



// Shared API transport models are generated from Pydantic/OpenAPI. Keep the
// generated schema as the single transport source; shared.ts performs runtime
// admission checks before these values reach React.
export type TechnicalStructureResponse =
  OpenApiComponents["schemas"]["TechnicalStructureResponse"]


export type ChanState = OpenApiComponents["schemas"]["ChanStateView"]


export type TechnicalStructureItem =
  OpenApiComponents["schemas"]["TechnicalStructureItemView"]


export type TechnicalStructureSectionKey =
  keyof TechnicalStructureResponse["sections"]
