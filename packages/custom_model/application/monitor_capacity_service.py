from __future__ import annotations

from dataclasses import dataclass

from datetime import datetime

from custom_model.domain.models import FetchOrigin, MonitorProfile

@dataclass(frozen=True)
class CapacityBatchObservation:
    batch_id: str
    origin: FetchOrigin
    provider: str
    profile: MonitorProfile
    bucket_close: datetime
    started_at: datetime
    completed_at: datetime
    latency_ms: float
    requested_codes: tuple[str, ...]
    returned_codes: tuple[str, ...]
    missing_codes: tuple[str, ...]
    business_code: int | None
    rate_limited: bool
    provider_called: bool
    cache_hit_count: int
    outcome: str
    error_kind: str | None


@dataclass(frozen=True)
class CapacityItemObservation:
    batch_id: str
    origin: FetchOrigin
    profile: MonitorProfile
    bucket_close: datetime
    instrument_key: str
    symbol: str
    status: str
    cache_hit: bool
    freshness_seconds: float | None


@dataclass(frozen=True)
class CapacityTargetObservation:
    instrument_key: str
    symbol: str
    profile: MonitorProfile


@dataclass(frozen=True)
class CapacityWindowSnapshot:
    batches: tuple[CapacityBatchObservation, ...]
    items: tuple[CapacityItemObservation, ...]
    targets: tuple[CapacityTargetObservation, ...]
    target_change_count: int
    worker_heartbeat_at: datetime | None


