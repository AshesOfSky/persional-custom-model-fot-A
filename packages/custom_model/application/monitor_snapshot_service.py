"""Shared, auditable quote batching for monitoring, refresh and resonance work."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from threading import Lock
from time import sleep
from typing import Any, Protocol, TypeVar, runtime_checkable
from uuid import uuid4

from custom_model.application.ports import BatchQuoteDataProvider
from custom_model.domain.models import (
    AssetType,
    FetchOrigin,
    InstrumentId,
    MonitorProfile,
    QuoteBatchQuery,
    QuoteBatchRoute,
    QuoteEnvelope,
    _require_aware,
    utc_now,
)


class MonitorSnapshotInputError(ValueError):
    """The requested fetch cannot be represented by an admitted monitor profile."""


class MonitorSnapshotFetchError(RuntimeError):
    """A provider returned a batch that did not match the requested instruments."""

    def __init__(
        self,
        message: str,
        *,
        returned_codes: tuple[str, ...],
        missing_codes: tuple[str, ...],
        unexpected_codes: tuple[str, ...],
        duplicate_codes: tuple[str, ...],
        missing_instrument_keys: tuple[str, ...],
        request_id: str | None,
    ) -> None:
        super().__init__(message)
        self.returned_codes = returned_codes
        self.missing_codes = missing_codes
        self.unexpected_codes = unexpected_codes
        self.duplicate_codes = duplicate_codes
        self.missing_instrument_keys = missing_instrument_keys
        self.request_id = request_id


class MonitorSnapshotStorageUnavailableError(RuntimeError):
    """Snapshot audit or cache state could not be read or committed safely."""


class FetchOutcome(str, Enum):
    SUCCESS = "success"
    CACHE_HIT = "cache_hit"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"


class FetchItemStatus(str, Enum):
    RETURNED = "returned"
    CACHE_HIT = "cache_hit"
    MISSING = "missing"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class FetchBatchAudit:
    batch_id: str
    origin: FetchOrigin
    provider: str
    route: QuoteBatchRoute
    profile: MonitorProfile
    bucket_close: datetime
    started_at: datetime
    completed_at: datetime
    requested_codes: tuple[str, ...]
    returned_codes: tuple[str, ...]
    missing_codes: tuple[str, ...]
    unexpected_codes: tuple[str, ...]
    duplicate_codes: tuple[str, ...]
    request_id: str | None
    business_code: int | None
    rate_limited: bool
    retry_count: int
    provider_called: bool
    cache_hit_count: int
    outcome: FetchOutcome
    error_kind: str | None
    http_status: int | None = None

    @property
    def latency_ms(self) -> float:
        return max(0.0, (self.completed_at - self.started_at).total_seconds() * 1000)


@dataclass(frozen=True)
class FetchItemAudit:
    batch_id: str
    instrument: InstrumentId
    status: FetchItemStatus
    cache_hit: bool
    quote: QuoteEnvelope | None


@dataclass(frozen=True)
class MonitorSnapshotResult:
    quotes: tuple[QuoteEnvelope, ...]
    cache_hit_count: int
    network_call_count: int
    batch_ids: tuple[str, ...]

    @property
    def by_instrument_key(self) -> dict[str, QuoteEnvelope]:
        return {quote.instrument.key: quote for quote in self.quotes}


@runtime_checkable
class MonitorSnapshotRepository(Protocol):
    def load_cached_quotes(
        self,
        *,
        provider: str,
        route: QuoteBatchRoute,
        profile: MonitorProfile,
        bucket_close: datetime,
        instruments: tuple[InstrumentId, ...],
    ) -> dict[str, QuoteEnvelope]: ...

    def load_manual_refresh_quotes(
        self,
        *,
        provider: str,
        route: QuoteBatchRoute,
        scheduled_bucket_close: datetime,
        interval_seconds: int,
        instruments: tuple[InstrumentId, ...],
    ) -> dict[str, QuoteEnvelope]: ...

    def record_fetch(
        self,
        batch: FetchBatchAudit,
        items: tuple[FetchItemAudit, ...],
    ) -> None: ...

    def try_acquire_rate_lease(
        self,
        governor_key: str,
        *,
        owner_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> datetime | None: ...

    def release_rate_lease(
        self,
        governor_key: str,
        *,
        owner_id: str,
        completed_at: datetime,
        min_interval_seconds: float,
    ) -> None: ...


T = TypeVar("T")


class RateGovernor(Protocol):
    def run(self, operation: Callable[[], T]) -> T: ...


class GlobalRateGovernor:
    """Serialize provider calls locally and through a shared SQLite lease."""

    def __init__(
        self,
        repository: MonitorSnapshotRepository | None = None,
        *,
        governor_key: str = "aicubes-global",
        min_interval_seconds: float = 1.0,
        lease_seconds: float = 60.0,
        clock: Callable[[], datetime] = utc_now,
        sleeper: Callable[[float], None] = sleep,
        owner_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self.repository = repository
        self.governor_key = governor_key
        self.min_interval_seconds = float(min_interval_seconds)
        self.lease_seconds = float(lease_seconds)
        self.clock = clock
        self.sleeper = sleeper
        self.owner_factory = owner_factory
        self._local_lock = Lock()
        self._next_local_at: datetime | None = None

    def run(self, operation: Callable[[], T]) -> T:
        owner_id = self.owner_factory()
        acquired_repository_lease = False
        with self._local_lock:
            self._wait_for_local_slot()
            if self.repository is not None:
                while True:
                    now = _require_aware(self.clock(), "governor clock")
                    unavailable_until = self.repository.try_acquire_rate_lease(
                        self.governor_key,
                        owner_id=owner_id,
                        now=now,
                        lease_seconds=self.lease_seconds,
                    )
                    if unavailable_until is None:
                        acquired_repository_lease = True
                        break
                    wait_seconds = max(
                        0.001, (unavailable_until - now).total_seconds()
                    )
                    self.sleeper(wait_seconds)
            try:
                return operation()
            finally:
                completed_at = _require_aware(self.clock(), "governor clock")
                self._next_local_at = completed_at + timedelta(
                    seconds=self.min_interval_seconds
                )
                if self.repository is not None and acquired_repository_lease:
                    self.repository.release_rate_lease(
                        self.governor_key,
                        owner_id=owner_id,
                        completed_at=completed_at,
                        min_interval_seconds=self.min_interval_seconds,
                    )

    def _wait_for_local_slot(self) -> None:
        while self._next_local_at is not None:
            now = _require_aware(self.clock(), "governor clock")
            if now >= self._next_local_at:
                return
            self.sleeper(max(0.001, (self._next_local_at - now).total_seconds()))


class MonitorSnapshotService:
    """Deduplicate, batch, govern, audit and cache native quote snapshots."""

    def __init__(
        self,
        provider: BatchQuoteDataProvider,
        repository: MonitorSnapshotRepository,
        *,
        governor: RateGovernor | None = None,
        batch_size: int = 30,
        clock: Callable[[], datetime] = utc_now,
        sleeper: Callable[[float], None] = sleep,
        rate_limit_retry_delays: Sequence[float] = (0.25, 0.5, 1.0),
        transient_retry_delays: Sequence[float] = (0.25,),
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        if batch_size < 1 or batch_size > 30:
            raise ValueError("batch_size must be between 1 and 30")
        if any(delay < 0 for delay in rate_limit_retry_delays):
            raise ValueError("rate-limit retry delays cannot be negative")
        if any(delay < 0 for delay in transient_retry_delays):
            raise ValueError("transient retry delays cannot be negative")
        self.provider = provider
        self.repository = repository
        self.governor = governor or GlobalRateGovernor(
            repository,
            clock=clock,
            sleeper=sleeper,
        )
        self.batch_size = batch_size
        self.clock = clock
        self.sleeper = sleeper
        self.rate_limit_retry_delays = tuple(float(item) for item in rate_limit_retry_delays)
        self.transient_retry_delays = tuple(float(item) for item in transient_retry_delays)
        self.id_factory = id_factory
        self._inflight_lock = Lock()
        self._manual_inflight: dict[tuple[str, str], Future[MonitorSnapshotResult]] = {}

    def fetch_quotes(
        self,
        instruments: Sequence[InstrumentId],
        *,
        profile: MonitorProfile,
        bucket_close: datetime,
        origin: FetchOrigin,
    ) -> MonitorSnapshotResult:
        bucket_close = _require_aware(bucket_close, "bucket_close")
        unique = self._unique_instruments(instruments)
        if not unique:
            raise MonitorSnapshotInputError("at least one instrument is required")
        for instrument in unique:
            self._validate_profile(instrument, profile)
        if origin is not FetchOrigin.MANUAL_REFRESH:
            return self._fetch_uncollapsed(unique, profile, bucket_close, origin)
        if len(unique) != 1:
            raise MonitorSnapshotInputError(
                "manual refresh accepts exactly one current instrument"
            )
        key = (self.provider.name, unique[0].key)
        with self._inflight_lock:
            existing = self._manual_inflight.get(key)
            if existing is None:
                future: Future[MonitorSnapshotResult] = Future()
                self._manual_inflight[key] = future
                leader = True
            else:
                future = existing
                leader = False
        if not leader:
            return future.result()
        try:
            result = self._fetch_uncollapsed(unique, profile, bucket_close, origin)
        except BaseException as exc:
            future.set_exception(exc)
            raise
        else:
            future.set_result(result)
            return result
        finally:
            with self._inflight_lock:
                if self._manual_inflight.get(key) is future:
                    del self._manual_inflight[key]

    def _fetch_uncollapsed(
        self,
        instruments: tuple[InstrumentId, ...],
        profile: MonitorProfile,
        bucket_close: datetime,
        origin: FetchOrigin,
    ) -> MonitorSnapshotResult:
        route = QuoteBatchQuery._route_for(instruments[0])
        cached: dict[str, QuoteEnvelope] = {}
        batch_ids: list[str] = []
        if origin is not FetchOrigin.MANUAL_REFRESH:
            cached = self.repository.load_cached_quotes(
                provider=self.provider.name,
                route=route,
                profile=profile,
                bucket_close=bucket_close,
                instruments=instruments,
            )
            if origin is FetchOrigin.SCHEDULED:
                pending_manual_reuse = tuple(
                    instrument
                    for instrument in instruments
                    if instrument.key not in cached
                )
                if pending_manual_reuse:
                    cached.update(
                        self.repository.load_manual_refresh_quotes(
                            provider=self.provider.name,
                            route=route,
                            scheduled_bucket_close=bucket_close,
                            interval_seconds=profile.interval_seconds,
                            instruments=pending_manual_reuse,
                        )
                    )
            if cached:
                batch_ids.append(
                    self._record_cache_reuse(
                        instruments=tuple(
                            instrument
                            for instrument in instruments
                            if instrument.key in cached
                        ),
                        quotes=cached,
                        profile=profile,
                        bucket_close=bucket_close,
                        origin=origin,
                        route=route,
                    )
                )

        pending = tuple(
            instrument for instrument in instruments if instrument.key not in cached
        )
        fetched: dict[str, QuoteEnvelope] = {}
        network_call_count = 0
        for start in range(0, len(pending), self.batch_size):
            chunk = pending[start : start + self.batch_size]
            quotes, calls, ids = self._fetch_network_batch(
                chunk,
                profile=profile,
                bucket_close=bucket_close,
                origin=origin,
                route=route,
            )
            network_call_count += calls
            batch_ids.extend(ids)
            fetched.update({item.instrument.key: item for item in quotes})

        combined = {**cached, **fetched}
        return MonitorSnapshotResult(
            quotes=tuple(combined[instrument.key] for instrument in instruments),
            cache_hit_count=len(cached),
            network_call_count=network_call_count,
            batch_ids=tuple(batch_ids),
        )

    def _fetch_network_batch(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        profile: MonitorProfile,
        bucket_close: datetime,
        origin: FetchOrigin,
        route: QuoteBatchRoute,
    ) -> tuple[tuple[QuoteEnvelope, ...], int, tuple[str, ...]]:
        query = QuoteBatchQuery(
            instruments=instruments,
            requested_at=_require_aware(self.clock(), "clock"),
            origin=origin,
        )
        batch_ids: list[str] = []
        requested_codes = tuple(item.symbol for item in instruments)
        max_retry_count = max(
            len(self.rate_limit_retry_delays),
            len(self.transient_retry_delays),
        )
        for retry_count in range(max_retry_count + 1):
            batch_id = self.id_factory()
            started_at: datetime | None = None

            def provider_call() -> tuple[QuoteEnvelope, ...]:
                nonlocal started_at
                started_at = _require_aware(self.clock(), "clock")
                return self.provider.fetch_quotes(query)

            try:
                quotes = self.governor.run(provider_call)
                returned_codes = tuple(item.instrument.symbol for item in quotes)
                returned: dict[str, QuoteEnvelope] = {}
                duplicate_codes: list[str] = []
                for item in quotes:
                    key = item.instrument.key
                    if key in returned and item.instrument.symbol not in duplicate_codes:
                        duplicate_codes.append(item.instrument.symbol)
                    returned[key] = item
                expected_keys = {instrument.key for instrument in instruments}
                missing_instruments = tuple(
                    instrument
                    for instrument in instruments
                    if instrument.key not in returned
                )
                unexpected_quotes = tuple(
                    item for key, item in returned.items() if key not in expected_keys
                )
                if duplicate_codes or missing_instruments or unexpected_quotes:
                    raise MonitorSnapshotFetchError(
                        "provider batch did not exactly match requested instruments",
                        returned_codes=returned_codes,
                        missing_codes=tuple(
                            instrument.symbol for instrument in missing_instruments
                        ),
                        unexpected_codes=tuple(
                            item.instrument.symbol for item in unexpected_quotes
                        ),
                        duplicate_codes=tuple(duplicate_codes),
                        missing_instrument_keys=tuple(
                            instrument.key for instrument in missing_instruments
                        ),
                        request_id=quotes[0].request_id if quotes else None,
                    )
            except Exception as exc:
                completed_at = _require_aware(self.clock(), "clock")
                started = started_at or completed_at
                business_code = getattr(exc, "business_code", None)
                is_rate_limit = business_code == 4001
                request_id = getattr(exc, "request_id", None)
                missing = self._error_codes(exc, "missing_codes")
                missing_instrument_keys = self._error_codes(
                    exc, "missing_instrument_keys"
                )
                unexpected = self._error_codes(exc, "unexpected_codes")
                duplicate = self._error_codes(exc, "duplicate_codes")
                self.repository.record_fetch(
                    FetchBatchAudit(
                        batch_id=batch_id,
                        origin=origin,
                        provider=self.provider.name,
                        route=route,
                        profile=profile,
                        bucket_close=bucket_close,
                        started_at=started,
                        completed_at=completed_at,
                        requested_codes=requested_codes,
                        returned_codes=self._error_codes(exc, "returned_codes"),
                        missing_codes=missing,
                        unexpected_codes=unexpected,
                        duplicate_codes=duplicate,
                        request_id=request_id if isinstance(request_id, str) else None,
                        business_code=business_code if isinstance(business_code, int) else None,
                        rate_limited=is_rate_limit,
                        retry_count=retry_count,
                        provider_called=started_at is not None,
                        cache_hit_count=0,
                        outcome=(
                            FetchOutcome.RATE_LIMITED
                            if is_rate_limit
                            else FetchOutcome.FAILED
                        ),
                        error_kind=type(exc).__name__,
                        http_status=(
                            getattr(exc, "status", None)
                            if getattr(exc, "error_category", None) == "http"
                            else None
                        ),
                    ),
                    tuple(
                        FetchItemAudit(
                            batch_id=batch_id,
                            instrument=instrument,
                            status=(
                                FetchItemStatus.MISSING
                                if instrument.key in missing_instrument_keys
                                or instrument.symbol in missing
                                else FetchItemStatus.UNAVAILABLE
                            ),
                            cache_hit=False,
                            quote=None,
                        )
                        for instrument in instruments
                    ),
                )
                batch_ids.append(batch_id)
                transient_transport = (
                    started_at is not None and getattr(exc, "retryable", False) is True
                )
                retry_delays = (
                    self.rate_limit_retry_delays
                    if is_rate_limit
                    else self.transient_retry_delays
                    if transient_transport
                    else ()
                )
                if retry_count < len(retry_delays):
                    self.sleeper(retry_delays[retry_count])
                    continue
                raise

            completed_at = _require_aware(self.clock(), "clock")
            started = started_at or completed_at
            ordered = tuple(returned[instrument.key] for instrument in instruments)
            request_id = ordered[0].request_id if ordered else None
            self.repository.record_fetch(
                FetchBatchAudit(
                    batch_id=batch_id,
                    origin=origin,
                    provider=self.provider.name,
                    route=route,
                    profile=profile,
                    bucket_close=bucket_close,
                    started_at=started,
                    completed_at=completed_at,
                    requested_codes=requested_codes,
                    returned_codes=tuple(item.instrument.symbol for item in ordered),
                    missing_codes=(),
                    unexpected_codes=(),
                    duplicate_codes=(),
                    request_id=request_id,
                    business_code=0,
                    rate_limited=False,
                    retry_count=retry_count,
                    provider_called=True,
                    cache_hit_count=0,
                    outcome=FetchOutcome.SUCCESS,
                    error_kind=None,
                ),
                tuple(
                    FetchItemAudit(
                        batch_id=batch_id,
                        instrument=item.instrument,
                        status=FetchItemStatus.RETURNED,
                        cache_hit=False,
                        quote=item,
                    )
                    for item in ordered
                ),
            )
            batch_ids.append(batch_id)
            return ordered, retry_count + 1, tuple(batch_ids)
        raise AssertionError("provider retry loop exhausted without a result")

    def _record_cache_reuse(
        self,
        *,
        instruments: tuple[InstrumentId, ...],
        quotes: dict[str, QuoteEnvelope],
        profile: MonitorProfile,
        bucket_close: datetime,
        origin: FetchOrigin,
        route: QuoteBatchRoute,
    ) -> str:
        timestamp = _require_aware(self.clock(), "clock")
        batch_id = self.id_factory()
        ordered = tuple(quotes[item.key] for item in instruments)
        self.repository.record_fetch(
            FetchBatchAudit(
                batch_id=batch_id,
                origin=origin,
                provider=self.provider.name,
                route=route,
                profile=profile,
                bucket_close=bucket_close,
                started_at=timestamp,
                completed_at=timestamp,
                requested_codes=tuple(item.symbol for item in instruments),
                returned_codes=tuple(item.instrument.symbol for item in ordered),
                missing_codes=(),
                unexpected_codes=(),
                duplicate_codes=(),
                request_id=None,
                business_code=None,
                rate_limited=False,
                retry_count=0,
                provider_called=False,
                cache_hit_count=len(ordered),
                outcome=FetchOutcome.CACHE_HIT,
                error_kind=None,
            ),
            tuple(
                FetchItemAudit(
                    batch_id=batch_id,
                    instrument=item.instrument,
                    status=FetchItemStatus.CACHE_HIT,
                    cache_hit=True,
                    quote=item,
                )
                for item in ordered
            ),
        )
        return batch_id

    @staticmethod
    def _error_codes(exc: Exception, field_name: str) -> tuple[str, ...]:
        value = getattr(exc, field_name, ())
        if not isinstance(value, tuple) or not all(isinstance(item, str) for item in value):
            return ()
        return value

    @staticmethod
    def _unique_instruments(
        instruments: Sequence[InstrumentId],
    ) -> tuple[InstrumentId, ...]:
        unique: dict[str, InstrumentId] = {}
        for instrument in instruments:
            existing = unique.get(instrument.key)
            if existing is not None and existing != instrument:
                raise MonitorSnapshotInputError("instrument identity key collision")
            unique.setdefault(instrument.key, instrument)
        return tuple(unique.values())

    @staticmethod
    def _validate_profile(instrument: InstrumentId, profile: MonitorProfile) -> None:
        if profile is MonitorProfile.STOCK_CORE_60S:
            if instrument.asset_type is not AssetType.STOCK:
                raise MonitorSnapshotInputError("stock profile requires a stock")
            return
        if instrument.asset_type is not AssetType.SECTOR_INDEX:
            raise MonitorSnapshotInputError("sector profile requires a sector_index")

    @staticmethod
    def cohort_for(instrument: InstrumentId) -> int:
        ticker = instrument.symbol.split(".", 1)[0]
        if ticker.isdigit():
            return int(ticker) % 5
        return sum((index + 1) * ord(char) for index, char in enumerate(instrument.key)) % 5

    @classmethod
    def is_due(
        cls,
        instrument: InstrumentId,
        profile: MonitorProfile,
        bucket_close: datetime,
    ) -> bool:
        _require_aware(bucket_close, "bucket_close")
        if profile is not MonitorProfile.SECTOR_STANDARD_300S:
            return True
        return bucket_close.minute % 5 == cls.cohort_for(instrument)


__all__ = [
    "FetchBatchAudit",
    "FetchItemAudit",
    "FetchItemStatus",
    "FetchOutcome",
    "GlobalRateGovernor",
    "MonitorSnapshotFetchError",
    "MonitorSnapshotInputError",
    "MonitorSnapshotRepository",
    "MonitorSnapshotResult",
    "MonitorSnapshotService",
    "MonitorSnapshotStorageUnavailableError",
    "RateGovernor",
]
