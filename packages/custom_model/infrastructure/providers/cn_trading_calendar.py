"""Production A-share calendar adapter backed by admitted local snapshots."""

from __future__ import annotations

from pathlib import Path

from custom_model.application.trading_calendar import TradingCalendarService
from custom_model.infrastructure.trading_calendar import (
    VersionedLocalAShareCalendarProvider,
)
from custom_model.infrastructure.trading_calendar_store import TradingCalendarStore


class CNTradingCalendarProvider(VersionedLocalAShareCalendarProvider):
    """SSE/SZSE/BSE provider that exposes only admitted versioned snapshots."""

    name = "cn-admitted-local-trading-calendar"

    def __init__(self, store: TradingCalendarStore) -> None:
        super().__init__(cache=store)


def build_cn_trading_calendar_service(
    root: str | Path,
) -> TradingCalendarService:
    """Build the production service without importing fixtures or mocks."""

    store = TradingCalendarStore(root)
    return TradingCalendarService(CNTradingCalendarProvider(store))


__all__ = ["CNTradingCalendarProvider", "build_cn_trading_calendar_service"]
