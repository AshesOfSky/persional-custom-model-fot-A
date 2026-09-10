"""Rate-governed manual refresh for exactly one current market instrument."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime
import math
import re
from threading import Lock
from typing import Protocol

from custom_model.application.monitor_snapshot_service import (
    MonitorSnapshotService,
)
from custom_model.domain.models import (
    AssetType,
    Exchange,
    FetchOrigin,
    InstrumentId,
    ManualRefreshResult,
    Market,
    MonitorProfile,
    QuoteEnvelope,
    _require_aware,
    utc_now,
)


class QuoteRefreshRepository(Protocol):
    def try_acquire_manual_refresh_slot(
        self,
        instrument_key: str,
        *,
        requested_at: datetime,
        cooldown_seconds: int,
    ) -> datetime | None: ...

    def load_latest_quote(
        self,
        *,
        provider: str,
        profile: MonitorProfile,
        instrument: InstrumentId,
    ) -> QuoteEnvelope | None: ...


class QuoteRefreshInputError(ValueError):
    """The refresh target is not an admitted current stock or exact board."""


class QuoteRefreshCooldownError(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("manual quote refresh is cooling down")


class QuoteRefreshRateLimitedError(RuntimeError):
    def __init__(self, retry_after_seconds: int = 1) -> None:
        self.retry_after_seconds = max(1, int(retry_after_seconds))
        super().__init__("manual quote refresh is temporarily rate limited")


class QuoteRefreshUnavailableError(RuntimeError):
    """No trustworthy new native quote could be returned."""


class QuoteRefreshService:
    """Force one provider call while preserving cache, quota and side-effect bounds."""

    _STOCK_EXCHANGES = frozenset({Exchange.SSE, Exchange.SZSE, Exchange.BSE})

    def __init__(
        self,
        snapshots: MonitorSnapshotService,
        repository: QuoteRefreshRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
        cooldown_seconds: int = 10,
    ) -> None:
        if not 1 <= cooldown_seconds <= 3600:
            raise ValueError("refresh cooldown must be between 1 and 3600 seconds")
        self.snapshots = snapshots
        self.repository = repository
        self.clock = clock
        self.cooldown_seconds = cooldown_seconds
        self._inflight_lock = Lock()
        self._inflight: dict[str, Future[ManualRefreshResult]] = {}

    @classmethod
    def profile_for(cls, instrument: InstrumentId) -> MonitorProfile:
        if instrument.market is not Market.CN or instrument.currency != "CNY":
            raise QuoteRefreshInputError("manual refresh requires a CN CNY instrument")
        if (
            instrument.asset_type is AssetType.STOCK
            and instrument.exchange in cls._STOCK_EXCHANGES
        ):
            return MonitorProfile.STOCK_CORE_60S
        if (
            instrument.asset_type is AssetType.SECTOR_INDEX
            and instrument.exchange is Exchange.OTHER
            and re.fullmatch(r"\d{6}\.TI", instrument.symbol) is not None
        ):
            return MonitorProfile.SECTOR_CORE_60S
        raise QuoteRefreshInputError(
            "manual refresh accepts only an A-share stock or exact six-digit .TI sector"
        )

    def refresh(
        self,
        instrument: InstrumentId,
        *,
        now: datetime | None = None,
    ) -> ManualRefreshResult:
        profile = self.profile_for(instrument)
        requested_at = _require_aware(now or self.clock(), "refresh time")
        inflight_key = f"{self.snapshots.provider.name}|{instrument.key}"
        with self._inflight_lock:
            future = self._inflight.get(inflight_key)
            if future is None:
                future = Future()
                self._inflight[inflight_key] = future
                leader = True
            else:
                leader = False
        if not leader:
            return future.result()
        try:
            result = self._refresh_once(
                instrument,
                profile=profile,
                requested_at=requested_at,
            )
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with self._inflight_lock:
                if self._inflight.get(inflight_key) is future:
                    del self._inflight[inflight_key]

    def _refresh_once(
        self,
        instrument: InstrumentId,
        *,
        profile: MonitorProfile,
        requested_at: datetime,
    ) -> ManualRefreshResult:
        try:
            retry_at = self.repository.try_acquire_manual_refresh_slot(
                instrument.key,
                requested_at=requested_at,
                cooldown_seconds=self.cooldown_seconds,
            )
            if retry_at is not None:
                remaining = max(
                    1,
                    math.ceil((retry_at - requested_at).total_seconds()),
                )
                raise QuoteRefreshCooldownError(remaining)
            previous = self.repository.load_latest_quote(
                provider=self.snapshots.provider.name,
                profile=profile,
                instrument=instrument,
            )
            fetched = self.snapshots.fetch_quotes(
                (instrument,),
                profile=profile,
                bucket_close=requested_at,
                origin=FetchOrigin.MANUAL_REFRESH,
            )
        except QuoteRefreshCooldownError:
            raise
        except Exception as exc:
            if getattr(exc, "business_code", None) == 4001:
                raise QuoteRefreshRateLimitedError() from exc
            raise QuoteRefreshUnavailableError(
                "native quote refresh is unavailable"
            ) from exc
        if len(fetched.quotes) != 1 or fetched.quotes[0].instrument != instrument:
            raise QuoteRefreshUnavailableError(
                "native quote refresh returned mismatched evidence"
            )
        current = fetched.quotes[0]
        if previous is not None and current.provider_updated_at < previous.provider_updated_at:
            raise QuoteRefreshUnavailableError(
                "native quote refresh returned an older provider timestamp"
            )
        return ManualRefreshResult(
            quote=current,
            provider_unchanged=(
                previous is not None
                and current.provider_updated_at == previous.provider_updated_at
            ),
            cooldown_seconds=self.cooldown_seconds,
        )


__all__ = [
    "QuoteRefreshCooldownError",
    "QuoteRefreshInputError",
    "QuoteRefreshRateLimitedError",
    "QuoteRefreshRepository",
    "QuoteRefreshService",
    "QuoteRefreshUnavailableError",
]
