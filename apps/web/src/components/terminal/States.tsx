import { AlertCircle, Inbox, RefreshCw } from "lucide-react"
import { Button } from "@/components/ui/button"

export function LoadingState({ text = "加载中…" }: { text?: string }) {
  return (
    <div className="flex h-32 flex-col items-center justify-center gap-2 text-futu-sub">
      <RefreshCw className="h-4 w-4 animate-spin" />
      <span className="text-xs">{text}</span>
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: string
  onRetry?: () => void
}) {
  return (
    <div className="flex h-32 flex-col items-center justify-center gap-2 text-futu-sub">
      <AlertCircle className="h-4 w-4 text-futu-down" />
      <span className="max-w-md text-center text-xs">{error}</span>
      {onRetry && (
        <Button
          size="sm"
          variant="outline"
          className="mt-1 h-7 border-futu-border text-xs"
          onClick={onRetry}
        >
          重试
        </Button>
      )}
    </div>
  )
}

export function EmptyState({ text = "暂无数据" }: { text?: string }) {
  return (
    <div className="flex h-32 flex-col items-center justify-center gap-2 text-futu-dim">
      <Inbox className="h-4 w-4" />
      <span className="text-xs">{text}</span>
    </div>
  )
}
