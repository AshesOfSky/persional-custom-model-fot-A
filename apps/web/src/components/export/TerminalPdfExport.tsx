import { useRef, useState, type ComponentType } from "react"
import { createPortal } from "react-dom"
import { Check, FileDown, LoaderCircle } from "lucide-react"
import {
  cloneStockSelection,
  type StockSelection,
  useStock,
} from "@/state/stock"
import { cn } from "@/lib/utils"

type ExportStatus = "idle" | "preparing" | "capturing" | "done"
type ExportDocument = ComponentType<{
  onReady: () => void
  selection: StockSelection
}>

function nextFrame(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => resolve()))
}

export function TerminalPdfExport() {
  const { currentCode, selectedStock } = useStock()
  const [status, setStatus] = useState<ExportStatus>("idle")
  const [error, setError] = useState<string | null>(null)
  const [DocumentComponent, setDocumentComponent] =
    useState<ExportDocument | null>(null)
  const [exportSelection, setExportSelection] =
    useState<StockSelection | null>(null)
  const exportRoot = useRef<HTMLDivElement>(null)
  const readyResolver = useRef<(() => void) | null>(null)
  const busy = status === "preparing" || status === "capturing"
  const isSector = currentCode.trim().toUpperCase().endsWith(".TI")

  const exportPdf = async () => {
    if (busy) return
    const frozenSelection = cloneStockSelection(selectedStock)
    setError(null)
    setStatus("preparing")
    setExportSelection(frozenSelection)
    let completed = false

    try {
      const [documentModule, canvasModule, pdfModule] = await Promise.all([
        import("@/components/export/TerminalPdfDocument"),
        import("html2canvas"),
        import("jspdf"),
      ])
      const ready = new Promise<void>((resolve) => {
        readyResolver.current = resolve
      })
      setDocumentComponent(() => documentModule.default)
      await ready
      await document.fonts.ready
      await nextFrame()
      await nextFrame()

      const root = exportRoot.current
      if (!root) throw new Error("PDF 导出画布未完成挂载")
      const sections = Array.from(
        root.querySelectorAll<HTMLElement>("[data-pdf-section]")
      )
      if (sections.length === 0) throw new Error("没有可导出的行情或技术页面")

      setStatus("capturing")
      const html2canvas = canvasModule.default
      const { jsPDF } = pdfModule
      const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" })
      const pageWidth = pdf.internal.pageSize.getWidth()
      const pageHeight = pdf.internal.pageSize.getHeight()
      const margin = 8
      const contentWidth = pageWidth - margin * 2
      const contentHeight = pageHeight - margin * 2
      let hasPage = false

      for (const section of sections) {
        const canvas = await html2canvas(section, {
          backgroundColor: "#10141b",
          logging: false,
          scale: 1.25,
          useCORS: true,
          width: section.scrollWidth,
          height: section.scrollHeight,
          windowWidth: Math.max(1600, section.scrollWidth),
          windowHeight: Math.max(900, section.scrollHeight),
        })
        const pixelsPerMm = canvas.width / contentWidth
        const maxSliceHeight = Math.max(
          1,
          Math.floor(contentHeight * pixelsPerMm)
        )

        for (let offset = 0; offset < canvas.height; offset += maxSliceHeight) {
          const sliceHeight = Math.min(maxSliceHeight, canvas.height - offset)
          const slice = document.createElement("canvas")
          slice.width = canvas.width
          slice.height = sliceHeight
          const context = slice.getContext("2d")
          if (!context) throw new Error("浏览器无法创建 PDF 图像画布")
          context.drawImage(
            canvas,
            0,
            offset,
            canvas.width,
            sliceHeight,
            0,
            0,
            canvas.width,
            sliceHeight
          )
          if (hasPage) pdf.addPage("a4", "landscape")
          pdf.setFillColor(16, 20, 27)
          pdf.rect(0, 0, pageWidth, pageHeight, "F")
          const renderedHeight = sliceHeight / pixelsPerMm
          pdf.addImage(
            slice.toDataURL("image/jpeg", 0.92),
            "JPEG",
            margin,
            margin,
            contentWidth,
            renderedHeight,
            undefined,
            "FAST"
          )
          hasPage = true
        }
      }

      const name = frozenSelection.currentName || frozenSelection.currentCode
      pdf.save(
        `${name}_${frozenSelection.currentCode}_${
          frozenSelection.currentCode.trim().toUpperCase().endsWith(".TI")
            ? "行情技术板块共振报告"
            : "行情技术报告"
        }.pdf`
      )
      completed = true
      setStatus("done")
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "PDF 导出失败")
      setStatus("idle")
    } finally {
      readyResolver.current = null
      setDocumentComponent(null)
      setExportSelection(null)
      if (completed) {
        window.setTimeout(() => setStatus("idle"), 2200)
      }
    }
  }

  return (
    <div className="relative">
      <button
        type="button"
        disabled={busy}
        onClick={() => void exportPdf()}
        className={cn(
          "flex h-7 items-center gap-1.5 rounded border px-2 text-[10px] font-medium transition-colors",
          status === "done"
            ? "border-futu-teal/40 bg-futu-teal/10 text-futu-teal"
            : "border-futu-border bg-futu-elevated text-futu-sub hover:border-futu-orange/60 hover:text-futu-orange",
          busy && "cursor-wait opacity-70"
        )}
        title={
          isSector
            ? "导出行情、全部技术二级页面与板块共振分类为一个 PDF"
            : "导出行情页与全部技术二级页面为一个 PDF"
        }
      >
        {busy ? (
          <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
        ) : status === "done" ? (
          <Check className="h-3.5 w-3.5" />
        ) : (
          <FileDown className="h-3.5 w-3.5" />
        )}
        <span>
          {status === "preparing"
            ? "准备报告"
            : status === "capturing"
              ? "生成 PDF"
              : status === "done"
                ? "已导出"
                : "导出 PDF"}
        </span>
      </button>

      {error && (
        <div
          role="alert"
          className="absolute right-0 top-9 z-50 w-72 rounded border border-futu-down/40 bg-futu-elevated px-2.5 py-2 text-[10px] leading-relaxed text-futu-down shadow-xl"
        >
          PDF 导出失败：{error}
        </div>
      )}

      {DocumentComponent && exportSelection &&
        createPortal(
          <div
            aria-hidden="true"
            style={{
              position: "absolute",
              left: "-20000px",
              top: 0,
              width: "1600px",
              pointerEvents: "none",
              background: "#10141b",
            }}
          >
            <div ref={exportRoot}>
              <DocumentComponent
                selection={exportSelection}
                onReady={() => {
                  readyResolver.current?.()
                  readyResolver.current = null
                }}
              />
            </div>
          </div>,
          document.body
        )}
    </div>
  )
}
