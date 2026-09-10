/**
 * 金融数字格式化工具（A股习惯：红涨绿跌）
 */

/** 涨→富途红，跌→富途绿，平→灰 */
export function trendColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "#8a91a6"
  if (v > 0) return "#f54345"
  if (v < 0) return "#19b36b"
  return "#8a91a6"
}

export function trendClass(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "text-futu-sub"
  if (v > 0) return "text-futu-up"
  if (v < 0) return "text-futu-down"
  return "text-futu-sub"
}

/** 带符号的百分数，如 +2.35% / -1.02% */
export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(digits)}%`
}

/** 价格，保留 2-3 位小数 */
export function fmtPrice(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--"
  if (Math.abs(v) >= 1000) return v.toFixed(2)
  if (Math.abs(v) >= 10) return v.toFixed(2)
  return v.toFixed(3)
}

/** 带符号涨跌额 */
export function fmtChange(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--"
  const sign = v > 0 ? "+" : ""
  return `${sign}${v.toFixed(2)}`
}

/** 大额数字：1.23亿 / 4567万 / 1.2万 */
export function fmtLarge(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--"
  const abs = Math.abs(v)
  if (abs >= 1e12) return `${(v / 1e12).toFixed(2)}万亿`
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)}万`
  return v.toFixed(0)
}

/** 成交量（手→万手/亿手） */
export function fmtVolume(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "--"
  const abs = Math.abs(v)
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)}亿手`
  if (abs >= 1e4) return `${(v / 1e4).toFixed(2)}万手`
  return `${v.toFixed(0)}手`
}

/** 时间显示：ISO → "MM-DD HH:mm" 或 "HH:mm" */
export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "--"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const p = (n: number) => String(n).padStart(2, "0")
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** K线时间 → 显示用字符串 */
export function fmtBarTime(t: string | number): string {
  if (typeof t === "number") {
    const d = new Date(t * 1000)
    const p = (n: number) => String(n).padStart(2, "0")
    return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`
  }
  return t
}
