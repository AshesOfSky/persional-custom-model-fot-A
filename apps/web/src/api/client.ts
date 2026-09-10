import { useCallback, useEffect, useRef, useState } from "react"

import type { AnalysisResult, ChartData, FundamentalSnapshotResponse, HealthResponse, Period, Quote, QuoteRefreshResponse, QuoteRefreshResult, StockRef, TechnicalStructureResponse, Timeframe, WatchlistEntriesReorderRequest, WatchlistEntryListResponse, WatchlistEntryPatchRequest, WatchlistEntryResponse, WatchlistRole } from "./types"

import { priorChanStateFor, rememberChanState } from "@/state/chanState"

import { buildSharedDataQuery, buildSharedFundamentalRequest, buildSharedQuoteQuery, buildSharedResearchRequest, buildSharedTechnicalStructureRequest, buildSharedWatchlistCreateRequest, buildSharedWatchlistPatchRequest, buildSharedWatchlistReorderRequest, findExactInstrument, normalizeInstrumentSearchQuery, mapMarketBarsView, mapSharedQuoteResponse, mapResearchAnalysisResponse, mapSearchResponse, validateFundamentalSnapshotResponse, validateSharedHealthResponse, validateTechnicalStructureResponse, validateWatchlistListResponse, validateWatchlistResponse, type SharedInstrumentSearchResponse, type SharedInstrumentSearchItem, type SharedMarketBarsView, type SharedQuoteViewResponse, type SharedResearchAnalysisResponse } from "./shared"



export class ApiError extends Error {
  status: number
  retryAfterSeconds: number | null
  outcomeUncertain: boolean
  constructor(
    message: string,
    status: number,
    retryAfterSeconds: number | null = null,
    outcomeUncertain = false
  ) {
    super(message)
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
    this.outcomeUncertain = outcomeUncertain
  }
}



interface CoreRequestOptions {
  timeoutMs?: number
  outcomeUncertainOnTimeout?: boolean
  timeoutMessage?: string
}



const CHART_READ_OPTIONS: CoreRequestOptions = {
  timeoutMs: 30_000,
  timeoutMessage: "K线数据读取超时（30 秒），请稍后重试。",
}



const LOCAL_CONFIG_REQUEST_TIMEOUT_MS = 8_000


const LOCAL_CONFIG_READ_OPTIONS: CoreRequestOptions = {
  timeoutMs: LOCAL_CONFIG_REQUEST_TIMEOUT_MS,
}


const LOCAL_CONFIG_WRITE_OPTIONS: CoreRequestOptions = {
  timeoutMs: LOCAL_CONFIG_REQUEST_TIMEOUT_MS,
  outcomeUncertainOnTimeout: true,
}



function localConfigTimeoutError(outcomeUncertain: boolean): ApiError {
  const seconds = LOCAL_CONFIG_REQUEST_TIMEOUT_MS / 1_000
  if (outcomeUncertain) {
    return new ApiError(
      `操作结果待核：本地配置写入在 ${seconds} 秒内未返回；服务端可能已完成操作。已停止等待，将只读刷新服务端状态，请勿自动或立即重复提交。`,
      0,
      null,
      true
    )
  }
  return new ApiError(
    `本地配置读取超时（${seconds} 秒），请确认 8010 API 状态后重试。`,
    0
  )
}



async function coreRequest<T>(
  path: string,
  init?: RequestInit,
  options: CoreRequestOptions = {}
): Promise<T> {
  const timeoutController = options.timeoutMs ? new AbortController() : null
  let timedOut = false
  const timeoutId = timeoutController
    ? window.setTimeout(() => {
        timedOut = true
        timeoutController.abort()
      }, options.timeoutMs)
    : null
  let res: Response
  try {
    res = await fetch(`/core${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
      ...(timeoutController ? { signal: timeoutController.signal } : {}),
    })
  } catch {
    if (timedOut) {
      if (options.timeoutMessage) throw new ApiError(options.timeoutMessage, 0)
      throw localConfigTimeoutError(options.outcomeUncertainOnTimeout === true)
    }
    throw new ApiError("无法连接数据服务（后端未启动？）", 0)
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId)
  }
  if (!res.ok) {
    let detail = res.statusText
    const retryAfterHeader = res.headers.get("Retry-After")
    const parsedRetryAfter = retryAfterHeader
      ? Number.parseInt(retryAfterHeader, 10)
      : Number.NaN
    const retryAfterSeconds =
      Number.isInteger(parsedRetryAfter) && parsedRetryAfter > 0
        ? parsedRetryAfter
        : null
    try {
      const body = await res.json()
      if (body?.error?.message) detail = String(body.error.message)
      else if (body?.detail) detail = String(body.detail)
    } catch {
      /* ignore */
    }
    throw new ApiError(detail, res.status, retryAfterSeconds)
  }
  return (await res.json()) as T
}



async function sharedSearch(query: string): Promise<StockRef[]> {
  const response = await coreRequest<SharedInstrumentSearchResponse>(
    `/v1/instruments/search?q=${encodeURIComponent(query)}&limit=10`
  )
  return mapSearchResponse(response)
}



async function sharedWatchlistCandidateSearch(
  query: string
): Promise<SharedInstrumentSearchItem[]> {
  const normalized = normalizeInstrumentSearchQuery(query)
  if (!normalized) return []
  const response = await coreRequest<SharedInstrumentSearchResponse>(
    `/v1/instruments/search?q=${encodeURIComponent(normalized)}&limit=10`
  )
  return response.items
}



async function resolveSharedInstrument(
  code: string,
  options: CoreRequestOptions = {}
): Promise<SharedInstrumentSearchItem> {
  const normalized = normalizeInstrumentSearchQuery(code)
  const search = await coreRequest<SharedInstrumentSearchResponse>(
    `/v1/instruments/search?q=${encodeURIComponent(normalized)}&limit=20`, undefined, options
  )
  const instrument = findExactInstrument(search, code)
  if (!instrument) {
    throw new ApiError(`共享核心无法精确识别证券代码：${code}`, 404)
  }
  return instrument
}



async function sharedChart(
  code: string,
  period: Period,
  timeframe: Timeframe
): Promise<ChartData> {
  const instrument = await resolveSharedInstrument(code, CHART_READ_OPTIONS)
  const query = buildSharedDataQuery(
    instrument.instrument,
    period,
    timeframe
  )
  try {
    const view = await coreRequest<SharedMarketBarsView>("/v1/market/bars/query", {
      method: "POST",
      body: JSON.stringify(query),
    }, CHART_READ_OPTIONS)
    return mapMarketBarsView(view, instrument, period, timeframe, query)
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `共享行情暂不可用（503）：${error.message}。未回退旧版或模拟K线。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 422) {
      throw new ApiError(
        `共享行情请求被拒绝（422）：${error.message}。未回退旧版或模拟K线。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 0) {
      throw new ApiError(
        `共享行情服务无法连接：${error.message}。未回退旧版或模拟K线。`,
        error.status
      )
    }
    if (error instanceof ApiError) throw error
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `共享行情响应不可用：${detail}。未回退旧版或模拟K线。`,
      502
    )
  }
}



async function sharedQuote(code: string): Promise<Quote> {
  let instrument: SharedInstrumentSearchItem
  let payload: ReturnType<typeof buildSharedQuoteQuery>
  try {
    instrument = await resolveSharedInstrument(code)
    payload = buildSharedQuoteQuery(instrument.instrument)
    const response = await coreRequest<SharedQuoteViewResponse>(
      "/v1/market/quote",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    )
    return mapSharedQuoteResponse(response, instrument, payload)
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `原生共享报价暂不可用（503）：${error.message}。未从日线构造报价，也未回退模拟数据。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 422) {
      throw new ApiError(
        `共享报价请求被拒绝（422）：${error.message}。未从日线构造报价，也未回退模拟数据。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 0) {
      throw new ApiError(
        `共享报价服务无法连接：${error.message}。未从日线构造报价，也未回退模拟数据。`,
        error.status
      )
    }
    if (error instanceof ApiError) throw error
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `共享报价响应不可用：${detail}。未从日线构造报价，也未回退模拟数据。`,
      502
    )
  }
}



function validateQuoteRefreshResponse(
  value: QuoteRefreshResponse,
  instrument: SharedInstrumentSearchItem
): QuoteRefreshResult {
  if (
    value.schema_version !== "quote-refresh/v1" ||
    value.origin !== "manual_refresh" ||
    value.cache_bypassed !== true ||
    value.alert_event_created !== false ||
    value.external_notification_sent !== false ||
    value.trade_created !== false ||
    typeof value.provider_unchanged !== "boolean" ||
    !Number.isInteger(value.cooldown_seconds) ||
    value.cooldown_seconds < 1 ||
    value.cooldown_seconds > 3600
  ) {
    throw new Error("拉取最新响应的固定边界或冷却字段非法")
  }
  if (!value.quote.requested_at) {
    throw new Error("拉取最新响应缺少 provider 请求时间")
  }
  const query = buildSharedQuoteQuery(
    instrument.instrument,
    new Date(value.quote.requested_at)
  )
  const mapped = mapSharedQuoteResponse(
    {
      schema_version: "quote-view/v2",
      quote: value.quote,
      formal_use_eligible: false,
    },
    instrument,
    query
  )
  return {
    quote: mapped,
    providerUnchanged: value.provider_unchanged,
    cooldownSeconds: value.cooldown_seconds,
    origin: "manual_refresh",
    cacheBypassed: true,
    alertEventCreated: false,
    externalNotificationSent: false,
    tradeCreated: false,
  }
}



async function sharedRefreshQuote(code: string): Promise<QuoteRefreshResult> {
  const instrument = await resolveSharedInstrument(code)
  try {
    const response = await coreRequest<QuoteRefreshResponse>(
      "/v1/market/quote/refresh",
      {
        method: "POST",
        body: JSON.stringify({ instrument: instrument.instrument }),
      }
    )
    return validateQuoteRefreshResponse(response, instrument)
  } catch (error) {
    if (error instanceof ApiError && error.status === 429) {
      throw new ApiError(
        `拉取最新冷却中：${error.message}`,
        error.status,
        error.retryAfterSeconds
      )
    }
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `拉取最新暂不可用（503）：${error.message}。旧报价继续保留。`,
        error.status,
        error.retryAfterSeconds
      )
    }
    if (error instanceof ApiError && error.status === 422) {
      throw new ApiError(
        `当前证券不能强制拉取（422）：${error.message}`,
        error.status
      )
    }
    if (error instanceof ApiError) throw error
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `拉取最新响应未通过契约校验：${detail}。旧报价继续保留。`,
      502
    )
  }
}



async function sharedAnalysis(code: string, quote?: Quote): Promise<AnalysisResult> {
  let instrument: SharedInstrumentSearchItem
  let response: SharedResearchAnalysisResponse
  try {
    instrument = await resolveSharedInstrument(code)
    const payload = buildSharedResearchRequest(instrument, quote?.sourceSnapshot)
    response = await coreRequest<SharedResearchAnalysisResponse>(
      "/v1/analysis/research",
      {
        method: "POST",
        body: JSON.stringify(payload),
      }
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `正式研究暂不可用（503）：${error.message}。未回退旧版评分。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 422) {
      throw new ApiError(
        `正式研究输入被拒绝（422）：${error.message}。未回退旧版评分。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 0) {
      throw new ApiError(
        `共享正式研究服务无法连接：${error.message}。未回退旧版评分。`,
        error.status
      )
    }
    throw error
  }

  try {
    return mapResearchAnalysisResponse(response, instrument, quote?.sourceSnapshot)
  } catch (error) {
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `正式研究响应不可用：${detail}。未回退旧版评分。`,
      502
    )
  }
}



async function sharedTechnicalStructure(
  code: string,
  period: Period = "2Y",
  timeframe: Timeframe = "日线"
): Promise<TechnicalStructureResponse> {
  let instrument: SharedInstrumentSearchItem
  let payload: ReturnType<typeof buildSharedTechnicalStructureRequest>
  let response: unknown
  try {
    instrument = await resolveSharedInstrument(code)
    payload = buildSharedTechnicalStructureRequest(instrument, period, timeframe)
    const priorState = priorChanStateFor(
      instrument.instrument,
      payload.query.timeframe
    )
    if (priorState) payload.prior_state = priorState
    response = await coreRequest<unknown>("/v1/analysis/structures", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  } catch (error) {
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `正式技术结构暂不可用（503）：${error.message}。未回退旧版技术接口。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 422) {
      throw new ApiError(
        `正式技术结构输入被拒绝（422）：${error.message}。未回退旧版技术接口。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 0) {
      throw new ApiError(
        `共享技术结构服务无法连接：${error.message}。未回退旧版技术接口。`,
        error.status
      )
    }
    throw error
  }

  try {
    const admitted = validateTechnicalStructureResponse(response, instrument, payload)
    rememberChanState(
      admitted.current_state,
      instrument.instrument,
      payload.query.timeframe
    )
    return admitted
  } catch (error) {
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `共享技术结构响应不可用：${detail}。未回退旧版技术接口。`,
      502
    )
  }
}



async function sharedFundamentalSnapshot(
  code: string
): Promise<FundamentalSnapshotResponse> {
  let instrument: SharedInstrumentSearchItem | null = null
  let payload: ReturnType<typeof buildSharedFundamentalRequest>
  let response: unknown
  try {
    instrument = await resolveSharedInstrument(code)
    payload = buildSharedFundamentalRequest(instrument)
    response = await coreRequest<unknown>("/v1/fundamentals/snapshots", {
      method: "POST",
      body: JSON.stringify(payload),
    })
  } catch (error) {
    if (error instanceof ApiError && error.status === 422) {
      if (instrument && instrument.instrument.asset_type !== "stock") {
        throw new ApiError(
          `不适用发行人基本面：${instrument.display_name}（${instrument.instrument.asset_type}）不是公司股票。未回退旧版基本面接口。`,
          error.status
        )
      }
      throw new ApiError(
        `正式基本面输入被拒绝（422）：${error.message}。未回退旧版基本面接口。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 503) {
      throw new ApiError(
        `正式基本面数据暂不可用（503）：${error.message}。未回退旧版基本面接口。`,
        error.status
      )
    }
    if (error instanceof ApiError && error.status === 0) {
      throw new ApiError(
        `共享基本面服务无法连接：${error.message}。未回退旧版基本面接口。`,
        error.status
      )
    }
    throw error
  }

  if (!instrument) {
    throw new ApiError("共享基本面请求上下文缺失。未回退旧版基本面接口。", 502)
  }
  try {
    return validateFundamentalSnapshotResponse(response, instrument, payload)
  } catch (error) {
    const detail = error instanceof Error ? error.message : "响应结构未知"
    throw new ApiError(
      `共享基本面快照响应不可用：${detail}。未回退旧版基本面接口。`,
      502
    )
  }
}



function throwSharedWatchlistError(error: unknown, operation: string): never {
  if (error instanceof ApiError && error.outcomeUncertain) throw error
  if (error instanceof ApiError && error.status === 404) {
    throw new ApiError(
      `自选条目不存在或已删除（404）：${error.message}。已请求刷新 Shared 配置。`,
      error.status
    )
  }
  if (error instanceof ApiError && error.status === 409) {
    throw new ApiError(
      `自选配置版本冲突（409）：${error.message}。列表将刷新，请基于最新 revision 重试。`,
      error.status
    )
  }
  if (error instanceof ApiError && error.status === 422) {
    throw new ApiError(
      `Shared 自选${operation}请求被拒绝（422）：${error.message}。V1 仅接受中国内地股票、ETF 与指数；XBI/美股仍待扩展，未回退旧接口。`,
      error.status
    )
  }
  if (error instanceof ApiError && error.status === 503) {
    throw new ApiError(
      `Shared 自选配置服务暂不可用（503）：${error.message}。未回退旧接口或本地伪存储。`,
      error.status
    )
  }
  if (error instanceof ApiError && error.status === 0) {
    throw new ApiError(
      `Shared 自选配置服务无法连接：${error.message}。未回退旧接口或本地伪存储。`,
      error.status
    )
  }
  if (error instanceof ApiError) throw error
  const detail = error instanceof Error ? error.message : "响应结构未知"
  throw new ApiError(
    `Shared 自选${operation}失败：${detail}。未回退旧接口或本地伪存储。`,
    502
  )
}



async function sharedWatchlistEntries(): Promise<WatchlistEntryListResponse> {
  let response: unknown
  try {
    response = await coreRequest<unknown>(
      "/v1/watchlist/entries",
      undefined,
      LOCAL_CONFIG_READ_OPTIONS
    )
  } catch (error) {
    throwSharedWatchlistError(error, "读取")
  }
  try {
    return validateWatchlistListResponse(response)
  } catch (error) {
    throwSharedWatchlistError(error, "读取响应校验")
  }
}



async function sharedCreateWatchlistEntry(
  candidate: SharedInstrumentSearchItem,
  role: WatchlistRole,
  note = ""
): Promise<WatchlistEntryResponse> {
  let payload: ReturnType<typeof buildSharedWatchlistCreateRequest>
  try {
    payload = buildSharedWatchlistCreateRequest(candidate, role, note)
  } catch (error) {
    const detail = error instanceof Error ? error.message : "创建参数无效"
    throw new ApiError(
      `Shared 自选创建请求被拒绝（422）：${detail}。未回退旧接口或本地伪存储。`,
      422
    )
  }
  let response: unknown
  try {
    response = await coreRequest<unknown>("/v1/watchlist/entries", {
      method: "POST",
      body: JSON.stringify(payload),
    }, LOCAL_CONFIG_WRITE_OPTIONS)
  } catch (error) {
    throwSharedWatchlistError(error, "创建")
  }
  try {
    return validateWatchlistResponse(response, "created")
  } catch (error) {
    throwSharedWatchlistError(error, "创建响应校验")
  }
}



async function sharedPatchWatchlistEntry(
  entryId: string,
  expectedRevision: number,
  changes: Omit<WatchlistEntryPatchRequest, "expected_revision">
): Promise<WatchlistEntryResponse> {
  let payload: WatchlistEntryPatchRequest
  try {
    payload = buildSharedWatchlistPatchRequest(expectedRevision, changes)
  } catch (error) {
    const detail = error instanceof Error ? error.message : "更新参数无效"
    throw new ApiError(
      `Shared 自选更新请求被拒绝（422）：${detail}。未回退旧接口或本地伪存储。`,
      422
    )
  }
  let response: unknown
  try {
    response = await coreRequest<unknown>(
      `/v1/watchlist/entries/${encodeURIComponent(entryId)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
      LOCAL_CONFIG_WRITE_OPTIONS
    )
  } catch (error) {
    throwSharedWatchlistError(error, "更新")
  }
  try {
    return validateWatchlistResponse(response, "updated")
  } catch (error) {
    throwSharedWatchlistError(error, "更新响应校验")
  }
}



async function sharedReorderWatchlistEntries(
  scope: WatchlistEntriesReorderRequest["scope"],
  role: WatchlistRole,
  orderedItems: WatchlistEntriesReorderRequest["ordered_items"]
): Promise<WatchlistEntryListResponse> {
  let payload: WatchlistEntriesReorderRequest
  try {
    payload = buildSharedWatchlistReorderRequest(scope, role, orderedItems)
  } catch (error) {
    const detail = error instanceof Error ? error.message : "排序参数无效"
    throw new ApiError(
      `Shared 自选排序请求被拒绝（422）：${detail}。未回退本地临时顺序。`,
      422
    )
  }
  let response: unknown
  try {
    response = await coreRequest<unknown>("/v1/watchlist/entries/order", {
      method: "PUT",
      body: JSON.stringify(payload),
    }, LOCAL_CONFIG_WRITE_OPTIONS)
  } catch (error) {
    throwSharedWatchlistError(error, "排序")
  }
  try {
    return validateWatchlistListResponse(response)
  } catch (error) {
    throwSharedWatchlistError(error, "排序响应校验")
  }
}



async function sharedDeleteWatchlistEntry(
  entryId: string,
  expectedRevision: number
): Promise<WatchlistEntryResponse> {
  if (!Number.isInteger(expectedRevision) || expectedRevision < 1) {
    throw new ApiError("expected_revision 必须至少为 1", 422)
  }
  let response: unknown
  try {
    response = await coreRequest<unknown>(
      `/v1/watchlist/entries/${encodeURIComponent(entryId)}?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
      LOCAL_CONFIG_WRITE_OPTIONS
    )
  } catch (error) {
    throwSharedWatchlistError(error, "删除")
  }
  try {
    return validateWatchlistResponse(response, "deleted")
  } catch (error) {
    throwSharedWatchlistError(error, "删除响应校验")
  }
}



async function sharedHealth(): Promise<HealthResponse> {
  const response = await coreRequest<unknown>("/v1/system/health")
  return validateSharedHealthResponse(response)
}



// ── 端点 ─────────────────────────────────────────────────────────
export const api = {
  health: sharedHealth,
  search: sharedSearch,
  quote: sharedQuote,
  refreshQuote: sharedRefreshQuote,
  chart: sharedChart,
  analysis: sharedAnalysis,
  tech: sharedTechnicalStructure,
  fundamental: sharedFundamentalSnapshot,
  watchlistEntries: sharedWatchlistEntries,
  watchlistCandidateSearch: sharedWatchlistCandidateSearch,
  createWatchlistEntry: sharedCreateWatchlistEntry,
  patchWatchlistEntry: sharedPatchWatchlistEntry,
  reorderWatchlistEntries: sharedReorderWatchlistEntries,
  deleteWatchlistEntry: sharedDeleteWatchlistEntry,
}



// ── Hook ─────────────────────────────────────────────────────────
export interface ApiState<T> {
  data: T | null
  loading: boolean
  error: string | null
  reload: () => void
}



interface ApiGenerationState<T> {
  requestKey: string
  data: T | null
  loading: boolean
  error: string | null
}



/** 通用数据获取 hook；requestKey 变化时自动开始新的请求代次。 */
export function useApi<T>(
  fetcher: (() => Promise<T>) | null,
  requestKey: string
): ApiState<T> {
  const [generation, setGeneration] = useState<ApiGenerationState<T>>(() => ({
    requestKey,
    data: null,
    loading: Boolean(fetcher),
    error: null,
  }))
  const seq = useRef(0)
  const pendingRef = useRef<{
    requestKey: string
    promise: Promise<T>
  } | null>(null)
  const fetcherRef = useRef(fetcher)

  // Keep the latest request factory without reading or writing refs during
  // render. This effect is declared before the request effect below, so a new
  // request key always observes the matching fetcher.
  useEffect(() => {
    fetcherRef.current = fetcher
  }, [fetcher])

  const run = useCallback(
    (preserveLastGood: boolean, reuseDependencyPromise: boolean) => {
      // requestKey intentionally defines a request generation even though the
      // value itself is not sent to the server.
      void requestKey
      // Invalidate every older request even when the new fetcher is null.  This
      // prevents an in-flight response from a previous stock/overlay selection
      // from writing itself back after the user has moved on.
      const mySeq = ++seq.current
      const fn = fetcherRef.current
      if (!fn) {
        setGeneration({
          requestKey,
          data: null,
          loading: false,
          error: null,
        })
        return
      }
      // Do not render the previous dependency generation while a slow request is
      // resolving (most visibly, an old stock's wave/Chan overlay on a new K-line).
      setGeneration((current) => ({
        requestKey,
        data:
          preserveLastGood && current.requestKey === requestKey
            ? current.data
            : null,
        loading: true,
        error: null,
      }))
      let pending = pendingRef.current
      if (
        !reuseDependencyPromise ||
        !pending ||
        pending.requestKey !== requestKey
      ) {
        pending = { requestKey, promise: fn() }
        pendingRef.current = pending
        const clearPending = () => {
          if (pendingRef.current === pending) pendingRef.current = null
        }
        pending.promise.then(clearPending, clearPending)
      }
      pending.promise
        .then((d) => {
          if (seq.current === mySeq) {
            setGeneration({
              requestKey,
              data: d,
              loading: false,
              error: null,
            })
          }
        })
        .catch((e: unknown) => {
          if (seq.current === mySeq) {
            setGeneration((current) => ({
              requestKey,
              data:
                preserveLastGood && current.requestKey === requestKey
                  ? current.data
                  : null,
              loading: false,
              error: e instanceof Error ? e.message : "请求失败",
            }))
          }
        })
    },
    [requestKey]
  )

  const reload = useCallback(() => {
    run(true, false)
  }, [run])

  useEffect(() => {
    // React StrictMode may replay the dependency effect. Reuse only that exact
    // dependency-generation promise; explicit reloads above always start fresh.
    run(false, true)
    return () => {
      // Invalidate an in-flight response as soon as its request generation is
      // replaced or the component unmounts.
      seq.current += 1
    }
  }, [run])

  // Effects start the next request after render. Hide the previous generation
  // during that first render as well, so no consumer can observe A's quote,
  // chart, or name under B's request key.
  const visible =
    generation.requestKey === requestKey
      ? generation
      : {
          requestKey,
          data: null,
          loading: Boolean(fetcher),
          error: null,
        }
  return {
    data: visible.data,
    loading: visible.loading,
    error: visible.error,
    reload,
  }
}
