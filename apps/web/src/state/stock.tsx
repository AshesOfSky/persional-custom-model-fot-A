/**
 * 全局选中证券上下文。共享搜索命中时保留完整候选身份，currentCode 始终
 * 使用 instrument.symbol，避免 legacy_code 为裸六位代码时跨交易所串标。
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react"
import type {
  SharedInstrumentId,
  SharedInstrumentSearchItem,
} from "@/api/shared"

export interface StockSelection {
  currentCode: string
  currentName: string
  currentInstrument: SharedInstrumentId | null
  currentSearchItem: SharedInstrumentSearchItem | null
}

export interface SetStock {
  (candidate: SharedInstrumentSearchItem): void
  (code: string, name?: string): void
}

interface StockContextValue extends StockSelection {
  selectedStock: StockSelection
  setStock: SetStock
}

const DEFAULT_SEARCH_ITEM: SharedInstrumentSearchItem = {
  display_name: "平安银行",
  instrument: {
    symbol: "000001.SZ",
    exchange: "SZSE",
    market: "CN",
    asset_type: "stock",
    currency: "CNY",
  },
  legacy_code: "000001",
  match_score: 1,
  source: "frontend-default",
}

const DEFAULT_SELECTION: StockSelection = {
  currentCode: DEFAULT_SEARCH_ITEM.instrument.symbol,
  currentName: DEFAULT_SEARCH_ITEM.display_name,
  currentInstrument: DEFAULT_SEARCH_ITEM.instrument,
  currentSearchItem: DEFAULT_SEARCH_ITEM,
}

const EMPTY_SET_STOCK = (() => {}) as SetStock
const StockContext = createContext<StockContextValue>({
  ...DEFAULT_SELECTION,
  selectedStock: DEFAULT_SELECTION,
  setStock: EMPTY_SET_STOCK,
})

const NAME_CACHE_KEY = "cm_name_cache"
const SELECTION_CACHE_KEY = "cm_last_instrument_selection"

function readNameCache(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(NAME_CACHE_KEY) || "{}")
  } catch {
    return {}
  }
}

function isInstrument(value: unknown): value is SharedInstrumentId {
  if (!value || typeof value !== "object") return false
  const instrument = value as Partial<SharedInstrumentId>
  return (
    typeof instrument.symbol === "string" &&
    typeof instrument.exchange === "string" &&
    typeof instrument.market === "string" &&
    typeof instrument.asset_type === "string" &&
    typeof instrument.currency === "string"
  )
}

function isSearchItem(value: unknown): value is SharedInstrumentSearchItem {
  if (!value || typeof value !== "object") return false
  const item = value as Partial<SharedInstrumentSearchItem>
  return (
    typeof item.display_name === "string" &&
    typeof item.legacy_code === "string" &&
    typeof item.match_score === "number" &&
    typeof item.source === "string" &&
    isInstrument(item.instrument)
  )
}

function selectionFromCandidate(
  candidate: SharedInstrumentSearchItem,
  name = candidate.display_name
): StockSelection {
  return {
    currentCode: candidate.instrument.symbol.trim().toUpperCase(),
    currentName: name,
    currentInstrument: { ...candidate.instrument },
    currentSearchItem: {
      ...candidate,
      display_name: name,
      instrument: { ...candidate.instrument },
    },
  }
}

export function cloneStockSelection(selection: StockSelection): StockSelection {
  return {
    currentCode: selection.currentCode,
    currentName: selection.currentName,
    currentInstrument: selection.currentInstrument
      ? { ...selection.currentInstrument }
      : null,
    currentSearchItem: selection.currentSearchItem
      ? {
          ...selection.currentSearchItem,
          instrument: { ...selection.currentSearchItem.instrument },
        }
      : null,
  }
}

function readInitialSelection(): StockSelection {
  try {
    const raw = localStorage.getItem(SELECTION_CACHE_KEY)
    if (raw) {
      const stored = JSON.parse(raw) as Partial<StockSelection>
      if (isSearchItem(stored.currentSearchItem)) {
        return selectionFromCandidate(
          stored.currentSearchItem,
          typeof stored.currentName === "string"
            ? stored.currentName
            : stored.currentSearchItem.display_name
        )
      }
      if (isInstrument(stored.currentInstrument)) {
        return {
          currentCode: stored.currentInstrument.symbol.trim().toUpperCase(),
          currentName:
            typeof stored.currentName === "string" ? stored.currentName : "",
          currentInstrument: { ...stored.currentInstrument },
          currentSearchItem: null,
        }
      }
    }
  } catch {
    // Fall through to the legacy keys for an existing installation.
  }

  const legacyCode = localStorage.getItem("cm_last_code")?.trim()
  if (!legacyCode) return cloneStockSelection(DEFAULT_SELECTION)
  return {
    currentCode: legacyCode.toUpperCase(),
    currentName: readNameCache()[legacyCode] || "",
    currentInstrument: null,
    currentSearchItem: null,
  }
}

function persistSelection(selection: StockSelection): void {
  localStorage.setItem("cm_last_code", selection.currentCode)
  localStorage.setItem(SELECTION_CACHE_KEY, JSON.stringify(selection))
  if (!selection.currentName) return
  const cache = readNameCache()
  cache[selection.currentCode] = selection.currentName
  localStorage.setItem(NAME_CACHE_KEY, JSON.stringify(cache))
}

function sameCode(left: string, right: string): boolean {
  return left.trim().toUpperCase() === right.trim().toUpperCase()
}

export function StockProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<StockSelection>(readInitialSelection)

  const setStockImplementation = useCallback(
    (stock: string | SharedInstrumentSearchItem, name?: string) => {
      setSelection((current) => {
        let next: StockSelection
        if (typeof stock !== "string") {
          next = selectionFromCandidate(stock)
        } else {
          const requestedCode = stock.trim().toUpperCase()
          const keepsKnownIdentity = Boolean(
            current.currentInstrument &&
              (sameCode(requestedCode, current.currentCode) ||
                sameCode(requestedCode, current.currentInstrument.symbol) ||
                (current.currentSearchItem &&
                  sameCode(requestedCode, current.currentSearchItem.legacy_code)))
          )
          if (keepsKnownIdentity && current.currentInstrument) {
            const currentName = name ?? current.currentName
            next = {
              currentCode: current.currentInstrument.symbol.trim().toUpperCase(),
              currentName,
              currentInstrument: { ...current.currentInstrument },
              currentSearchItem: current.currentSearchItem
                ? {
                    ...current.currentSearchItem,
                    display_name:
                      currentName || current.currentSearchItem.display_name,
                    instrument: { ...current.currentSearchItem.instrument },
                  }
                : null,
            }
          } else {
            next = {
              currentCode: requestedCode,
              currentName: name ?? readNameCache()[requestedCode] ?? "",
              currentInstrument: null,
              currentSearchItem: null,
            }
          }
        }
        persistSelection(next)
        return next
      })
    },
    []
  )
  const setStock = setStockImplementation as SetStock

  const value = useMemo(
    () => ({ ...selection, selectedStock: selection, setStock }),
    [selection, setStock]
  )
  return <StockContext.Provider value={value}>{children}</StockContext.Provider>
}

export function StockSnapshotProvider({
  selection,
  children,
}: {
  selection: StockSelection
  children: ReactNode
}) {
  const frozenSelection = useMemo(
    () => cloneStockSelection(selection),
    [selection]
  )
  const value = useMemo(
    () => ({
      ...frozenSelection,
      selectedStock: frozenSelection,
      setStock: EMPTY_SET_STOCK,
    }),
    [frozenSelection]
  )
  return <StockContext.Provider value={value}>{children}</StockContext.Provider>
}

export function useStock() {
  return useContext(StockContext)
}
