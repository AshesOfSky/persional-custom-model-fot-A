"""Offline adapter and explicit in-process cache for A-share calendars."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
import json
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from custom_model.application.trading_calendar import TradingCalendarUnavailableError
from custom_model.domain.models import Exchange, Market
from custom_model.domain.trading_calendar import (
    TradingCalendarSnapshot,
    TradingSessionName,
    TradingSessionTemplate,
)


class LocalTradingCalendarSnapshotCache:
    """Load immutable JSON snapshots once, with explicit atomic reload.

    The cache intentionally does not watch files or refresh itself.  A running
    decision cycle therefore cannot silently switch calendar revisions midway.
    Operators can call :meth:`reload` between cycles after placing a newly
    admitted snapshot in the directory.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        reader: Callable[[Path], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self._reader = reader or (lambda path: path.read_text(encoding="utf-8"))
        self._lock = RLock()
        self._snapshots: tuple[TradingCalendarSnapshot, ...] | None = None

    def snapshots(self) -> tuple[TradingCalendarSnapshot, ...]:
        with self._lock:
            if self._snapshots is None:
                return self.reload()
            return self._snapshots

    def reload(self) -> tuple[TradingCalendarSnapshot, ...]:
        with self._lock:
            try:
                paths = tuple(
                    sorted(
                        (path for path in self.root.glob("*.json") if path.is_file()),
                        key=lambda path: path.name,
                    )
                )
            except OSError as exc:
                raise TradingCalendarUnavailableError(
                    f"cannot enumerate local calendar snapshots in {self.root}"
                ) from exc
            if not paths:
                raise TradingCalendarUnavailableError(
                    f"no local calendar snapshots found in {self.root}"
                )

            loaded: list[TradingCalendarSnapshot] = []
            for path in paths:
                loaded.append(self._load_one(path))
            ids = [snapshot.snapshot_id for snapshot in loaded]
            if len(ids) != len(set(ids)):
                raise TradingCalendarUnavailableError(
                    "duplicate calendar snapshot_id values are not admissible"
                )
            result = tuple(loaded)
            self._snapshots = result
            return result

    def _load_one(self, path: Path) -> TradingCalendarSnapshot:
        try:
            raw = json.loads(self._reader(path))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise TradingCalendarUnavailableError(
                f"calendar snapshot {path.name} is not valid UTF-8 JSON: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise TradingCalendarUnavailableError(
                f"calendar snapshot {path.name} must contain one JSON object"
            )
        try:
            return TradingCalendarSnapshot.model_validate(raw)
        except ValidationError as exc:
            raise TradingCalendarUnavailableError(
                f"calendar snapshot {path.name} failed schema validation: {exc}"
            ) from exc


class VersionedLocalAShareCalendarProvider:
    """A fail-closed adapter for admitted SSE/SZSE/BSE calendar snapshots."""

    name = "versioned-local-a-share-calendar"
    _EXCHANGES = frozenset({Exchange.SSE, Exchange.SZSE, Exchange.BSE})
    _TIMEZONE = "Asia/Shanghai"
    _SESSIONS = (
        TradingSessionTemplate(
            name=TradingSessionName.MORNING,
            opens_at=time(9, 30),
            closes_at=time(11, 30),
        ),
        TradingSessionTemplate(
            name=TradingSessionName.AFTERNOON,
            opens_at=time(13, 0),
            closes_at=time(15, 0),
        ),
    )

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        cache: LocalTradingCalendarSnapshotCache | None = None,
    ) -> None:
        if cache is None and root is None:
            raise ValueError("root or cache is required")
        if cache is not None and root is not None:
            raise ValueError("pass root or cache, not both")
        if cache is None:
            assert root is not None
            cache = LocalTradingCalendarSnapshotCache(root)
        self._cache = cache

    @property
    def cache(self) -> LocalTradingCalendarSnapshotCache:
        return self._cache

    def timezone_for(self, exchange: Exchange) -> str:
        if exchange not in self._EXCHANGES:
            raise TradingCalendarUnavailableError(
                f"exchange {exchange.value} is outside the A-share calendar adapter"
            )
        return self._TIMEZONE

    def snapshot_for(
        self,
        exchange: Exchange,
        target_date: date,
        *,
        as_of: datetime,
        snapshot_id: str | None = None,
    ) -> TradingCalendarSnapshot:
        self.timezone_for(exchange)
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        snapshots = self._cache.snapshots()
        for snapshot in snapshots:
            self._validate_a_share_snapshot(snapshot)

        if snapshot_id is not None:
            pinned = [item for item in snapshots if item.snapshot_id == snapshot_id]
            if not pinned:
                raise TradingCalendarUnavailableError(
                    f"calendar snapshot_id {snapshot_id!r} is unavailable"
                )
            candidate = pinned[0]
            self._validate_candidate(candidate, exchange, target_date, as_of)
            return candidate

        exchange_matches = [item for item in snapshots if exchange in item.exchanges]
        if not exchange_matches:
            raise TradingCalendarUnavailableError(
                f"no local calendar snapshot covers exchange {exchange.value}"
            )
        coverage_matches = [
            item
            for item in exchange_matches
            if item.coverage_start <= target_date <= item.coverage_end
        ]
        if not coverage_matches:
            raise TradingCalendarUnavailableError(
                f"target date {target_date.isoformat()} is outside local calendar coverage"
            )
        point_in_time = [
            item
            for item in coverage_matches
            if item.published_at <= as_of and item.captured_at <= as_of
        ]
        if not point_in_time:
            raise TradingCalendarUnavailableError(
                "no point-in-time calendar snapshot was known at as_of"
            )
        point_in_time.sort(
            key=lambda item: (item.captured_at, item.published_at, item.snapshot_id)
        )
        return point_in_time[-1]

    @classmethod
    def _validate_a_share_snapshot(cls, snapshot: TradingCalendarSnapshot) -> None:
        if snapshot.market is not Market.CN:
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} is not a CN calendar"
            )
        if not set(snapshot.exchanges).issubset(cls._EXCHANGES):
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} contains a non-A-share exchange"
            )
        if snapshot.timezone != cls._TIMEZONE:
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} has a non-A-share timezone"
            )
        if snapshot.sessions != cls._SESSIONS:
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} has an unsupported A-share session template"
            )
        if not snapshot.coverage_complete or not snapshot.point_in_time_reproducible:
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} is not admissible: complete and "
                "point-in-time coverage are required"
            )
        if any(day.weekday() >= 5 for day in snapshot.trading_days):
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} marks a weekend as an A-share trading day"
            )

    @staticmethod
    def _validate_candidate(
        snapshot: TradingCalendarSnapshot,
        exchange: Exchange,
        target_date: date,
        as_of: datetime,
    ) -> None:
        if exchange not in snapshot.exchanges:
            raise TradingCalendarUnavailableError(
                f"snapshot {snapshot.snapshot_id} does not cover exchange {exchange.value}"
            )
        if not snapshot.coverage_start <= target_date <= snapshot.coverage_end:
            raise TradingCalendarUnavailableError(
                f"target date {target_date.isoformat()} is outside pinned snapshot coverage"
            )
        if snapshot.published_at > as_of or snapshot.captured_at > as_of:
            raise TradingCalendarUnavailableError(
                f"pinned snapshot {snapshot.snapshot_id} violates point-in-time as_of"
            )


__all__ = [
    "LocalTradingCalendarSnapshotCache",
    "VersionedLocalAShareCalendarProvider",
]
