"""Canonical runtime store for admitted trading-calendar snapshots."""

from __future__ import annotations

from custom_model.infrastructure.trading_calendar import (
    LocalTradingCalendarSnapshotCache,
)


class TradingCalendarStore(LocalTradingCalendarSnapshotCache):
    """Named production boundary over the immutable local snapshot cache.

    All parsing, point-in-time and complete-coverage checks remain in
    ``LocalTradingCalendarSnapshotCache``.  This type prevents production
    composition from depending on test helpers or an in-memory calendar.
    """


__all__ = ["TradingCalendarStore"]
