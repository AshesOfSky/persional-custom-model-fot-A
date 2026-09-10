"""Canonical, provider-neutral watchlist domain models.

Watchlist entries are configuration only.  They never contain a quote, imply
that monitoring is running, or create an alert rule as a side effect.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import Field, field_validator, model_validator

from custom_model.domain.models import (
    AssetType,
    Exchange,
    FrozenModel,
    InstrumentId,
    Market,
    _require_aware,
)


_CN_CASH_EXCHANGES = frozenset({Exchange.SSE, Exchange.SZSE, Exchange.BSE})
_CN_INDEX_EXCHANGES = frozenset(
    {Exchange.SSE, Exchange.SZSE, Exchange.BSE, Exchange.OTHER}
)


class WatchlistRole(str, Enum):
    CORE = "core"
    WATCHLIST = "watchlist"
    ETF = "etf"
    INDEX = "index"


def validate_watchlist_assignment(
    instrument: InstrumentId,
    role: WatchlistRole,
) -> None:
    """Admit only the deliberately narrow mainland-China V1 profile."""

    if instrument.market is not Market.CN or instrument.currency != "CNY":
        raise ValueError("Watchlist V1 only admits CNY instruments in market CN")
    if role in {WatchlistRole.CORE, WatchlistRole.WATCHLIST}:
        if (
            instrument.asset_type is not AssetType.STOCK
            or instrument.exchange not in _CN_CASH_EXCHANGES
        ):
            raise ValueError(
                "core/watchlist roles require a CNY A-share stock on SSE/SZSE/BSE"
            )
        return
    if role is WatchlistRole.ETF:
        if (
            instrument.asset_type is not AssetType.ETF
            or instrument.exchange not in _CN_CASH_EXCHANGES
        ):
            raise ValueError("ETF role requires an explicit mainland ETF instrument")
        return
    if role is WatchlistRole.INDEX:
        if (
            instrument.asset_type not in {AssetType.INDEX, AssetType.SECTOR_INDEX}
            or instrument.exchange not in _CN_INDEX_EXCHANGES
        ):
            raise ValueError(
                "index role requires an explicit mainland index or sector_index instrument"
            )
        return
    raise ValueError("unsupported watchlist role")


class WatchlistEntry(FrozenModel):
    """One canonical instrument and its single active organizational role."""

    entry_id: str = Field(min_length=1, max_length=64)
    instrument: InstrumentId
    role: WatchlistRole
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=256)
    pinned: bool = False
    sort_order: int = Field(default=0, ge=0, le=10_000)

    @field_validator("entry_id", "display_name", "note")
    @classmethod
    def normalized_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if info.field_name != "note" and not normalized:
            raise ValueError(f"{info.field_name} cannot be blank")
        return normalized

    @model_validator(mode="after")
    def admitted_assignment(self) -> "WatchlistEntry":
        validate_watchlist_assignment(self.instrument, self.role)
        return self


class WatchlistEntryRecord(FrozenModel):
    """Persisted entry plus immutable optimistic-lock and audit metadata."""

    entry: WatchlistEntry
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    deleted_at: datetime | None = None
    spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("created_at", "updated_at", "deleted_at")
    @classmethod
    def aware_times(cls, value: datetime | None, info: Any) -> datetime | None:
        return None if value is None else _require_aware(value, info.field_name)

    @model_validator(mode="after")
    def coherent_metadata(self) -> "WatchlistEntryRecord":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.deleted_at is not None:
            if self.deleted_at < self.created_at:
                raise ValueError("deleted_at cannot precede created_at")
            if self.updated_at != self.deleted_at:
                raise ValueError("a tombstone must be updated at its deletion time")
        return self


__all__ = [
    "WatchlistEntry",
    "WatchlistEntryRecord",
    "WatchlistRole",
    "validate_watchlist_assignment",
]
