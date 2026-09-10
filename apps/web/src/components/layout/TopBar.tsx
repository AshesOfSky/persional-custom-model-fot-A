import { useEffect, useId, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { AlertTriangle, Check, LoaderCircle, Plus, Search } from "lucide-react"
import { api, ApiError, useApi } from "@/api/client"
import {
  dedupeInstrumentSearchItems,
  findWatchlistEntryByInstrument,
  searchItemMarket,
  sharedInstrumentIdentityKey,
  watchlistRoleForInstrument,
  type SharedInstrumentSearchItem,
} from "@/api/shared"
import { TerminalPdfExport } from "@/components/export/TerminalPdfExport"
import { notifyWatchlistChanged } from "@/lib/watchlistEvents"
import { useStock } from "@/state/stock"
import { cn } from "@/lib/utils"

const MODE_LABEL = {
  live: "实时",
  delayed: "延迟",
  cache: "缓存",
  fallback: "不可用",
  demo: "演示",
  simulation: "模拟",
} as const

const MODE_CLS = {
  live: "bg-futu-up/15 text-futu-up",
  delayed: "bg-futu-teal/15 text-futu-teal",
  cache: "bg-futu-gold/15 text-futu-gold",
  fallback: "bg-futu-orange/15 text-futu-orange",
  demo: "bg-futu-purple/15 text-futu-purple",
  simulation: "bg-futu-blue/15 text-futu-blue",
} as const

type SearchStatus = "idle" | "loading" | "success" | "error"

interface SearchState {
  query: string
  status: SearchStatus
  items: SharedInstrumentSearchItem[]
  error: string | null
}

const EMPTY_SEARCH: SearchState = {
  query: "",
  status: "idle",
  items: [],
  error: null,
}

type WatchlistAddStatus = "adding" | "added" | "error"

interface WatchlistAddState {
  status: WatchlistAddStatus
  message?: string
}

function searchResultKey(result: SharedInstrumentSearchItem): string {
  return sharedInstrumentIdentityKey(result.instrument)
}

function GlobalSearch() {
  const { setStock } = useStock()
  const navigate = useNavigate()
  const listboxId = useId()
  const [q, setQ] = useState("")
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [retryToken, setRetryToken] = useState(0)
  const [searchState, setSearchState] = useState<SearchState>(EMPTY_SEARCH)
  const [watchlistAdd, setWatchlistAdd] = useState<Record<string, WatchlistAddState>>({})
  const [watchlistError, setWatchlistError] = useState<string | null>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const searchSeq = useRef(0)
  const normalizedQuery = q.trim()
  const stateIsCurrent = searchState.query === normalizedQuery
  const status: SearchStatus = !normalizedQuery
    ? "idle"
    : stateIsCurrent
      ? searchState.status
      : "loading"
  const results = stateIsCurrent ? searchState.items : []
  const showPanel = open && Boolean(normalizedQuery)
  const activeResult = results[activeIndex]

  const selectResult = (result: SharedInstrumentSearchItem) => {
    searchSeq.current += 1
    setStock(result)
    setQ("")
    setOpen(false)
    setActiveIndex(-1)
    setSearchState(EMPTY_SEARCH)
    navigate("/")
  }

  const markWatchlistAdded = (key: string) => {
    setWatchlistAdd((current) => ({
      ...current,
      [key]: { status: "added" },
    }))
    notifyWatchlistChanged()
  }

  const addResultToWatchlist = async (result: SharedInstrumentSearchItem) => {
    const key = searchResultKey(result)
    setWatchlistError(null)
    setWatchlistAdd((current) => ({
      ...current,
      [key]: { status: "adding" },
    }))

    try {
      const role = watchlistRoleForInstrument(result.instrument)
      if (!role) {
        throw new Error(`${result.display_name} 当前不支持加入 V1 自选配置`)
      }
      await api.createWatchlistEntry(result, role)
      markWatchlistAdded(key)
    } catch (cause) {
      if (
        cause instanceof ApiError &&
        (cause.status === 409 || cause.outcomeUncertain)
      ) {
        try {
          const current = await api.watchlistEntries()
          if (findWatchlistEntryByInstrument(current, result.instrument)) {
            markWatchlistAdded(key)
            return
          }
        } catch {
          // Preserve the original conflict/uncertain outcome if read-back fails.
        }
      }
      const message = cause instanceof Error ? cause.message : "创建自选配置失败"
      setWatchlistAdd((current) => ({
        ...current,
        [key]: { status: "error", message },
      }))
      setWatchlistError(message)
    }
  }

  useEffect(() => {
    const query = q.trim()
    const mySeq = ++searchSeq.current
    if (!query) return

    const timer = window.setTimeout(() => {
      api
        .watchlistCandidateSearch(query)
        .then((items) => {
          if (searchSeq.current === mySeq) {
            const uniqueItems = dedupeInstrumentSearchItems(items)
            setSearchState({
              query,
              status: "success",
              items: uniqueItems,
              error: null,
            })
            setActiveIndex(uniqueItems.length > 0 ? 0 : -1)
          }
        })
        .catch((error: unknown) => {
          if (searchSeq.current === mySeq) {
            const message =
              error instanceof Error && error.message.trim()
                ? error.message
                : "搜索请求失败，请稍后重试"
            setSearchState({
              query,
              status: "error",
              items: [],
              error: message,
            })
            setActiveIndex(-1)
          }
        })
    }, 250)

    return () => window.clearTimeout(timer)
  }, [q, retryToken])

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onClick)
    return () => document.removeEventListener("mousedown", onClick)
  }, [])

  return (
    <div
      ref={boxRef}
      className="relative"
      onBlur={(event) => {
        const nextTarget = event.relatedTarget
        if (
          !(nextTarget instanceof Node) ||
          !event.currentTarget.contains(nextTarget)
        ) {
          setOpen(false)
          setActiveIndex(-1)
        }
      }}
    >
      <div
        className={cn(
          "flex h-7 w-64 items-center gap-1.5 rounded border bg-futu-elevated px-2 transition-colors",
          open && normalizedQuery
            ? "border-futu-orange/70"
            : "border-futu-border"
        )}
      >
        {status === "loading" ? (
          <LoaderCircle className="h-3.5 w-3.5 animate-spin text-futu-orange" />
        ) : (
          <Search className="h-3.5 w-3.5 text-futu-dim" />
        )}
        <input
          value={q}
          onChange={(e) => {
            const nextValue = e.target.value
            const nextQuery = nextValue.trim()
            setQ(nextValue)
            setActiveIndex(-1)
            setOpen(Boolean(nextQuery))
            setWatchlistError(null)
            setSearchState(
              nextQuery
                ? {
                    query: nextQuery,
                    status: "loading",
                    items: [],
                    error: null,
                  }
                : EMPTY_SEARCH
            )
          }}
          onFocus={() => {
            if (normalizedQuery) setOpen(true)
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              if (open) {
                event.preventDefault()
                setOpen(false)
                setActiveIndex(-1)
              }
              return
            }

            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
              if (!normalizedQuery) return
              event.preventDefault()
              setOpen(true)
              if (results.length === 0) return
              setActiveIndex((current) => {
                if (event.key === "ArrowDown") {
                  return current < 0 ? 0 : (current + 1) % results.length
                }
                return current < 0
                  ? results.length - 1
                  : (current - 1 + results.length) % results.length
              })
              return
            }

            if (event.key === "Enter" && showPanel && activeResult) {
              event.preventDefault()
              selectResult(activeResult)
            }
          }}
          placeholder="搜索代码 / 名称"
          autoComplete="off"
          spellCheck={false}
          role="combobox"
          aria-label="搜索股票代码或名称"
          aria-autocomplete="list"
          aria-haspopup="listbox"
          aria-expanded={showPanel}
          aria-controls={showPanel ? listboxId : undefined}
          aria-activedescendant={
            showPanel && activeIndex >= 0
              ? `${listboxId}-option-${activeIndex}`
              : undefined
          }
          className="w-full bg-transparent text-xs text-futu-text outline-none placeholder:text-futu-dim"
        />
      </div>
      {showPanel && (
        <div
          id={listboxId}
          role="listbox"
          aria-label="股票搜索结果"
          aria-busy={status === "loading"}
          className="absolute left-0 top-8 z-50 w-72 overflow-hidden rounded border border-futu-border bg-futu-elevated shadow-xl"
        >
          {status === "loading" && (
            <div
              role="status"
              aria-live="polite"
              className="flex items-center gap-2 px-3 py-2.5 text-xs text-futu-sub"
            >
              <LoaderCircle className="h-3.5 w-3.5 animate-spin text-futu-orange" />
              正在搜索“{normalizedQuery}”…
            </div>
          )}

          {status === "error" && (
            <div
              role="alert"
              className="flex items-start gap-2 px-3 py-2.5 text-xs text-futu-down"
            >
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="break-words">{searchState.error}</div>
                <button
                  type="button"
                  className="mt-1.5 text-[11px] font-medium text-futu-orange hover:underline"
                  onClick={() => {
                    setSearchState({
                      query: normalizedQuery,
                      status: "loading",
                      items: [],
                      error: null,
                    })
                    setActiveIndex(-1)
                    setRetryToken((token) => token + 1)
                  }}
                >
                  重新搜索
                </button>
              </div>
            </div>
          )}

          {status === "success" && results.length === 0 && (
            <div
              role="status"
              aria-live="polite"
              className="px-3 py-2.5 text-xs text-futu-sub"
            >
              未找到“{normalizedQuery}”，请检查代码或名称
            </div>
          )}

          {status === "success" &&
            results.map((r, index) => (
              <div
                key={searchResultKey(r)}
                id={`${listboxId}-option-${index}`}
                role="option"
                aria-selected={index === activeIndex}
                className={cn(
                  "flex w-full items-center justify-between px-3 py-1.5 text-left transition-colors",
                  index === activeIndex
                    ? "bg-futu-hover"
                    : "hover:bg-futu-hover"
                )}
                onMouseEnter={() => setActiveIndex(index)}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center justify-between text-left"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => selectResult(r)}
                >
                  <span className="truncate text-xs text-futu-text">
                    {r.display_name}
                  </span>
                  <span className="tnum ml-2 shrink-0 text-[11px] text-futu-sub">
                    {r.instrument.symbol}
                    <span className="ml-1 rounded bg-futu-border/60 px-1 text-[10px]">
                      {searchItemMarket(r)}
                    </span>
                  </span>
                </button>
                {(() => {
                  const addState = watchlistAdd[searchResultKey(r)]
                  const adding = addState?.status === "adding"
                  const added = addState?.status === "added"
                  return (
                    <button
                      type="button"
                      disabled={adding || added}
                      onMouseDown={(event) => event.preventDefault()}
                      onClick={() => void addResultToWatchlist(r)}
                      className={cn(
                        "ml-2 flex h-6 w-6 shrink-0 items-center justify-center rounded border transition-colors",
                        added
                          ? "border-futu-teal/40 bg-futu-teal/10 text-futu-teal"
                          : addState?.status === "error"
                            ? "border-futu-down/40 text-futu-down hover:bg-futu-down/10"
                            : "border-futu-border text-futu-sub hover:border-futu-orange/60 hover:bg-futu-orange/10 hover:text-futu-orange"
                      )}
                      aria-label={
                        added
                          ? `${r.display_name} 已在自选配置`
                          : `添加 ${r.display_name} 到自选配置`
                      }
                      title={addState?.message ?? (added ? "已加入自选配置" : "加入自选配置")}
                    >
                      {adding ? (
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                      ) : added ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        <Plus className="h-3.5 w-3.5" />
                      )}
                    </button>
                  )
                })()}
              </div>
            ))}

          {watchlistError && (
            <div
              role="alert"
              className="border-t border-futu-down/20 px-3 py-2 text-[10px] leading-relaxed text-futu-down"
            >
              添加自选失败：{watchlistError}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Clock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const pad = (n: number) => String(n).padStart(2, "0")
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  const time = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`

  return (
    <span className="tnum text-[11px] text-futu-sub" title="本地时间">
      {date}
      <span className="ml-1.5 text-futu-text">{time}</span>
    </span>
  )
}

export function TopBar() {
  const { data: health } = useApi(api.health, "health")
  const mode = health?.mode ?? "cache"
  const sharedLabel =
    mode === "fallback" ? "Shared 不可用" : `Shared ${MODE_LABEL[mode]}源`
  return (
    <header className="flex h-11 shrink-0 items-center gap-4 border-b border-futu-border bg-[#0c0f15] px-3">
      <GlobalSearch />

      <div className="ml-auto flex items-center gap-3">
        <TerminalPdfExport />
        <Clock />
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium",
            MODE_CLS[mode]
          )}
          title={
            health
              ? `Shared API 状态: ${health.status ?? (health.ok ? "ok" : "degraded")} | 数据源: ${health.sources.join(", ") || "—"}`
              : "正在连接 Shared API..."
          }
        >
          {sharedLabel}
        </span>
      </div>
    </header>
  )
}
