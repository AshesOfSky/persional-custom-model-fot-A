import type { ChanState } from "@/api/types"
import {
  buildChanStateKey,
  type SharedCoreTimeframe,
  type SharedInstrumentId,
} from "@/api/shared"

const STORAGE_KEY = "cm_chan_state_v1"

interface ChanStateCacheRecord {
  stateKey: string
  state: ChanState
}

let memoryRecord: ChanStateCacheRecord | null = null

function isStateForKey(value: unknown, expectedKey: string): value is ChanState {
  if (!value || typeof value !== "object") return false
  const state = value as Partial<ChanState>
  return (
    state.version === "chan-state-1" &&
    state.methodVersion === "chan-local-3" &&
    state.reliabilityVersion === "chan-reliability-1" &&
    state.stateKey === expectedKey &&
    state.persistenceEligible === true &&
    typeof state.contentHash === "string" &&
    /^chan-state-[0-9a-f]{20}$/.test(state.contentHash) &&
    Boolean(state.confirmedStreams) &&
    Array.isArray(state.confirmedStreams?.stableBi) &&
    Array.isArray(state.confirmedStreams?.segment) &&
    Array.isArray(state.confirmedStreams?.sameLevel)
  )
}

function readStoredRecord(): ChanStateCacheRecord | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<ChanStateCacheRecord>
    if (
      typeof parsed.stateKey !== "string" ||
      !isStateForKey(parsed.state, parsed.stateKey)
    ) {
      return null
    }
    return { stateKey: parsed.stateKey, state: parsed.state }
  } catch {
    return null
  }
}

export function priorChanStateFor(
  instrument: SharedInstrumentId,
  timeframe: SharedCoreTimeframe
): ChanState | null {
  const expectedKey = buildChanStateKey(instrument, timeframe)
  const record = memoryRecord ?? readStoredRecord()
  if (!record || record.stateKey !== expectedKey) return null
  if (!isStateForKey(record.state, expectedKey)) return null
  memoryRecord = record
  return record.state
}

export function rememberChanState(
  state: ChanState,
  instrument: SharedInstrumentId,
  timeframe: SharedCoreTimeframe
): void {
  const expectedKey = buildChanStateKey(instrument, timeframe)
  if (!isStateForKey(state, expectedKey)) {
    throw new Error("refusing to cache Chan state for another instrument/timeframe")
  }
  const record = { stateKey: expectedKey, state }
  memoryRecord = record
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(record))
  } catch {
    // The verified in-memory state remains available for this app session.
  }
}

export function clearChanStateCache(): void {
  memoryRecord = null
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing else owns this optional client cache.
  }
}
