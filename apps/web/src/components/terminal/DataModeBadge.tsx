import type { DataMode } from "@/api/types"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

/** 数据模式徽章：同时兼容共享核心与旧 Web API 的来源状态。 */
const CONF: Record<DataMode, { label: string; className: string }> = {
  live: {
    label: "实时",
    className: "border-futu-up/50 bg-futu-up/10 text-futu-up",
  },
  cache: {
    label: "缓存",
    className: "border-futu-gold/50 bg-futu-gold/10 text-futu-gold",
  },
  delayed: {
    label: "延迟",
    className: "border-futu-teal/50 bg-futu-teal/10 text-futu-teal",
  },
  fallback: {
    label: "降级",
    className: "border-futu-orange/50 bg-futu-orange/10 text-futu-orange",
  },
  demo: {
    label: "演示",
    className: "border-futu-purple/50 bg-futu-purple/10 text-futu-purple",
  },
  simulation: {
    label: "模拟",
    className: "border-futu-blue/50 bg-futu-blue/10 text-futu-blue",
  },
}

export function DataModeBadge({
  mode,
  className,
}: {
  mode: DataMode | null | undefined
  className?: string
}) {
  if (!mode) return null
  const c = CONF[mode]
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-sm px-1.5 py-0 text-[10px] font-normal leading-4",
        c.className,
        className
      )}
    >
      {c.label}
    </Badge>
  )
}
