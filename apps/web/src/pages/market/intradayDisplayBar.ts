import type { ChartData, Quote } from "../../api/types"

export interface IntradayDisplayChart {
  data: ChartData | null
  provisionalBarTime: string | null
  notice: string | null
}

const shanghaiDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
})

function shanghaiDateKey(value: string): string {
  const parts = Object.fromEntries(
    shanghaiDateFormatter
      .formatToParts(new Date(value))
      .map((part) => [part.type, part.value])
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

export function withIntradayDisplayBar(
  chart: ChartData | null,
  quote: Quote | null
): IntradayDisplayChart {
  if (
    !chart ||
    !quote ||
    chart.timeframe !== "日线" ||
    chart.bars.length === 0 ||
    quote.provenance.isSynthetic ||
    !["open", "midday_break", "after_close"].includes(quote.sessionStatus)
  ) {
    return { data: chart, provisionalBarTime: null, notice: null }
  }

  const lastCompletedTime = chart.bars[chart.bars.length - 1].time
  if (typeof lastCompletedTime !== "string") {
    return { data: chart, provisionalBarTime: null, notice: null }
  }

  const quoteDate = shanghaiDateKey(quote.time)
  const lastCompletedDate = lastCompletedTime.slice(0, 10)
  if (quoteDate <= lastCompletedDate) {
    return { data: chart, provisionalBarTime: null, notice: null }
  }

  const label = quote.sessionStatus === "after_close" ? "当日报价K线" : "盘中临时K线"
  return {
    data: {
      ...chart,
      bars: [
        ...chart.bars,
        {
          time: quoteDate,
          open: quote.open,
          high: quote.high,
          low: quote.low,
          close: quote.price,
          volume: quote.volume,
        },
      ],
    },
    provisionalBarTime: quoteDate,
    notice:
      `${label} ${quoteDate} 来自 ${quote.provenance.provider} ` +
      `(${quote.dataMode}/${quote.quality})；正式指标、确认信号及支撑压力仍截至完成K线 ${lastCompletedDate}。`,
  }
}
