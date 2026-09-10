/**
 * 报价头部条：股票名/代码、最新价大字（红涨绿跌）、涨跌额/幅、
 * 今开/昨收/最高/最低/成交量/成交额，数据来自 api.quote。
 */
import type { Quote } from "@/api/types"
import type { QuoteRefreshPhase } from "./useRefreshableQuote"
import { fmtLarge, fmtPct, fmtPrice, fmtTime, fmtVolume, trendClass } from "@/lib/fmt"
import { ChangeText } from "@/components/terminal/PriceText"
import { ErrorState, LoadingState } from "@/components/terminal/States"
import { cn } from "@/lib/utils"
import { LoaderCircle, RefreshCw } from "lucide-react"

function HeaderStat({
  label,
  value,
  className,
}: {
  label: string
  value: string
  className?: string
}) {
  return (
    <div className="min-w-[64px]">
      <div className="text-[10px] text-futu-dim">{label}</div>
      <div className={cn("tnum mt-0.5 text-xs text-futu-text", className)}>
        {value}
      </div>
    </div>
  )
}

export function QuoteHeader({
  quote,
  loading,
  error,
  onRetry,
  fallbackCode,
  fallbackName,
  onRefresh,
  refreshPhase,
  refreshError,
  cooldownRemaining,
  refreshedAt,
}: {
  quote: Quote | null
  loading: boolean
  error: string | null
  onRetry: () => void
  fallbackCode: string
  fallbackName: string
  onRefresh: () => void
  refreshPhase: QuoteRefreshPhase
  refreshError: string | null
  cooldownRemaining: number
  refreshedAt: Date | null
}) {
  const name = quote?.name || fallbackName || fallbackCode
  const code = quote?.code || fallbackCode
  const pct = quote?.changePct ?? null
  const busy = refreshPhase === "queued" || refreshPhase === "loading"
  const refreshLabel =
    refreshPhase === "queued"
      ? "排队中"
      : refreshPhase === "loading"
        ? "拉取中"
        : refreshPhase === "updated"
          ? `已更新 ${refreshedAt?.toLocaleTimeString("zh-CN", {
              hour12: false,
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            })}`
          : refreshPhase === "unchanged"
            ? "上游暂无更新"
            : cooldownRemaining > 0
              ? `${cooldownRemaining}s 后可重试`
              : refreshPhase === "error"
                ? "重试拉取"
                : "拉取最新"
  const refreshButton = (
    <button
      type="button"
      onClick={onRefresh}
      disabled={busy || cooldownRemaining > 0 || (loading && !quote)}
      className={cn(
        "inline-flex h-7 shrink-0 items-center gap-1 rounded-sm border px-2 text-[10px] font-medium transition-colors",
        busy
          ? "cursor-wait border-futu-orange/45 bg-futu-orange/10 text-futu-orange"
          : cooldownRemaining > 0
            ? "cursor-not-allowed border-futu-border bg-futu-elevated text-futu-dim"
            : "border-futu-orange/55 bg-futu-orange/10 text-futu-orange hover:bg-futu-orange/20"
      )}
      title="仅强制拉取当前证券最新原生报价；不刷新分析、基本面、共振池，也不触发预警或外发"
    >
      {busy ? (
        <LoaderCircle className="h-3 w-3 animate-spin" />
      ) : (
        <RefreshCw className="h-3 w-3" />
      )}
      {refreshLabel}
    </button>
  )

  return (
    <div className="shrink-0 rounded border border-futu-border bg-futu-panel px-3 py-2">
      {error && !quote ? (
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <ErrorState error={error} onRetry={onRetry} />
          </div>
          {refreshButton}
        </div>
      ) : loading && !quote ? (
        <LoadingState text="报价加载中…" />
      ) : (
        <div className="flex items-center gap-5 overflow-x-auto">
          {/* 名称 + 代码 */}
          <div className="shrink-0">
            <div className="text-sm font-semibold text-futu-text">{name}</div>
            <div className="tnum mt-0.5 text-[11px] text-futu-dim">{code}</div>
          </div>
          {/* 最新价 + 涨跌 */}
          <div className="flex shrink-0 items-end gap-3">
            <span
              className={cn(
                "tnum text-3xl font-bold leading-none",
                trendClass(pct)
              )}
            >
              {quote ? fmtPrice(quote.price) : "--"}
            </span>
            <div className="flex flex-col gap-0.5 pb-0.5">
              <ChangeText change={quote?.change} pct={pct} />
              <span className={cn("tnum text-[11px]", trendClass(pct))}>
                {fmtPct(pct)}
              </span>
            </div>
          </div>
          <span className="h-8 w-px shrink-0 bg-futu-divider" />
          {/* 明细 */}
          <div className="flex shrink-0 items-center gap-5">
            <HeaderStat
              label="今开"
              value={quote ? fmtPrice(quote.open) : "--"}
              className={quote ? trendClass(quote.open - quote.prevClose) : undefined}
            />
            <HeaderStat
              label="昨收"
              value={quote ? fmtPrice(quote.prevClose) : "--"}
            />
            <HeaderStat
              label="最高"
              value={quote ? fmtPrice(quote.high) : "--"}
              className={quote ? trendClass(quote.high - quote.prevClose) : undefined}
            />
            <HeaderStat
              label="最低"
              value={quote ? fmtPrice(quote.low) : "--"}
              className={quote ? trendClass(quote.low - quote.prevClose) : undefined}
            />
            <HeaderStat
              label="成交量"
              value={quote ? fmtVolume(quote.volume) : "--"}
            />
            <HeaderStat
              label="成交额"
              value={quote ? fmtLarge(quote.amount) : "--"}
            />
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-3 border-l border-futu-divider pl-3">
            {quote?.time && (
              <div className="grid grid-cols-3 gap-x-3 text-right">
                <HeaderStat label="行情时间" value={fmtTime(quote.time)} />
                <HeaderStat
                  label="Provider 时间"
                  value={fmtTime(quote.provenance.providerUpdatedAt)}
                />
                <HeaderStat
                  label="抓取时间"
                  value={fmtTime(quote.provenance.fetchedAt)}
                />
                <div
                  className={cn(
                    "col-span-3 mt-0.5 text-[9px]",
                    quote.quality === "valid" && quote.formalUseEligible
                      ? "text-futu-teal"
                      : "text-futu-gold"
                  )}
                >
                  {quote.provenance.provider} · {quote.dataMode} · {quote.quality}
                </div>
              </div>
            )}
            {refreshButton}
          </div>
        </div>
      )}
      {refreshError && quote && (
        <div className="mt-1 border-t border-futu-divider pt-1 text-right text-[10px] text-futu-gold">
          {refreshError}；旧报价继续显示。
        </div>
      )}
    </div>
  )
}
