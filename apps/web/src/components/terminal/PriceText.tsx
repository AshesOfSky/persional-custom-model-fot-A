import { cn } from "@/lib/utils"
import { fmtChange, fmtPct, fmtPrice, trendClass } from "@/lib/fmt"

/** 涨跌着色文本：红涨绿跌 */
export function PriceText({
  value,
  pct,
  size = "md",
  className,
}: {
  value: number | null | undefined
  pct?: number | null
  size?: "sm" | "md" | "lg" | "xl"
  className?: string
}) {
  const cls = trendClass(pct ?? value)
  const sizeCls =
    size === "xl"
      ? "text-2xl font-bold"
      : size === "lg"
        ? "text-lg font-semibold"
        : size === "sm"
          ? "text-xs"
          : "text-sm font-medium"
  return (
    <span className={cn("tnum", sizeCls, cls, className)}>
      {fmtPrice(value)}
      {pct !== undefined && pct !== null && (
        <span className="ml-1 text-[0.85em]">{fmtPct(pct)}</span>
      )}
    </span>
  )
}

/** 涨跌额 + 百分比组合（如 +0.12 / +1.18%） */
export function ChangeText({
  change,
  pct,
  className,
}: {
  change: number | null | undefined
  pct: number | null | undefined
  className?: string
}) {
  return (
    <span className={cn("tnum text-xs", trendClass(pct ?? change), className)}>
      {fmtChange(change)} {fmtPct(pct)}
    </span>
  )
}
