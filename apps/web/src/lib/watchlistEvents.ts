export const WATCHLIST_CHANGED_EVENT = "custom-model:watchlist-changed"

export function notifyWatchlistChanged(): void {
  window.dispatchEvent(new Event(WATCHLIST_CHANGED_EVENT))
}
