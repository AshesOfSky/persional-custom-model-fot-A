import {
  type DragEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import { useLocation, useNavigate } from "react-router"
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  GripVertical,
  LoaderCircle,
  Pin,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react"
import { api, ApiError, useApi } from "@/api/client"
import { watchlistRoleForInstrument } from "@/api/shared"
import type {
  Quote,
  SharedInstrumentSearchItem,
  WatchlistEntryView,
  WatchlistRole,
} from "@/api/types"
import { useStock } from "@/state/stock"
import { fmtPct, fmtPrice, trendClass } from "@/lib/fmt"
import { WATCHLIST_CHANGED_EVENT } from "@/lib/watchlistEvents"
import { ErrorState, LoadingState } from "@/components/terminal/States"
import { cn } from "@/lib/utils"

const ROLE_LABELS: Record<WatchlistRole, string> = {
  core: "核心持仓",
  watchlist: "普通自选",
  etf: "ETF",
  index: "板块指数",
}

const ROLE_ORDER: WatchlistRole[] = ["core", "watchlist", "etf", "index"]
type WatchlistOrderScope = "securities" | "sectors"
interface WatchlistOrderOverride {
  entryIds: string[]
  revisionKey: string
}
type KeyboardOrderDirection = "up" | "down"
interface KeyboardOrderIntent {
  entryId: string
  displayName: string
  direction: KeyboardOrderDirection
}
interface OrderFeedback {
  kind: "pending" | "success" | "uncertain" | "error"
  message: string
}

function orderGroupKey(
  scope: WatchlistOrderScope,
  role: WatchlistRole
): string {
  return `${scope}:${role}`
}

function orderRevisionKey(entries: WatchlistEntryView[]): string {
  return entries
    .map((entry) => `${entry.entry_id}:${entry.revision}`)
    .sort()
    .join("|")
}

function codeForDisplay(entry: WatchlistEntryView): string {
  return entry.instrument.symbol
}

function instrumentKey(
  instrument: SharedInstrumentSearchItem["instrument"]
): string {
  return [
    instrument.market,
    instrument.exchange,
    instrument.asset_type,
    instrument.currency,
    instrument.symbol,
  ].join("|")
}

function QuoteCell({
  entry,
  eager,
}: {
  entry: WatchlistEntryView
  eager: boolean
}) {
  const [requested, setRequested] = useState(false)
  const shouldLoad = eager || requested
  const quote = useApi<Quote>(
    shouldLoad ? () => api.quote(codeForDisplay(entry)) : null,
    `watchlist-quote:${entry.entry_id}:${shouldLoad ? "load" : "idle"}`
  )

  if (!shouldLoad) {
    return (
      <button
        type="button"
        onClick={() => setRequested(true)}
        className="shrink-0 text-right text-[10px] text-futu-dim hover:text-futu-orange"
        title="按需请求该证券的 Shared Quote；不会写入自选配置"
      >
        查看报价
      </button>
    )
  }
  if (quote.loading && !quote.data) {
    return (
      <div className="flex items-center justify-end gap-1 text-[10px] text-futu-dim">
        <LoaderCircle className="h-3 w-3 animate-spin" />
        报价
      </div>
    )
  }
  if (!quote.data) {
    return (
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation()
          quote.reload()
        }}
        className="text-right text-[10px] text-futu-gold hover:text-futu-orange"
        title={`${quote.error ?? "报价不可用"}。自选配置仍有效，价格未写入配置。`}
      >
        报价不可用
      </button>
    )
  }
  return (
    <div className="text-right" title="Shared Quote 独立请求；不属于自选配置">
      <div
        className={cn(
          "tnum text-xs font-medium",
          trendClass(quote.data.changePct)
        )}
      >
        {fmtPrice(quote.data.price)}
      </div>
      <div
        className={cn(
          "tnum text-[10px]",
          trendClass(quote.data.changePct)
        )}
      >
        {fmtPct(quote.data.changePct)}
      </div>
    </div>
  )
}

function EntryRow({
  entry,
  active,
  pending,
  orderingSupported,
  ordering,
  canMoveUp,
  canMoveDown,
  dragging,
  dragTarget,
  onOpen,
  onPin,
  onMoveUp,
  onMoveDown,
  onRoleChange,
  onDelete,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
}: {
  entry: WatchlistEntryView
  active: boolean
  pending: boolean
  orderingSupported: boolean
  ordering: boolean
  canMoveUp: boolean
  canMoveDown: boolean
  dragging: boolean
  dragTarget: boolean
  onOpen: () => void
  onPin: () => void
  onMoveUp: () => void
  onMoveDown: () => void
  onRoleChange: (role: WatchlistRole) => void
  onDelete: () => void
  onDragStart: (event: DragEvent<HTMLButtonElement>) => void
  onDragEnd: () => void
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
}) {
  const stockRole = entry.role === "core" || entry.role === "watchlist"
  return (
    <div
      data-watchlist-entry={entry.entry_id}
      onDragOver={onDragOver}
      onDrop={onDrop}
      className={cn(
        "group border-l-2 px-2 py-1.5 transition-all",
        active
          ? "border-futu-orange bg-futu-orange/10"
          : "border-transparent hover:bg-futu-hover",
        dragging && "opacity-45",
        dragTarget && "bg-futu-orange/10 ring-1 ring-inset ring-futu-orange/55"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <button
          type="button"
          draggable={orderingSupported && !pending && !ordering}
          disabled={!orderingSupported || pending || ordering}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          className="-ml-1 shrink-0 cursor-grab rounded p-0.5 text-futu-dim hover:bg-futu-elevated hover:text-futu-orange active:cursor-grabbing disabled:cursor-wait disabled:opacity-35"
          aria-label={`拖动排序 ${entry.display_name}`}
          aria-grabbed={dragging}
          title={
            orderingSupported
              ? "拖动调整本组顺序；置顶与未置顶条目分别排序"
              : "常驻 API 尚未加载排序字段；自选数据仍正常显示"
          }
        >
          <GripVertical className="h-3.5 w-3.5" />
        </button>
        <div className="-ml-1 flex shrink-0 items-center">
          <button
            type="button"
            disabled={
              !orderingSupported || pending || ordering || !canMoveUp
            }
            onClick={onMoveUp}
            data-watchlist-order-entry={entry.entry_id}
            data-watchlist-order-direction="up"
            className="rounded p-0.5 text-futu-dim hover:bg-futu-elevated hover:text-futu-orange disabled:cursor-not-allowed disabled:opacity-30"
            aria-label={`上移 ${entry.display_name}`}
            title={
              !orderingSupported
                ? "常驻 API 尚未加载排序字段"
                : canMoveUp
                  ? "在当前置顶分区内上移一位"
                  : "已是当前置顶分区首位"
            }
          >
            <ArrowUp className="h-3 w-3" />
          </button>
          <button
            type="button"
            disabled={
              !orderingSupported || pending || ordering || !canMoveDown
            }
            onClick={onMoveDown}
            data-watchlist-order-entry={entry.entry_id}
            data-watchlist-order-direction="down"
            className="rounded p-0.5 text-futu-dim hover:bg-futu-elevated hover:text-futu-orange disabled:cursor-not-allowed disabled:opacity-30"
            aria-label={`下移 ${entry.display_name}`}
            title={
              !orderingSupported
                ? "常驻 API 尚未加载排序字段"
                : canMoveDown
                  ? "在当前置顶分区内下移一位"
                  : "已是当前置顶分区末位"
            }
          >
            <ArrowDown className="h-3 w-3" />
          </button>
        </div>
        <button
          type="button"
          onClick={onOpen}
          data-watchlist-open-entry={entry.entry_id}
          className="min-w-0 flex-1 text-left"
        >
          <div className="min-w-0">
            <div className="truncate text-xs text-futu-text">
              {entry.display_name}
            </div>
            <div className="tnum text-[10px] text-futu-dim">
              {codeForDisplay(entry)} · rev {entry.revision}
            </div>
          </div>
        </button>
        <button
          type="button"
          disabled={!orderingSupported || pending || ordering}
          onClick={onPin}
          className={cn(
            "shrink-0 rounded p-0.5 transition-colors disabled:cursor-wait disabled:opacity-40",
            entry.pinned
              ? "bg-futu-orange/15 text-futu-orange"
              : "text-futu-dim opacity-65 hover:bg-futu-orange/10 hover:text-futu-orange group-hover:opacity-100"
          )}
          aria-label={`${entry.pinned ? "取消置顶" : "置顶"} ${entry.display_name}`}
          aria-pressed={entry.pinned}
          title={
            orderingSupported
              ? entry.pinned
                ? "取消置顶"
                : "置顶到本组顶部"
              : "常驻 API 受控重载后开放置顶"
          }
        >
          <Pin className={cn("h-3 w-3", entry.pinned && "fill-current")} />
        </button>
        <QuoteCell entry={entry} eager={active} />
      </div>
      <div className="mt-1 flex items-center justify-between gap-1">
        {stockRole ? (
          <select
            aria-label={`调整 ${entry.display_name} 的自选分组`}
            value={entry.role}
            disabled={pending || ordering}
            onChange={(event) =>
              onRoleChange(event.target.value as WatchlistRole)
            }
            className="h-5 min-w-0 flex-1 rounded border border-futu-border bg-futu-elevated px-1 text-[10px] text-futu-sub outline-none focus:border-futu-orange"
          >
            <option value="core">核心持仓</option>
            <option value="watchlist">普通自选</option>
          </select>
        ) : (
          <span className="text-[10px] text-futu-dim">
            {ROLE_LABELS[entry.role]} · 配置
          </span>
        )}
        <button
          type="button"
          disabled={pending || ordering}
          onClick={onDelete}
          className="rounded p-0.5 text-futu-dim opacity-70 hover:bg-futu-up/10 hover:text-futu-up disabled:cursor-wait disabled:opacity-40"
          aria-label={`删除 ${entry.display_name} 自选配置`}
          title="软删除配置；不会创建交易或更改预警"
        >
          {pending ? (
            <LoaderCircle className="h-3 w-3 animate-spin" />
          ) : (
            <Trash2 className="h-3 w-3" />
          )}
        </button>
      </div>

    </div>
  )
}

function AddEntryPanel({
  existing,
  onClose,
  onCreated,
  onUncertain,
}: {
  existing: Set<string>
  onClose: () => void
  onCreated: () => void
  onUncertain: () => void
}) {
  const [query, setQuery] = useState("")
  const [results, setResults] = useState<SharedInstrumentSearchItem[]>([])
  const [searching, setSearching] = useState(false)
  const [creatingKey, setCreatingKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const sequence = useRef(0)

  useEffect(() => {
    const normalized = query.trim()
    const current = ++sequence.current
    if (!normalized) return
    const timer = window.setTimeout(() => {
      api
        .watchlistCandidateSearch(normalized)
        .then((items) => {
          if (sequence.current === current) setResults(items)
        })
        .catch((cause: unknown) => {
          if (sequence.current === current) {
            setResults([])
            setError(
              cause instanceof Error ? cause.message : "Shared 证券搜索失败"
            )
          }
        })
        .finally(() => {
          if (sequence.current === current) setSearching(false)
        })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [query])

  const create = (
    candidate: SharedInstrumentSearchItem,
    role: WatchlistRole
  ) => {
    const key = instrumentKey(candidate.instrument)
    setCreatingKey(key)
    setError(null)
    api
      .createWatchlistEntry(candidate, role)
      .then(() => {
        setQuery("")
        setResults([])
        onCreated()
      })
      .catch((cause: unknown) => {
        setError(cause instanceof Error ? cause.message : "创建自选配置失败")
        if (cause instanceof ApiError && cause.outcomeUncertain) onUncertain()
      })
      .finally(() => setCreatingKey(null))
  }

  return (
    <div className="border-b border-futu-divider bg-futu-elevated/70 p-2">
      <div className="flex items-center gap-1.5">
        <Search className="h-3 w-3 shrink-0 text-futu-dim" />
        <input
          autoFocus
          value={query}
          onChange={(event) => {
            const next = event.target.value
            setQuery(next)
            setResults([])
            setError(null)
            setSearching(Boolean(next.trim()))
          }}
          placeholder="搜索代码 / 名称"
          className="h-6 min-w-0 flex-1 rounded border border-futu-border bg-futu-panel px-1.5 text-[11px] text-futu-text outline-none focus:border-futu-orange"
        />
        <button
          type="button"
          onClick={onClose}
          className="text-futu-dim hover:text-futu-text"
          aria-label="关闭新增自选"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
      {searching && (
        <div className="mt-1 flex items-center gap-1 text-[10px] text-futu-dim">
          <LoaderCircle className="h-3 w-3 animate-spin" />
          正在查询 Shared 证券目录…
        </div>
      )}
      {!searching && query.trim() && results.length === 0 && !error && (
        <div className="mt-1 text-[10px] text-futu-dim">未找到精确证券</div>
      )}
      <div className="mt-1 max-h-48 space-y-1 overflow-y-auto">
        {results.map((candidate) => {
          const key = instrumentKey(candidate.instrument)
          const role = watchlistRoleForInstrument(candidate.instrument)
          const alreadyAdded = existing.has(key)
          const unsupported = role === null
          return (
            <button
              key={`${candidate.instrument.exchange}:${candidate.instrument.symbol}`}
              type="button"
              disabled={alreadyAdded || unsupported || creatingKey !== null}
              onClick={() => role && create(candidate, role)}
              className="flex w-full items-center justify-between gap-2 rounded border border-futu-border bg-futu-panel px-1.5 py-1 text-left hover:border-futu-orange/60 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="min-w-0">
                <span className="block truncate text-[11px] text-futu-text">
                  {candidate.display_name}
                </span>
                <span className="tnum block text-[9px] text-futu-dim">
                  {candidate.legacy_code} · {candidate.instrument.market}/
                  {candidate.instrument.asset_type}
                </span>
              </span>
              <span className="shrink-0 text-[9px] text-futu-sub">
                {creatingKey === key
                  ? "保存中"
                  : alreadyAdded
                    ? "已添加"
                    : unsupported
                      ? "V1 不支持"
                      : "添加"}
              </span>
            </button>
          )
        })}
      </div>
      {error && (
        <div
          role="alert"
          className="mt-1.5 flex items-start gap-1 text-[10px] leading-relaxed text-futu-down"
        >
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      <div className="mt-1.5 text-[9px] leading-relaxed text-futu-dim">
        V1 保存中国内地股票、ETF、指数及同花顺板块配置；XBI/美股会明确拒绝。
      </div>
    </div>
  )
}

/** 左侧 Shared 自选配置面板；报价独立读取，不伪造监控、预警或交易状态。 */
export function Watchlist() {
  const { data, loading, error, reload } = useApi(
    api.watchlistEntries,
    "shared-watchlist-v1"
  )
  const { currentCode, setStock } = useStock()

  const location = useLocation()
  const navigate = useNavigate()
  const sectorOnly = location.pathname.startsWith("/sectors")
  const orderScope: WatchlistOrderScope = sectorOnly ? "sectors" : "securities"
  const [adding, setAdding] = useState(false)
  const [pendingId, setPendingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [orderFeedback, setOrderFeedback] = useState<OrderFeedback | null>(null)
  const watchlistRef = useRef<HTMLElement | null>(null)
  const keyboardOrderFocusRef = useRef<{
    entryId: string
    direction: KeyboardOrderDirection
  } | null>(null)
  const draggingIdRef = useRef<string | null>(null)
  const [draggingId, setDraggingId] = useState<string | null>(null)
  const [dragTargetId, setDragTargetId] = useState<string | null>(null)
  const [orderPendingKey, setOrderPendingKey] = useState<string | null>(null)
  const [orderOverrides, setOrderOverrides] = useState<
    Record<string, WatchlistOrderOverride>
  >({})
  const orderingSupported = data?.ordering_supported === true

  useEffect(() => {
    if (orderPendingKey !== null || keyboardOrderFocusRef.current === null) {
      return
    }
    const frame = window.requestAnimationFrame(() => {
      const pending = keyboardOrderFocusRef.current
      if (pending === null) return
      const controls = Array.from(
        watchlistRef.current?.querySelectorAll<HTMLButtonElement>(
          "[data-watchlist-order-entry]"
        ) ?? []
      ).filter(
        (control) => control.dataset.watchlistOrderEntry === pending.entryId
      )
      const preferred = controls.find(
        (control) =>
          control.dataset.watchlistOrderDirection === pending.direction &&
          !control.disabled
      )
      const alternate = controls.find((control) => !control.disabled)
      const openControl = Array.from(
        watchlistRef.current?.querySelectorAll<HTMLButtonElement>(
          "[data-watchlist-open-entry]"
        ) ?? []
      ).find(
        (control) => control.dataset.watchlistOpenEntry === pending.entryId
      )
      const focusTarget = preferred ?? alternate ?? openControl
      focusTarget?.focus()
      keyboardOrderFocusRef.current = null
    })
    return () => window.cancelAnimationFrame(frame)
  }, [data, orderOverrides, orderPendingKey])

  useEffect(() => {
    const refresh = () => {
      setOrderOverrides({})
      reload()
    }
    window.addEventListener(WATCHLIST_CHANGED_EVENT, refresh)
    return () => window.removeEventListener(WATCHLIST_CHANGED_EVENT, refresh)
  }, [reload])

  const visibleItems = useMemo(
    () =>
      (data?.items ?? []).filter((entry) =>
        sectorOnly
          ? entry.instrument.asset_type === "sector_index"
          : entry.instrument.asset_type !== "sector_index"
      ),
    [data, sectorOnly]
  )

  const grouped = useMemo(() => {
    const result = new Map<WatchlistRole, WatchlistEntryView[]>()
    ROLE_ORDER.forEach((role) => result.set(role, []))
    visibleItems.forEach((entry) => result.get(entry.role)?.push(entry))
    ROLE_ORDER.forEach((role) => {
      const entries = result.get(role) ?? []
      const override = orderOverrides[orderGroupKey(orderScope, role)]
      if (
        !override ||
        override.revisionKey !== orderRevisionKey(entries) ||
        override.entryIds.length !== entries.length ||
        entries.some((entry) => !override.entryIds.includes(entry.entry_id))
      ) {
        return
      }
      const positions = new Map(
        override.entryIds.map((entryId, index) => [entryId, index])
      )
      entries.sort(
        (left, right) =>
          (positions.get(left.entry_id) ?? 0) -
          (positions.get(right.entry_id) ?? 0)
      )
    })
    return result
  }, [orderOverrides, orderScope, visibleItems])

  const existing = useMemo(
    () =>
      new Set(
        (data?.items ?? []).map((entry) => instrumentKey(entry.instrument))
      ),
    [data]
  )



  const reportActionError = (cause: unknown) => {
    setActionError(cause instanceof Error ? cause.message : "自选配置操作失败")
    if (
      cause instanceof ApiError &&
      (cause.status === 404 || cause.status === 409 || cause.outcomeUncertain)
    ) {
      reload()
    }
  }

  const clearOrderOverrides = () => setOrderOverrides({})

  const togglePin = (entry: WatchlistEntryView) => {
    if (!orderingSupported) return
    setPendingId(entry.entry_id)
    setActionError(null)
    clearOrderOverrides()
    api
      .patchWatchlistEntry(entry.entry_id, entry.revision, {
        pinned: !entry.pinned,
      })
      .then(() => reload())
      .catch(reportActionError)
      .finally(() => setPendingId(null))
  }

  const reorderWithinGroup = (
    source: WatchlistEntryView,
    target: WatchlistEntryView,
    placeAfterTarget: boolean,
    keyboardIntent?: KeyboardOrderIntent
  ) => {
    if (
      source.entry_id === target.entry_id ||
      source.role !== target.role ||
      source.pinned !== target.pinned
    ) {
      return
    }
    const entries = [...(grouped.get(source.role) ?? [])]
    const sourceIndex = entries.findIndex(
      (entry) => entry.entry_id === source.entry_id
    )
    if (sourceIndex < 0) return
    const [moved] = entries.splice(sourceIndex, 1)
    const targetIndex = entries.findIndex(
      (entry) => entry.entry_id === target.entry_id
    )
    if (targetIndex < 0) return
    entries.splice(targetIndex + (placeAfterTarget ? 1 : 0), 0, moved)
    const currentOrder = (grouped.get(source.role) ?? []).map(
      (entry) => entry.entry_id
    )
    const nextOrder = entries.map((entry) => entry.entry_id)
    if (nextOrder.every((entryId, index) => entryId === currentOrder[index])) {
      return
    }

    const key = orderGroupKey(orderScope, source.role)
    const directionLabel =
      keyboardIntent?.direction === "up" ? "上移" : "下移"
    const partition = entries.filter((entry) => entry.pinned === source.pinned)
    const partitionPosition =
      partition.findIndex((entry) => entry.entry_id === source.entry_id) + 1
    if (keyboardIntent) {
      keyboardOrderFocusRef.current = {
        entryId: keyboardIntent.entryId,
        direction: keyboardIntent.direction,
      }
      setOrderFeedback({
        kind: "pending",
        message: `${keyboardIntent.displayName}已${directionLabel}，正在保存`,
      })
    }
    setOrderOverrides((current) => ({
      ...current,
      [key]: {
        entryIds: nextOrder,
        revisionKey: orderRevisionKey(entries),
      },
    }))
    setOrderPendingKey(key)
    setActionError(null)
    api
      .reorderWatchlistEntries(
        orderScope,
        source.role,
        entries.map((entry) => ({
          entry_id: entry.entry_id,
          expected_revision: entry.revision,
        }))
      )
      .then(() => {
        if (keyboardIntent) {
          setOrderFeedback({
            kind: "success",
            message: `${keyboardIntent.displayName}已${directionLabel}并保存，当前位置 ${partitionPosition}/${partition.length}`,
          })
        }
        reload()
      })
      .catch((cause: unknown) => {
        setOrderOverrides((current) => {
          const next = { ...current }
          delete next[key]
          return next
        })
        if (keyboardIntent) {
          const uncertain =
            cause instanceof ApiError && cause.outcomeUncertain
          setOrderFeedback({
            kind: uncertain ? "uncertain" : "error",
            message: uncertain
              ? `${keyboardIntent.displayName}${directionLabel}结果待核，已重新读取服务端顺序`
              : `${keyboardIntent.displayName}${directionLabel}未保存，已恢复原顺序`,
          })
        }
        reportActionError(cause)
      })
      .finally(() => {
        setOrderPendingKey(null)
        draggingIdRef.current = null
        setDraggingId(null)
        setDragTargetId(null)
      })
  }

  const moveByKeyboard = (
    entry: WatchlistEntryView,
    direction: KeyboardOrderDirection
  ) => {
    if (
      !orderingSupported ||
      pendingId !== null ||
      orderPendingKey !== null
    ) {
      return
    }
    const partition = (grouped.get(entry.role) ?? []).filter(
      (candidate) => candidate.pinned === entry.pinned
    )
    const currentIndex = partition.findIndex(
      (candidate) => candidate.entry_id === entry.entry_id
    )
    const targetIndex = currentIndex + (direction === "up" ? -1 : 1)
    const target = partition[targetIndex]
    if (currentIndex < 0 || !target) return
    reorderWithinGroup(entry, target, direction === "down", {
      entryId: entry.entry_id,
      displayName: entry.display_name,
      direction,
    })
  }

  const beginDrag = (
    event: DragEvent<HTMLButtonElement>,
    entry: WatchlistEntryView
  ) => {
    if (
      !orderingSupported ||
      pendingId !== null ||
      orderPendingKey !== null
    ) {
      event.preventDefault()
      return
    }
    event.dataTransfer.effectAllowed = "move"
    event.dataTransfer.setData("text/plain", entry.entry_id)
    draggingIdRef.current = entry.entry_id
    setDraggingId(entry.entry_id)
    setDragTargetId(null)
    setActionError(null)
  }

  const dragOver = (
    event: DragEvent<HTMLDivElement>,
    target: WatchlistEntryView
  ) => {
    const source = visibleItems.find(
      (entry) => entry.entry_id === draggingIdRef.current
    )
    if (
      !source ||
      source.role !== target.role ||
      source.pinned !== target.pinned
    ) {
      return
    }
    event.preventDefault()
    event.dataTransfer.dropEffect = "move"
    setDragTargetId(target.entry_id)
  }

  const drop = (
    event: DragEvent<HTMLDivElement>,
    target: WatchlistEntryView
  ) => {
    const source = visibleItems.find(
      (entry) => entry.entry_id === draggingIdRef.current
    )
    if (
      !source ||
      source.role !== target.role ||
      source.pinned !== target.pinned
    ) {
      return
    }
    event.preventDefault()
    const bounds = event.currentTarget.getBoundingClientRect()
    reorderWithinGroup(
      source,
      target,
      event.clientY >= bounds.top + bounds.height / 2
    )
  }

  const changeRole = (entry: WatchlistEntryView, role: WatchlistRole) => {
    if (role === entry.role) return
    setPendingId(entry.entry_id)
    setActionError(null)
    clearOrderOverrides()
    api
      .patchWatchlistEntry(entry.entry_id, entry.revision, { role })
      .then(() => reload())
      .catch(reportActionError)
      .finally(() => setPendingId(null))
  }

  const remove = (entry: WatchlistEntryView) => {
    if (
      !window.confirm(
        `确认删除 ${entry.display_name}（${codeForDisplay(entry)}）的自选配置？\n该操作不会删除行情、预警或产生交易。`
      )
    ) {
      return
    }
    setPendingId(entry.entry_id)
    setActionError(null)
    clearOrderOverrides()
    api
      .deleteWatchlistEntry(entry.entry_id, entry.revision)
      .then(() => reload())
      .catch(reportActionError)
      .finally(() => setPendingId(null))
  }


  return (
    <aside
      ref={watchlistRef}
      className="flex w-60 shrink-0 flex-col overflow-y-auto border-r border-futu-border bg-futu-panel"
    >
      <div className="flex h-8 shrink-0 items-center justify-between border-b border-futu-divider px-3">
        <span className="text-xs font-semibold text-futu-text">
          {sectorOnly ? "板块自选" : "个股自选"}{" "}
          {data ? `(${visibleItems.length})` : ""}
        </span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setAdding((value) => !value)}
            className="text-futu-dim hover:text-futu-orange"
            title="从 Shared 证券目录新增"
            aria-label="新增自选配置"
          >
            <Plus className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => {
              clearOrderOverrides()
              reload()
            }}
            className="text-futu-dim hover:text-futu-orange"
            title="刷新 Shared 自选配置"
            aria-label="刷新自选配置"
          >
            <RefreshCw className="h-3 w-3" />
          </button>
        </div>
      </div>

      {adding && (
        <AddEntryPanel
          existing={existing}
          onClose={() => setAdding(false)}
          onCreated={() => {
            setAdding(false)
            setActionError(null)
            clearOrderOverrides()
            reload()
          }}
          onUncertain={() => {
            clearOrderOverrides()
            reload()
          }}
        />
      )}

      {actionError && (
        <div
          role="alert"
          className="border-b border-futu-down/30 bg-futu-down/10 px-2 py-1.5 text-[10px] leading-relaxed text-futu-down"
        >
          {actionError}
        </div>
      )}
      {orderFeedback && (
        <div
          role="status"
          aria-live="polite"
          aria-atomic="true"
          className={cn(
            "border-b px-2 py-1.5 text-[10px] leading-relaxed",
            orderFeedback.kind === "success" &&
              "border-cyan-400/25 bg-cyan-400/5 text-cyan-200",
            orderFeedback.kind === "pending" &&
              "border-futu-orange/25 bg-futu-orange/5 text-futu-orange",
            orderFeedback.kind === "uncertain" &&
              "border-futu-gold/25 bg-futu-gold/5 text-futu-gold",
            orderFeedback.kind === "error" &&
              "border-futu-down/30 bg-futu-down/10 text-futu-down"
          )}
        >
          {orderFeedback.message}
        </div>
      )}
      {(data?.warnings ?? []).filter((warning) => !warning.startsWith("watchlist membership is configuration") && !warning.startsWith("this operation does not")).map((warning) => (
        <div
          key={warning}
          className="border-b border-futu-orange/20 bg-futu-orange/5 px-2 py-1 text-[9px] text-futu-sub"
        >
          {warning}
        </div>
      ))}

      {data && !orderingSupported && (
        <div className="border-b border-futu-gold/25 bg-futu-gold/5 px-2 py-1.5 text-[9px] leading-relaxed text-futu-gold">
          自选数据已恢复显示；当前 8010 API 尚未加载排序字段，置顶与拖动将在受控重载后开放。
        </div>
      )}

      {loading && !data && <LoadingState text="读取 Shared 自选配置…" />}
      {error && !data && <ErrorState error={error} onRetry={reload} />}
      {data && visibleItems.length === 0 && (
        <div className="px-3 py-8 text-center text-[11px] leading-relaxed text-futu-dim">
          {sectorOnly ? "暂无板块自选" : "暂无个股自选"}
          <br />
          点击右上角“+”从 Shared 证券目录添加
        </div>
      )}

      {ROLE_ORDER.map((role) => {
        const entries = grouped.get(role) ?? []
        if (entries.length === 0) return null
        const groupKey = orderGroupKey(orderScope, role)
        const groupOrdering = orderPendingKey === groupKey
        return (
          <div key={role}>
            <div className="flex items-center justify-between gap-2 bg-futu-elevated/60 px-3 py-1 text-[10px] font-medium text-futu-dim">
              <span>
                {ROLE_LABELS[role]} · {entries.length}
              </span>
              <span
                className="flex items-center gap-0.5 text-[9px] font-normal"
                title="拖动行首手柄或使用每行上移/下移按钮；图钉条目固定显示在本组顶部"
              >
                {groupOrdering ? (
                  <LoaderCircle className="h-2.5 w-2.5 animate-spin text-futu-orange" />
                ) : (
                  <GripVertical className="h-2.5 w-2.5" />
                )}
                {groupOrdering
                  ? "保存中"
                  : orderingSupported
                    ? "拖动 / 键盘"
                    : "待重载"}
              </span>
            </div>
            {entries.map((entry) => {
              const partition = entries.filter(
                (candidate) => candidate.pinned === entry.pinned
              )
              const partitionIndex = partition.findIndex(
                (candidate) => candidate.entry_id === entry.entry_id
              )
              return (
                <EntryRow
                  key={entry.entry_id}
                  entry={entry}
                  active={codeForDisplay(entry) === currentCode}
                  pending={pendingId === entry.entry_id}
                  orderingSupported={orderingSupported}
                  ordering={groupOrdering}
                  canMoveUp={partitionIndex > 0}
                  canMoveDown={
                    partitionIndex >= 0 &&
                    partitionIndex < partition.length - 1
                  }
                  dragging={draggingId === entry.entry_id}
                  dragTarget={dragTargetId === entry.entry_id}
                  onOpen={() => {
                    setStock(codeForDisplay(entry), entry.display_name)
                    navigate("/")
                  }}
                  onPin={() => togglePin(entry)}
                  onMoveUp={() => moveByKeyboard(entry, "up")}
                  onMoveDown={() => moveByKeyboard(entry, "down")}
                  onRoleChange={(nextRole) => changeRole(entry, nextRole)}
                  onDelete={() => remove(entry)}
                  onDragStart={(event) => beginDrag(event, entry)}
                  onDragEnd={() => {
                    draggingIdRef.current = null
                    setDraggingId(null)
                    setDragTargetId(null)
                  }}
                  onDragOver={(event) => dragOver(event, entry)}
                  onDrop={(event) => drop(event, entry)}
                />
              )
            })}
          </div>
        )
      })}

      {data && (
        <div className="mt-auto border-t border-futu-divider px-2 py-1.5 text-[9px] leading-relaxed text-futu-dim">
          自选保存在本机，可添加、分组与排序。
        </div>
      )}
    </aside>
  )
}
