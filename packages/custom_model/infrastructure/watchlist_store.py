"""SQLite WAL persistence for canonical watchlist configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from custom_model.application.watchlist_service import (
    WatchlistAuditAction,
    WatchlistConflictError,
    WatchlistLimitError,
    WatchlistNotFoundError,
    WatchlistService,
    WatchlistStorageUnavailableError,
)
from custom_model.domain.models import AssetType
from custom_model.domain.watchlist import WatchlistEntryRecord, WatchlistRole


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("database times must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


class SQLiteWatchlistStore:
    """Owns atomic uniqueness, capacity, optimistic locking, and audit rows."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
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
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS watchlist_schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watchlist_entries (
                    entry_id TEXT PRIMARY KEY,
                    instrument_key TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('core', 'watchlist', 'etf', 'index')),
                    asset_type TEXT NOT NULL,
                    spec_sha256 TEXT NOT NULL CHECK(length(spec_sha256) = 64),
                    record_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    deleted_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS uq_watchlist_active_instrument
                    ON watchlist_entries(instrument_key) WHERE deleted_at IS NULL;
                CREATE INDEX IF NOT EXISTS idx_watchlist_active_role
                    ON watchlist_entries(role, created_at, instrument_key)
                    WHERE deleted_at IS NULL;
                CREATE TABLE IF NOT EXISTS watchlist_entry_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entry_id TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1),
                    action TEXT NOT NULL CHECK(action IN ('created', 'updated', 'deleted')),
                    changed_at TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    UNIQUE(entry_id, revision)
                );
                INSERT OR IGNORE INTO watchlist_schema_migrations(version, applied_at)
                    VALUES ('001_watchlist_core', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
                """
            )

    @staticmethod
    def _record_from_json(payload: str) -> WatchlistEntryRecord:
        try:
            return WatchlistEntryRecord.model_validate_json(payload)
        except (ValidationError, ValueError, TypeError) as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository contains an invalid record"
            ) from exc

    @classmethod
    def _record_from_row(cls, row: sqlite3.Row) -> WatchlistEntryRecord:
        record = cls._record_from_json(str(row["record_json"]))
        expected: dict[str, object] = {
            "entry_id": record.entry.entry_id,
            "instrument_key": record.entry.instrument.key,
            "role": record.entry.role.value,
            "asset_type": record.entry.instrument.asset_type.value,
            "spec_sha256": record.spec_sha256,
            "created_at": _iso(record.created_at),
            "updated_at": _iso(record.updated_at),
            "revision": record.revision,
            "deleted_at": _iso(record.deleted_at) if record.deleted_at else None,
        }
        if any(row[field_name] != value for field_name, value in expected.items()):
            raise WatchlistStorageUnavailableError(
                "watchlist repository metadata does not match record JSON"
            )
        return record

    @staticmethod
    def _record_values(record: WatchlistEntryRecord) -> tuple[object, ...]:
        return (
            record.entry.entry_id,
            record.entry.instrument.key,
            record.entry.role.value,
            record.entry.instrument.asset_type.value,
            record.spec_sha256,
            record.model_dump_json(),
            _iso(record.created_at),
            _iso(record.updated_at),
            record.revision,
            _iso(record.deleted_at) if record.deleted_at else None,
        )

    @staticmethod
    def _insert_audit(
        connection: sqlite3.Connection,
        record: WatchlistEntryRecord,
        action: WatchlistAuditAction,
    ) -> None:
        connection.execute(
            """INSERT INTO watchlist_entry_audit(
                   entry_id, revision, action, changed_at, record_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                record.entry.entry_id,
                record.revision,
                action,
                _iso(record.updated_at),
                record.model_dump_json(),
            ),
        )

    @staticmethod
    def _assert_capacity(
        connection: sqlite3.Connection,
        record: WatchlistEntryRecord,
        *,
        excluding_entry_id: str | None = None,
    ) -> None:
        exclusion = " AND entry_id<>?" if excluding_entry_id is not None else ""
        params: tuple[object, ...] = (
            (excluding_entry_id,) if excluding_entry_id is not None else ()
        )
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM watchlist_entries WHERE deleted_at IS NULL{exclusion}",
                params,
            ).fetchone()[0]
        )
        if total + 1 > WatchlistService.MAX_ACTIVE_ENTRIES:
            raise WatchlistLimitError(
                f"watchlist active total limit is {WatchlistService.MAX_ACTIVE_ENTRIES}"
            )
        if record.entry.instrument.asset_type is AssetType.STOCK:
            stocks = int(
                connection.execute(
                    f"""SELECT COUNT(*) FROM watchlist_entries
                        WHERE deleted_at IS NULL AND asset_type=?{exclusion}""",
                    (AssetType.STOCK.value, *params),
                ).fetchone()[0]
            )
            if stocks + 1 > WatchlistService.MAX_ACTIVE_STOCKS:
                raise WatchlistLimitError(
                    f"watchlist active stock limit is {WatchlistService.MAX_ACTIVE_STOCKS}"
                )
        if record.entry.role is WatchlistRole.CORE:
            cores = int(
                connection.execute(
                    f"""SELECT COUNT(*) FROM watchlist_entries
                        WHERE deleted_at IS NULL AND role=?{exclusion}""",
                    (WatchlistRole.CORE.value, *params),
                ).fetchone()[0]
            )
            if cores + 1 > WatchlistService.MAX_CORE_STOCKS:
                raise WatchlistLimitError(
                    f"watchlist core stock limit is {WatchlistService.MAX_CORE_STOCKS}"
                )

    def create_watchlist_entry(
        self, record: WatchlistEntryRecord
    ) -> WatchlistEntryRecord:
        record = WatchlistService._validate_persisted_record(record)
        if record.revision != 1 or record.deleted_at is not None:
            raise WatchlistConflictError(
                "a new watchlist entry must be an active revision one record"
            )
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._assert_capacity(connection, record)
                connection.execute(
                    """INSERT INTO watchlist_entries(
                           entry_id, instrument_key, role, asset_type, spec_sha256,
                           record_json, created_at, updated_at, revision, deleted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._record_values(record),
                )
                self._insert_audit(connection, record, "created")
        except WatchlistLimitError:
            raise
        except sqlite3.IntegrityError as exc:
            raise WatchlistConflictError(
                "duplicate watchlist entry id or active instrument"
            ) from exc
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return record

    def get_watchlist_entry(
        self, entry_id: str, *, include_deleted: bool = False
    ) -> WatchlistEntryRecord | None:
        clause = "" if include_deleted else " AND deleted_at IS NULL"
        try:
            with self.connect() as connection:
                row = connection.execute(
                    f"SELECT * FROM watchlist_entries WHERE entry_id=?{clause}",
                    (entry_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return None if row is None else self._record_from_row(row)

    def list_watchlist_entries(self) -> list[WatchlistEntryRecord]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """SELECT * FROM watchlist_entries
                       WHERE deleted_at IS NULL
                       ORDER BY CASE role
                           WHEN 'core' THEN 0
                           WHEN 'watchlist' THEN 1
                           WHEN 'etf' THEN 2
                           ELSE 3 END,
                           created_at, instrument_key, entry_id"""
                ).fetchall()
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return [self._record_from_row(row) for row in rows]

    def replace_watchlist_entry(
        self,
        record: WatchlistEntryRecord,
        *,
        expected_revision: int,
        action: WatchlistAuditAction,
    ) -> WatchlistEntryRecord:
        record = WatchlistService._validate_persisted_record(record)
        if record.revision != expected_revision + 1:
            raise WatchlistConflictError("replacement revision must increment by one")
        if action == "deleted" and record.deleted_at is None:
            raise WatchlistConflictError("deleted audit action requires a tombstone")
        if action == "updated" and record.deleted_at is not None:
            raise WatchlistConflictError("updated audit action cannot write a tombstone")
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current = connection.execute(
                    """SELECT revision, deleted_at, instrument_key, created_at
                       FROM watchlist_entries WHERE entry_id=?""",
                    (record.entry.entry_id,),
                ).fetchone()
                if current is None or current["deleted_at"] is not None:
                    raise WatchlistNotFoundError("watchlist entry was not found")
                if int(current["revision"]) != expected_revision:
                    raise WatchlistConflictError("watchlist entry revision conflict")
                if (
                    str(current["instrument_key"]) != record.entry.instrument.key
                    or str(current["created_at"]) != _iso(record.created_at)
                ):
                    raise WatchlistConflictError(
                        "watchlist instrument and creation time are immutable"
                    )
                if record.deleted_at is None:
                    self._assert_capacity(
                        connection,
                        record,
                        excluding_entry_id=record.entry.entry_id,
                    )
                cursor = connection.execute(
                    """UPDATE watchlist_entries SET
                           instrument_key=?, role=?, asset_type=?, spec_sha256=?,
                           record_json=?, created_at=?, updated_at=?, revision=?, deleted_at=?
                       WHERE entry_id=? AND revision=? AND deleted_at IS NULL""",
                    (
                        *self._record_values(record)[1:],
                        record.entry.entry_id,
                        expected_revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise WatchlistConflictError("watchlist entry revision conflict")
                self._insert_audit(connection, record, action)
        except (WatchlistConflictError, WatchlistLimitError, WatchlistNotFoundError):
            raise
        except sqlite3.IntegrityError as exc:
            raise WatchlistConflictError(
                "duplicate active watchlist instrument"
            ) from exc
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return record

    def reorder_watchlist_entries(
        self,
        records: list[WatchlistEntryRecord],
        *,
        expected_revisions: dict[str, int],
    ) -> list[WatchlistEntryRecord]:
        admitted = [
            WatchlistService._validate_persisted_record(record)
            for record in records
        ]
        entry_ids = [record.entry.entry_id for record in admitted]
        if (
            not admitted
            or len(entry_ids) != len(set(entry_ids))
            or set(entry_ids) != set(expected_revisions)
        ):
            raise WatchlistConflictError(
                "watchlist reorder records and revisions must match"
            )
        for record in admitted:
            expected_revision = expected_revisions[record.entry.entry_id]
            if record.revision != expected_revision + 1:
                raise WatchlistConflictError(
                    "replacement revision must increment by one"
                )
            if record.deleted_at is not None:
                raise WatchlistConflictError(
                    "watchlist reorder cannot write a tombstone"
                )

        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_by_id: dict[str, WatchlistEntryRecord] = {}
                for entry_id in entry_ids:
                    row = connection.execute(
                        "SELECT * FROM watchlist_entries WHERE entry_id=?",
                        (entry_id,),
                    ).fetchone()
                    if row is None or row["deleted_at"] is not None:
                        raise WatchlistNotFoundError(
                            "watchlist entry was not found"
                        )
                    current = self._record_from_row(row)
                    if current.revision != expected_revisions[entry_id]:
                        raise WatchlistConflictError(
                            "watchlist entry revision conflict"
                        )
                    current_by_id[entry_id] = current

                for record in admitted:
                    current = current_by_id[record.entry.entry_id]
                    expected_entry = current.entry.model_copy(
                        update={"sort_order": record.entry.sort_order}
                    )
                    if (
                        record.entry != expected_entry
                        or record.created_at != current.created_at
                        or record.spec_sha256 != current.spec_sha256
                    ):
                        raise WatchlistConflictError(
                            "watchlist reorder may only change sort_order"
                        )
                    cursor = connection.execute(
                        """UPDATE watchlist_entries SET
                               record_json=?, updated_at=?, revision=?
                           WHERE entry_id=? AND revision=? AND deleted_at IS NULL""",
                        (
                            record.model_dump_json(),
                            _iso(record.updated_at),
                            record.revision,
                            record.entry.entry_id,
                            expected_revisions[record.entry.entry_id],
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise WatchlistConflictError(
                            "watchlist entry revision conflict"
                        )
                    self._insert_audit(connection, record, "updated")
        except (WatchlistConflictError, WatchlistNotFoundError):
            raise
        except sqlite3.IntegrityError as exc:
            raise WatchlistConflictError(
                "watchlist reorder conflicts with current state"
            ) from exc
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return admitted

    def watchlist_audit_actions(self, entry_id: str) -> list[str]:
        try:
            with self.connect() as connection:
                rows = connection.execute(
                    """SELECT action FROM watchlist_entry_audit
                       WHERE entry_id=? ORDER BY revision""",
                    (entry_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise WatchlistStorageUnavailableError(
                "watchlist repository is unavailable"
            ) from exc
        return [str(row["action"]) for row in rows]


__all__ = ["SQLiteWatchlistStore"]
