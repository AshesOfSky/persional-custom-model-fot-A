import type { ReactNode } from "react"
import { cn } from "@/lib/utils"

/** 标签-数值小指标（右侧面板、报价网格用） */
export function Stat({
  label,
  value,
  className,
  valueClassName,
}: {
  label: ReactNode
  value: ReactNode
  className?: string
  valueClassName?: string
}) {
  return (
    <div className={cn("flex items-center justify-between py-1", className)}>
      <span className="text-xs text-futu-sub">{label}</span>
      <span className={cn("tnum text-xs text-futu-text", valueClassName)}>
        {value}
      </span>
    </div>
  )
}

/** 大数字指标卡（回测指标网格用） */
export function StatCard({
  label,
  value,
  good,
  className,
}: {
  label: ReactNode
  value: ReactNode
  good?: boolean | null
  className?: string
}) {
  const color =
    good === true
      ? "text-futu-up"
      : good === false
        ? "text-futu-down"
        : "text-futu-text"
  return (
    <div
      className={cn(
        "rounded border border-futu-divider bg-futu-elevated px-3 py-2",
        className
      )}
    >
      <div className="text-[11px] text-futu-sub">{label}</div>
      <div className={cn("tnum mt-0.5 text-base font-semibold", color)}>
        {value}
      </div>
    </div>
  )
}
