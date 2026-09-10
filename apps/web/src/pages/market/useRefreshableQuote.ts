import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react"
import { api, ApiError, useApi } from "@/api/client"
import type { Quote, QuoteRefreshResult } from "@/api/types"

export type QuoteRefreshPhase =
  | "idle"
  | "queued"
  | "loading"
  | "updated"
  | "unchanged"
  | "cooldown"
  | "error"

export interface RefreshableQuoteState {
  data: Quote | null
  loading: boolean
  error: string | null
  reload: () => void
  refresh: () => void
  refreshPhase: QuoteRefreshPhase
  refreshError: string | null
  cooldownRemaining: number
  refreshedAt: Date | null
}

interface ManualQuoteState {
  code: string
  quote: Quote | null
  phase: QuoteRefreshPhase
  error: string | null
  cooldownUntil: number
  settledAt: number
  refreshedAt: Date | null
}

interface ActiveRefresh {
  code: string
  active: boolean
}

const EMPTY_MANUAL_STATE: ManualQuoteState = {
  code: "",
  quote: null,
  phase: "idle",
  error: null,
  cooldownUntil: 0,
  settledAt: 0,
  refreshedAt: null,
}

/**
 * Keep the normal initial quote and the forced latest request in one state
 * boundary. Manual state is tagged by symbol, so changing symbols immediately
 * hides the old result without an effect-driven reset. A cancelled symbol's
 * late response is ignored and never triggers the chart callback.
 */
export function useRefreshableQuote(
  code: string,
  onRefreshed?: (result: QuoteRefreshResult) => void
): RefreshableQuoteState {
  const base = useApi(() => api.quote(code), `quote:${code}`)
  const [manual, setManual] = useState<ManualQuoteState>(EMPTY_MANUAL_STATE)
  const [clock, setClock] = useState(() => Date.now())
  const activeRefresh = useRef<ActiveRefresh | null>(null)
  const callbackRef = useRef(onRefreshed)

  useLayoutEffect(() => {
    const active = activeRefresh.current
    if (active && active.code !== code) {
      active.active = false
      activeRefresh.current = null
      setManual((previous) =>
        previous.code === active.code &&
        (previous.phase === "queued" || previous.phase === "loading")
          ? {
              ...previous,
              phase: "idle",
              error: null,
              settledAt: 0,
            }
          : previous
      )
    }
  }, [code])

  useLayoutEffect(
    () => () => {
      const active = activeRefresh.current
      if (!active) return
      active.active = false
      activeRefresh.current = null
    },
    []
  )

  useEffect(() => {
    callbackRef.current = onRefreshed
  }, [onRefreshed])

  const ownsManualState = manual.code === code
  const cooldownUntil = ownsManualState ? manual.cooldownUntil : 0
  const cooldownRemaining = Math.max(
    0,
    Math.ceil((cooldownUntil - clock) / 1000)
  )

  useEffect(() => {
    if (cooldownUntil <= Date.now()) return
    const timer = window.setInterval(() => setClock(Date.now()), 250)
    return () => window.clearInterval(timer)
  }, [cooldownUntil])

  let refreshPhase: QuoteRefreshPhase = ownsManualState
    ? manual.phase
    : "idle"
  if (refreshPhase === "updated" || refreshPhase === "unchanged") {
    if (clock - manual.settledAt >= 2000) {
      refreshPhase = cooldownRemaining > 0 ? "cooldown" : "idle"
    }
  } else if (refreshPhase === "cooldown" && cooldownRemaining === 0) {
    refreshPhase = "idle"
  }

  const refresh = useCallback(() => {
    const inFlight = activeRefresh.current
    if (inFlight?.active && inFlight.code === code) return
    if (cooldownRemaining > 0) return

    const token: ActiveRefresh = { code, active: true }
    activeRefresh.current = token
    setManual((previous) => ({
      code,
      quote: previous.code === code ? previous.quote : null,
      phase: "queued",
      error: null,
      cooldownUntil: previous.code === code ? previous.cooldownUntil : 0,
      settledAt: 0,
      refreshedAt: previous.code === code ? previous.refreshedAt : null,
    }))

    window.setTimeout(() => {
      if (!token.active || activeRefresh.current !== token) return
      setManual((previous) =>
        previous.code === code ? { ...previous, phase: "loading" } : previous
      )
      api
        .refreshQuote(code)
        .then((result) => {
          if (!token.active || activeRefresh.current !== token) return
          token.active = false
          activeRefresh.current = null
          const now = Date.now()
          setClock(now)
          setManual({
            code,
            quote: result.quote,
            phase: result.providerUnchanged ? "unchanged" : "updated",
            error: null,
            cooldownUntil: now + result.cooldownSeconds * 1000,
            settledAt: now,
            refreshedAt: new Date(now),
          })
          callbackRef.current?.(result)
        })
        .catch((cause: unknown) => {
          if (!token.active || activeRefresh.current !== token) return
          token.active = false
          activeRefresh.current = null
          const now = Date.now()
          const message =
            cause instanceof Error ? cause.message : "拉取最新失败"
          const cooldownSeconds =
            cause instanceof ApiError && cause.status === 429
              ? (cause.retryAfterSeconds ?? 10)
              : 0
          setClock(now)
          setManual((previous) => ({
            code,
            quote: previous.code === code ? previous.quote : null,
            phase: cooldownSeconds > 0 ? "cooldown" : "error",
            error: message,
            cooldownUntil: now + cooldownSeconds * 1000,
            settledAt: now,
            refreshedAt: previous.code === code ? previous.refreshedAt : null,
          }))
        })
    }, 0)
  }, [code, cooldownRemaining])

  const manualQuote = ownsManualState ? manual.quote : null
  return {
    data: manualQuote ?? base.data,
    loading: base.loading && manualQuote === null,
    error: base.error,
    reload: base.reload,
    refresh,
    refreshPhase,
    refreshError: ownsManualState ? manual.error : null,
    cooldownRemaining,
    refreshedAt: ownsManualState ? manual.refreshedAt : null,
  }
}
