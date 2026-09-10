"""SQLite WAL audit, cache, cooldown and cross-process rate state for monitoring."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from custom_model.application.monitor_snapshot_service import (
    FetchBatchAudit,
    FetchItemAudit,
    FetchItemStatus,
    MonitorSnapshotStorageUnavailableError,
)
from custom_model.application.monitor_capacity_service import (
    CapacityBatchObservation,
    CapacityItemObservation,
    CapacityTargetObservation,
    CapacityWindowSnapshot,
)
from custom_model.domain.models import (
    FetchOrigin,
    InstrumentId,
    MonitorProfile,
    QuoteBatchQuery,
    QuoteBatchRoute,
    QuoteEnvelope,
)
from custom_model.infrastructure.runtime_paths import resolve_runtime_state_path


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database times must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MonitorSnapshotStorageUnavailableError(
            "monitor snapshot database contains a naive timestamp"
        )
    return parsed


_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _target_audit_window(
    started_at: datetime,
    ended_at: datetime,
) -> tuple[datetime, datetime] | None:
    local_date = started_at.astimezone(_SHANGHAI).date()
    session_started_at = datetime.combine(
        local_date,
        time(9, 31),
        tzinfo=_SHANGHAI,
    )
    session_ended_at = datetime.combine(
        local_date,
        time(15, 1),
        tzinfo=_SHANGHAI,
    )
    selected_start = max(started_at, session_started_at)
    selected_end = min(ended_at, session_ended_at)
    return (
        (selected_start, selected_end)
        if selected_start < selected_end
        else None
    )


def _json_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def _parse_required(value: str, label: str) -> datetime:
    parsed = _parse(value)
    if parsed is None:
        raise MonitorSnapshotStorageUnavailableError(
            f"monitor capacity {label} is missing"
        )
    return parsed


def _parse_codes(value: str, label: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MonitorSnapshotStorageUnavailableError(
            f"monitor capacity {label} is invalid"
        ) from exc
    if not isinstance(parsed, list) or not all(
        isinstance(item, str) and item.strip() for item in parsed
    ):
        raise MonitorSnapshotStorageUnavailableError(
            f"monitor capacity {label} is invalid"
        )
    return tuple(item.strip().upper() for item in parsed)


class SQLiteMonitorSnapshotStore:
    """Persist coordinator evidence in the authoritative Monitor Worker database."""

    def __init__(self, path: str | Path) -> None:
        self.path = resolve_runtime_state_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        try:
            with self.connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS monitor_schema_migrations (
                        version TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS monitor_fetch_batches (
                        batch_id TEXT PRIMARY KEY,
                        origin TEXT NOT NULL CHECK(origin IN (
                            'scheduled', 'manual_refresh', 'resonance_scan')),
                        provider TEXT NOT NULL,
                        provider_route TEXT NOT NULL CHECK(provider_route IN (
                            'equity_snapshot', 'index_snapshot')),
                        profile TEXT NOT NULL CHECK(profile IN (
                            'stock_core_60s', 'sector_core_60s',
                            'sector_standard_300s')),
                        bucket_close TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        latency_ms REAL NOT NULL CHECK(latency_ms >= 0),
                        requested_count INTEGER NOT NULL CHECK(requested_count >= 0),
                        returned_count INTEGER NOT NULL CHECK(returned_count >= 0),
                        requested_codes_json TEXT NOT NULL,
                        returned_codes_json TEXT NOT NULL,
                        missing_codes_json TEXT NOT NULL,
                        unexpected_codes_json TEXT NOT NULL,
                        duplicate_codes_json TEXT NOT NULL,
                        request_id TEXT,
                        business_code INTEGER,
                        rate_limited INTEGER NOT NULL CHECK(rate_limited IN (0, 1)),
                        retry_count INTEGER NOT NULL CHECK(retry_count >= 0),
                        provider_called INTEGER NOT NULL CHECK(provider_called IN (0, 1)),
                        cache_hit_count INTEGER NOT NULL CHECK(cache_hit_count >= 0),
                        outcome TEXT NOT NULL CHECK(outcome IN (
                            'success', 'cache_hit', 'rate_limited', 'failed')),
                        error_kind TEXT,
                        http_status INTEGER CHECK(http_status BETWEEN 100 AND 599)
                    );
                    CREATE INDEX IF NOT EXISTS idx_monitor_fetch_batches_time
                        ON monitor_fetch_batches(completed_at, batch_id);
                    CREATE INDEX IF NOT EXISTS idx_monitor_fetch_batches_origin
                        ON monitor_fetch_batches(origin, provider_called, completed_at);
                    CREATE TABLE IF NOT EXISTS monitor_fetch_items (
                        batch_id TEXT NOT NULL,
                        instrument_key TEXT NOT NULL,
                        instrument_json TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN (
                            'returned', 'cache_hit', 'missing', 'unavailable')),
                        cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0, 1)),
                        quote_json TEXT,
                        provider_time TEXT,
                        fetched_at TEXT,
                        freshness_seconds REAL,
                        PRIMARY KEY(batch_id, instrument_key),
                        FOREIGN KEY(batch_id) REFERENCES monitor_fetch_batches(batch_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_monitor_fetch_items_instrument
                        ON monitor_fetch_items(instrument_key, fetched_at);
                    CREATE TABLE IF NOT EXISTS monitor_latest_quotes (
                        provider TEXT NOT NULL,
                        provider_route TEXT NOT NULL,
                        instrument_key TEXT NOT NULL,
                        profile TEXT NOT NULL,
                        bucket_close TEXT NOT NULL,
                        quote_json TEXT NOT NULL,
                        provider_time TEXT NOT NULL,
                        fetched_at TEXT NOT NULL,
                        freshness_seconds REAL NOT NULL CHECK(freshness_seconds >= 0),
                        batch_id TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(provider, provider_route, instrument_key, profile)
                    );
                    CREATE INDEX IF NOT EXISTS idx_monitor_latest_bucket
                        ON monitor_latest_quotes(provider, provider_route, profile,
                                                 bucket_close, instrument_key);
                    CREATE TABLE IF NOT EXISTS monitor_manual_refresh_cooldown (
                        instrument_key TEXT PRIMARY KEY,
                        last_requested_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS monitor_rate_governor (
                        governor_key TEXT PRIMARY KEY,
                        owner_id TEXT,
                        lease_expires_at TEXT,
                        next_allowed_at TEXT,
                        updated_at TEXT NOT NULL
                    );
                    INSERT OR IGNORE INTO monitor_schema_migrations(version, applied_at)
                        VALUES ('004_monitor_snapshots',
                                strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                    """
                )
                # Existing API/Worker databases predate the HTTP diagnostic.
                # Serialize the check and additive upgrade across both processes.
                connection.execute("BEGIN IMMEDIATE")
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(monitor_fetch_batches)")
                }
                if "http_status" not in columns:
                    connection.execute(
                        "ALTER TABLE monitor_fetch_batches ADD COLUMN "
                        "http_status INTEGER CHECK(http_status BETWEEN 100 AND 599)"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO monitor_schema_migrations(version, applied_at) "
                    "VALUES ('005_monitor_http_status', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
                )
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor snapshot schema is unavailable"
            ) from exc

    def record_fetch(
        self,
        batch: FetchBatchAudit,
        items: tuple[FetchItemAudit, ...],
    ) -> None:
        if any(item.batch_id != batch.batch_id for item in items):
            raise MonitorSnapshotStorageUnavailableError(
                "fetch item batch identity does not match its audit"
            )
        if batch.completed_at < batch.started_at:
            raise MonitorSnapshotStorageUnavailableError(
                "fetch completion cannot precede request start"
            )
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO monitor_fetch_batches(
                           batch_id, origin, provider, provider_route, profile,
                           bucket_close, started_at, completed_at, latency_ms,
                           requested_count, returned_count, requested_codes_json,
                           returned_codes_json, missing_codes_json,
                           unexpected_codes_json, duplicate_codes_json, request_id,
                           business_code, rate_limited, retry_count, provider_called,
                           cache_hit_count, outcome, error_kind, http_status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        batch.batch_id,
                        batch.origin.value,
                        batch.provider,
                        batch.route.value,
                        batch.profile.value,
                        _iso(batch.bucket_close),
                        _iso(batch.started_at),
                        _iso(batch.completed_at),
                        batch.latency_ms,
                        len(batch.requested_codes),
                        len(batch.returned_codes),
                        _json_tuple(batch.requested_codes),
                        _json_tuple(batch.returned_codes),
                        _json_tuple(batch.missing_codes),
                        _json_tuple(batch.unexpected_codes),
                        _json_tuple(batch.duplicate_codes),
                        batch.request_id,
                        batch.business_code,
                        int(batch.rate_limited),
                        batch.retry_count,
                        int(batch.provider_called),
                        batch.cache_hit_count,
                        batch.outcome.value,
                        batch.error_kind,
                        batch.http_status,
                    ),
                )
                for item in items:
                    quote = item.quote
                    connection.execute(
                        """INSERT INTO monitor_fetch_items(
                               batch_id, instrument_key, instrument_json, status,
                               cache_hit, quote_json, provider_time, fetched_at,
                               freshness_seconds)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            item.batch_id,
                            item.instrument.key,
                            item.instrument.model_dump_json(),
                            item.status.value,
                            int(item.cache_hit),
                            quote.model_dump_json() if quote is not None else None,
                            _iso(quote.provider_updated_at) if quote is not None else None,
                            _iso(quote.fetched_at) if quote is not None else None,
                            quote.freshness_seconds if quote is not None else None,
                        ),
                    )
                    if quote is not None and item.status in {
                        FetchItemStatus.RETURNED,
                        FetchItemStatus.CACHE_HIT,
                    }:
                        connection.execute(
                            """INSERT INTO monitor_latest_quotes(
                                   provider, provider_route, instrument_key, profile,
                                   bucket_close, quote_json, provider_time, fetched_at,
                                   freshness_seconds, batch_id, updated_at)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                               ON CONFLICT(provider, provider_route, instrument_key, profile)
                               DO UPDATE SET
                                   bucket_close=excluded.bucket_close,
                                   quote_json=excluded.quote_json,
                                   provider_time=excluded.provider_time,
                                   fetched_at=excluded.fetched_at,
                                   freshness_seconds=excluded.freshness_seconds,
                                   batch_id=excluded.batch_id,
                                   updated_at=excluded.updated_at
                               WHERE excluded.fetched_at >= monitor_latest_quotes.fetched_at""",
                            (
                                batch.provider,
                                batch.route.value,
                                item.instrument.key,
                                batch.profile.value,
                                _iso(batch.bucket_close),
                                quote.model_dump_json(),
                                _iso(quote.provider_updated_at),
                                _iso(quote.fetched_at),
                                quote.freshness_seconds,
                                batch.batch_id,
                                _iso(batch.completed_at),
                            ),
                        )
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor snapshot fetch audit is unavailable"
            ) from exc

    def load_cached_quotes(
        self,
        *,
        provider: str,
        route: QuoteBatchRoute,
        profile: MonitorProfile,
        bucket_close: datetime,
        instruments: tuple[InstrumentId, ...],
    ) -> dict[str, QuoteEnvelope]:
        if not instruments:
            return {}
        placeholders = ",".join("?" for _ in instruments)
        params: tuple[object, ...] = (
            provider,
            route.value,
            profile.value,
            _iso(bucket_close),
            *(item.key for item in instruments),
        )
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    f"""SELECT instrument_key, quote_json, provider, fetched_at
                        FROM monitor_latest_quotes
                        WHERE provider=? AND provider_route=? AND profile=?
                          AND bucket_close=? AND instrument_key IN ({placeholders})""",
                    params,
                ).fetchall()
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor latest quote cache is unavailable"
            ) from exc

        result: dict[str, QuoteEnvelope] = {}
        for row in rows:
            try:
                quote = QuoteEnvelope.model_validate_json(str(row["quote_json"]))
            except (ValidationError, ValueError, TypeError) as exc:
                raise MonitorSnapshotStorageUnavailableError(
                    "monitor latest quote cache contains an invalid quote"
                ) from exc
            key = str(row["instrument_key"])
            if (
                quote.instrument.key != key
                or quote.provider != str(row["provider"])
                or _iso(quote.fetched_at) != str(row["fetched_at"])
            ):
                raise MonitorSnapshotStorageUnavailableError(
                    "monitor latest quote metadata does not match quote JSON"
                )
            result[key] = quote
        return result

    def load_manual_refresh_quotes(
        self,
        *,
        provider: str,
        route: QuoteBatchRoute,
        scheduled_bucket_close: datetime,
        interval_seconds: int,
        instruments: tuple[InstrumentId, ...],
    ) -> dict[str, QuoteEnvelope]:
        """Load trusted manual snapshots inside one scheduled profile interval."""

        if not instruments:
            return {}
        if interval_seconds < 1:
            raise ValueError("scheduled profile interval must be positive")
        window_started_at = scheduled_bucket_close - timedelta(
            seconds=interval_seconds
        )
        placeholders = ",".join("?" for _ in instruments)
        params: tuple[object, ...] = (
            FetchOrigin.MANUAL_REFRESH.value,
            provider,
            route.value,
            _iso(window_started_at),
            _iso(scheduled_bucket_close),
            *(item.key for item in instruments),
        )
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    f"""SELECT i.instrument_key, i.quote_json, i.fetched_at,
                                b.provider, b.bucket_close
                         FROM monitor_fetch_items AS i
                         JOIN monitor_fetch_batches AS b ON b.batch_id=i.batch_id
                         WHERE b.origin=? AND b.provider=? AND b.provider_route=?
                           AND b.bucket_close>? AND b.bucket_close<=?
                           AND b.provider_called=1 AND b.outcome='success'
                           AND i.status='returned' AND i.cache_hit=0
                           AND i.quote_json IS NOT NULL AND i.fetched_at IS NOT NULL
                           AND i.instrument_key IN ({placeholders})
                         ORDER BY i.instrument_key, b.bucket_close DESC,
                                  b.completed_at DESC, b.rowid DESC""",
                    params,
                ).fetchall()
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "manual refresh snapshot reuse is unavailable"
            ) from exc

        requested = {item.key: item for item in instruments}
        result: dict[str, QuoteEnvelope] = {}
        for row in rows:
            key = str(row["instrument_key"])
            if key in result:
                continue
            try:
                quote = QuoteEnvelope.model_validate_json(str(row["quote_json"]))
            except (ValidationError, ValueError, TypeError) as exc:
                raise MonitorSnapshotStorageUnavailableError(
                    "manual refresh snapshot reuse contains an invalid quote"
                ) from exc
            if (
                key not in requested
                or quote.instrument != requested[key]
                or quote.provider != str(row["provider"])
                or _iso(quote.fetched_at) != str(row["fetched_at"])
            ):
                raise MonitorSnapshotStorageUnavailableError(
                    "manual refresh snapshot metadata does not match quote JSON"
                )
            result[key] = quote
        return result

    def set_manual_refresh_cooldown(
        self, instrument_key: str, requested_at: datetime
    ) -> None:
        try:
            with self.connect() as connection:
                connection.execute(
                    """INSERT INTO monitor_manual_refresh_cooldown(
                           instrument_key, last_requested_at)
                       VALUES (?, ?)
                       ON CONFLICT(instrument_key) DO UPDATE SET
                           last_requested_at=excluded.last_requested_at""",
                    (instrument_key, _iso(requested_at)),
                )
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "manual refresh cooldown is unavailable"
            ) from exc

    def get_manual_refresh_cooldown(self, instrument_key: str) -> datetime | None:
        try:
            with self.connect() as connection:
                row = connection.execute(
                    """SELECT last_requested_at
                       FROM monitor_manual_refresh_cooldown WHERE instrument_key=?""",
                    (instrument_key,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "manual refresh cooldown is unavailable"
            ) from exc
        return None if row is None else _parse(str(row["last_requested_at"]))

    def try_acquire_manual_refresh_slot(
        self,
        instrument_key: str,
        *,
        requested_at: datetime,
        cooldown_seconds: int,
    ) -> datetime | None:
        """Atomically reserve a manual refresh or return its next allowed time."""

        if not instrument_key.strip():
            raise ValueError("manual refresh instrument key cannot be blank")
        if not 1 <= cooldown_seconds <= 3600:
            raise ValueError("manual refresh cooldown must be between 1 and 3600")
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT last_requested_at
                       FROM monitor_manual_refresh_cooldown
                       WHERE instrument_key=?""",
                    (instrument_key,),
                ).fetchone()
                if row is not None:
                    last_requested_at = _parse(str(row["last_requested_at"]))
                    retry_at = last_requested_at + timedelta(
                        seconds=cooldown_seconds
                    )
                    if requested_at < retry_at:
                        return retry_at
                connection.execute(
                    """INSERT INTO monitor_manual_refresh_cooldown(
                           instrument_key, last_requested_at)
                       VALUES (?, ?)
                       ON CONFLICT(instrument_key) DO UPDATE SET
                           last_requested_at=excluded.last_requested_at""",
                    (instrument_key, _iso(requested_at)),
                )
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "manual refresh cooldown is unavailable"
            ) from exc
        return None

    def load_latest_quote(
        self,
        *,
        provider: str,
        profile: MonitorProfile,
        instrument: InstrumentId,
    ) -> QuoteEnvelope | None:
        """Load the newest persisted quote regardless of its prior time bucket."""

        route = QuoteBatchQuery._route_for(instrument)
        try:
            with self.connect() as connection:
                row = connection.execute(
                    """SELECT quote_json, provider, provider_route, instrument_key,
                              profile, fetched_at
                       FROM monitor_latest_quotes
                       WHERE provider=? AND provider_route=? AND instrument_key=?
                         AND profile=?""",
                    (provider, route.value, instrument.key, profile.value),
                ).fetchone()
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor latest quote is unavailable"
            ) from exc
        if row is None:
            return None
        try:
            quote = QuoteEnvelope.model_validate_json(str(row["quote_json"]))
        except (ValidationError, ValueError, TypeError) as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor latest quote contains invalid evidence"
            ) from exc
        if (
            quote.instrument != instrument
            or quote.provider != str(row["provider"])
            or str(row["provider_route"]) != route.value
            or str(row["instrument_key"]) != instrument.key
            or str(row["profile"]) != profile.value
            or _iso(quote.fetched_at) != str(row["fetched_at"])
        ):
            raise MonitorSnapshotStorageUnavailableError(
                "monitor latest quote metadata does not match quote JSON"
            )
        return quote

    def read_capacity_window(
        self,
        *,
        started_at: datetime,
        ended_at: datetime,
    ) -> CapacityWindowSnapshot:
        """Read one local-day evidence window without mutating worker state."""

        if ended_at <= started_at:
            raise ValueError("monitor capacity window must be increasing")
        started_iso = _iso(started_at)
        ended_iso = _iso(ended_at)
        target_audit_window = _target_audit_window(started_at, ended_at)
        try:
            with self.connect() as connection:
                batch_rows = connection.execute(
                    """SELECT * FROM monitor_fetch_batches
                       WHERE completed_at>=? AND completed_at<?
                       ORDER BY completed_at, batch_id""",
                    (started_iso, ended_iso),
                ).fetchall()
                item_rows = connection.execute(
                    """SELECT i.*, b.origin, b.profile, b.bucket_close
                       FROM monitor_fetch_items AS i
                       JOIN monitor_fetch_batches AS b ON b.batch_id=i.batch_id
                       WHERE b.completed_at>=? AND b.completed_at<?
                       ORDER BY b.completed_at, i.batch_id, i.instrument_key""",
                    (started_iso, ended_iso),
                ).fetchall()
                target_rows = connection.execute(
                    """SELECT instrument_key, profile FROM monitor_targets
                       WHERE deleted_at IS NULL AND enabled=1
                       ORDER BY profile, instrument_key"""
                ).fetchall()
                target_change_count = (
                    int(
                        connection.execute(
                            """SELECT COUNT(*) FROM monitor_target_audit
                               WHERE changed_at>=? AND changed_at<?""",
                            (
                                _iso(target_audit_window[0]),
                                _iso(target_audit_window[1]),
                            ),
                        ).fetchone()[0]
                    )
                    if target_audit_window is not None
                    else 0
                )
                heartbeat_row = connection.execute(
                    """SELECT heartbeat_at FROM worker_leases
                       WHERE lease_name='minute-monitor'"""
                ).fetchone()
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "monitor capacity evidence is unavailable"
            ) from exc

        batches: list[CapacityBatchObservation] = []
        for row in batch_rows:
            try:
                batches.append(
                    CapacityBatchObservation(
                        batch_id=str(row["batch_id"]),
                        origin=FetchOrigin(str(row["origin"])),
                        provider=str(row["provider"]),
                        profile=MonitorProfile(str(row["profile"])),
                        bucket_close=_parse_required(
                            str(row["bucket_close"]), "bucket_close"
                        ),
                        started_at=_parse_required(
                            str(row["started_at"]), "started_at"
                        ),
                        completed_at=_parse_required(
                            str(row["completed_at"]), "completed_at"
                        ),
                        latency_ms=float(row["latency_ms"]),
                        requested_codes=_parse_codes(
                            str(row["requested_codes_json"]), "requested codes"
                        ),
                        returned_codes=_parse_codes(
                            str(row["returned_codes_json"]), "returned codes"
                        ),
                        missing_codes=_parse_codes(
                            str(row["missing_codes_json"]), "missing codes"
                        ),
                        business_code=(
                            int(row["business_code"])
                            if row["business_code"] is not None
                            else None
                        ),
                        rate_limited=bool(row["rate_limited"]),
                        provider_called=bool(row["provider_called"]),
                        cache_hit_count=int(row["cache_hit_count"]),
                        outcome=str(row["outcome"]),
                        error_kind=(
                            str(row["error_kind"])
                            if row["error_kind"] is not None
                            else None
                        ),
                    )
                )
            except (TypeError, ValueError) as exc:
                raise MonitorSnapshotStorageUnavailableError(
                    "monitor capacity batch evidence is invalid"
                ) from exc

        items: list[CapacityItemObservation] = []
        for row in item_rows:
            try:
                instrument = InstrumentId.model_validate_json(
                    str(row["instrument_json"])
                )
                if instrument.key != str(row["instrument_key"]):
                    raise ValueError("instrument key mismatch")
                freshness = (
                    float(row["freshness_seconds"])
                    if row["freshness_seconds"] is not None
                    else None
                )
                if freshness is not None and freshness < 0:
                    raise ValueError("negative freshness")
                items.append(
                    CapacityItemObservation(
                        batch_id=str(row["batch_id"]),
                        origin=FetchOrigin(str(row["origin"])),
                        profile=MonitorProfile(str(row["profile"])),
                        bucket_close=_parse_required(
                            str(row["bucket_close"]), "item bucket_close"
                        ),
                        instrument_key=instrument.key,
                        symbol=instrument.symbol,
                        status=str(row["status"]),
                        cache_hit=bool(row["cache_hit"]),
                        freshness_seconds=freshness,
                    )
                )
            except (ValidationError, TypeError, ValueError) as exc:
                raise MonitorSnapshotStorageUnavailableError(
                    "monitor capacity item evidence is invalid"
                ) from exc

        targets: list[CapacityTargetObservation] = []
        for row in target_rows:
            try:
                key = str(row["instrument_key"])
                symbol = key.rsplit(":", 1)[-1].strip().upper()
                if not symbol or key.count(":") < 3:
                    raise ValueError("invalid target instrument key")
                targets.append(
                    CapacityTargetObservation(
                        instrument_key=key,
                        symbol=symbol,
                        profile=MonitorProfile(str(row["profile"])),
                    )
                )
            except ValueError as exc:
                raise MonitorSnapshotStorageUnavailableError(
                    "monitor capacity target evidence is invalid"
                ) from exc

        heartbeat = (
            _parse_required(str(heartbeat_row["heartbeat_at"]), "worker heartbeat")
            if heartbeat_row is not None
            else None
        )
        return CapacityWindowSnapshot(
            batches=tuple(batches),
            items=tuple(items),
            targets=tuple(targets),
            target_change_count=target_change_count,
            worker_heartbeat_at=heartbeat,
        )

    def try_acquire_rate_lease(
        self,
        governor_key: str,
        *,
        owner_id: str,
        now: datetime,
        lease_seconds: float,
    ) -> datetime | None:
        now_iso = _iso(now)
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """SELECT owner_id, lease_expires_at, next_allowed_at
                       FROM monitor_rate_governor WHERE governor_key=?""",
                    (governor_key,),
                ).fetchone()
                if row is not None:
                    lease_expires = _parse(row["lease_expires_at"])
                    next_allowed = _parse(row["next_allowed_at"])
                    current_owner = row["owner_id"]
                    if (
                        current_owner is not None
                        and current_owner != owner_id
                        and lease_expires is not None
                        and lease_expires > now
                    ):
                        return lease_expires
                    if next_allowed is not None and next_allowed > now:
                        return next_allowed
                lease_expires_at = now + timedelta(seconds=lease_seconds)
                connection.execute(
                    """INSERT INTO monitor_rate_governor(
                           governor_key, owner_id, lease_expires_at,
                           next_allowed_at, updated_at)
                       VALUES (?, ?, ?, NULL, ?)
                       ON CONFLICT(governor_key) DO UPDATE SET
                           owner_id=excluded.owner_id,
                           lease_expires_at=excluded.lease_expires_at,
                           updated_at=excluded.updated_at""",
                    (governor_key, owner_id, _iso(lease_expires_at), now_iso),
                )
        except MonitorSnapshotStorageUnavailableError:
            raise
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "global monitor rate lease is unavailable"
            ) from exc
        return None

    def release_rate_lease(
        self,
        governor_key: str,
        *,
        owner_id: str,
        completed_at: datetime,
        min_interval_seconds: float,
    ) -> None:
        next_allowed = completed_at + timedelta(seconds=min_interval_seconds)
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """UPDATE monitor_rate_governor SET
                           owner_id=NULL, lease_expires_at=NULL,
                           next_allowed_at=?, updated_at=?
                       WHERE governor_key=? AND owner_id=?""",
                    (
                        _iso(next_allowed),
                        _iso(completed_at),
                        governor_key,
                        owner_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise MonitorSnapshotStorageUnavailableError(
                        "global monitor rate lease ownership was lost"
                    )
        except MonitorSnapshotStorageUnavailableError:
            raise
        except sqlite3.Error as exc:
            raise MonitorSnapshotStorageUnavailableError(
                "global monitor rate lease is unavailable"
            ) from exc


__all__ = ["SQLiteMonitorSnapshotStore"]
