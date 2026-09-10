"""Validated Watchlist V1 CRUD and capacity policy."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import json
from typing import Literal, Protocol, runtime_checkable
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator, model_validator

from custom_model.domain.models import (
    AssetType,
    FrozenModel,
    InstrumentId,
    _require_aware,
    utc_now,
)
from custom_model.domain.watchlist import (
    WatchlistEntry,
    WatchlistEntryRecord,
    WatchlistRole,
    validate_watchlist_assignment,
)


WatchlistAuditAction = Literal["created", "updated", "deleted"]
WatchlistOrderScope = Literal["securities", "sectors"]
_MUTABLE_FIELDS = frozenset({"role", "display_name", "note", "pinned"})
_ROLE_ORDER = {
    WatchlistRole.CORE: 0,
    WatchlistRole.WATCHLIST: 1,
    WatchlistRole.ETF: 2,
    WatchlistRole.INDEX: 3,
}


class WatchlistInputError(ValueError):
    """The requested entry is outside the admitted V1 profile."""


class WatchlistNotFoundError(LookupError):
    """No active entry exists for the requested identifier."""


class WatchlistConflictError(RuntimeError):
    """A duplicate instrument or optimistic-lock conflict was detected."""


class WatchlistLimitError(WatchlistConflictError):
    """An atomic active-entry capacity limit would be exceeded."""


class WatchlistStorageUnavailableError(RuntimeError):
    """The repository could not return a trustworthy result."""


class CreateWatchlistEntrySpec(FrozenModel):
    instrument: InstrumentId
    role: WatchlistRole
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=256)

    @field_validator("display_name", "note")
    @classmethod
    def normalized_text(cls, value: str, info) -> str:
        normalized = value.strip()
        if info.field_name == "display_name" and not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def admitted_assignment(self) -> "CreateWatchlistEntrySpec":
        validate_watchlist_assignment(self.instrument, self.role)
        return self


class PatchWatchlistEntrySpec(FrozenModel):
    expected_revision: int = Field(ge=1)
    role: WatchlistRole | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=256)
    pinned: bool | None = None

    @field_validator("display_name", "note")
    @classmethod
    def normalized_optional_text(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if info.field_name == "display_name" and not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def contains_a_change(self) -> "PatchWatchlistEntrySpec":
        supplied = self.model_fields_set & _MUTABLE_FIELDS
        if not supplied:
            raise ValueError("patch must include at least one mutable field")
        if any(getattr(self, field_name) is None for field_name in supplied):
            raise ValueError("patch fields cannot be null; use an empty note to clear it")
        return self


class WatchlistOrderItemSpec(FrozenModel):
    entry_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)

    @field_validator("entry_id")
    @classmethod
    def normalized_entry_id(cls, value: str) -> str:
        return value.strip()


class ReorderWatchlistEntriesSpec(FrozenModel):
    scope: WatchlistOrderScope
    role: WatchlistRole
    ordered_items: list[WatchlistOrderItemSpec] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_entry_ids(self) -> "ReorderWatchlistEntriesSpec":
        entry_ids = [item.entry_id for item in self.ordered_items]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("ordered_items cannot contain duplicate entry_id values")
        return self


@runtime_checkable
class WatchlistRepository(Protocol):
    def create_watchlist_entry(
        self, record: WatchlistEntryRecord
    ) -> WatchlistEntryRecord: ...

    def get_watchlist_entry(
        self, entry_id: str, *, include_deleted: bool = False
    ) -> WatchlistEntryRecord | None: ...

    def list_watchlist_entries(self) -> list[WatchlistEntryRecord]: ...

    def replace_watchlist_entry(
        self,
        record: WatchlistEntryRecord,
        *,
        expected_revision: int,
        action: WatchlistAuditAction,
    ) -> WatchlistEntryRecord: ...

    def reorder_watchlist_entries(
        self,
        records: list[WatchlistEntryRecord],
        *,
        expected_revisions: dict[str, int],
    ) -> list[WatchlistEntryRecord]: ...


class WatchlistService:
    """Single application boundary for canonical watchlist configuration."""

    MAX_CORE_STOCKS = 10
    MAX_ACTIVE_STOCKS = 30
    MAX_ACTIVE_ENTRIES = 100

    def __init__(
        self,
        repository: WatchlistRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.id_factory = id_factory

    @staticmethod
    def _aware(value: datetime, field_name: str) -> datetime:
        return _require_aware(value, field_name)

    @staticmethod
    def canonical_spec_sha256(spec: CreateWatchlistEntrySpec) -> str:
        payload = {
            "display_name": spec.display_name,
            "instrument": spec.instrument.model_dump(mode="json"),
            "note": spec.note,
            "role": spec.role.value,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _validate_persisted_record(
        cls, record: WatchlistEntryRecord
    ) -> WatchlistEntryRecord:
        """Re-parse JSON, re-admit semantics, and re-compute the fingerprint."""

        try:
            admitted = WatchlistEntryRecord.model_validate_json(
                record.model_dump_json()
            )
            spec = CreateWatchlistEntrySpec(
                instrument=admitted.entry.instrument,
                role=admitted.entry.role,
                display_name=admitted.entry.display_name,
                note=admitted.entry.note,
            )
            expected_hash = cls.canonical_spec_sha256(spec)
        except (ValidationError, ValueError, TypeError) as exc:
            raise WatchlistStorageUnavailableError(
                "persisted watchlist entry failed admission"
            ) from exc
        if expected_hash != admitted.spec_sha256:
            raise WatchlistStorageUnavailableError(
                "persisted watchlist entry fingerprint mismatch"
            )
        return admitted

    @staticmethod
    def _sort_key(record: WatchlistEntryRecord) -> tuple[object, ...]:
        return (
            _ROLE_ORDER[record.entry.role],
            not record.entry.pinned,
            record.entry.sort_order,
            record.created_at,
            record.entry.instrument.key,
            record.entry.entry_id,
        )

    @staticmethod
    def _scope(record: WatchlistEntryRecord) -> WatchlistOrderScope:
        if record.entry.instrument.asset_type is AssetType.SECTOR_INDEX:
            return "sectors"
        return "securities"

    def _next_sort_order(
        self,
        *,
        role: WatchlistRole,
        scope: WatchlistOrderScope,
        excluding_entry_id: str | None = None,
    ) -> int:
        ranks = [
            item.entry.sort_order
            for item in self._active_records()
            if item.entry.entry_id != excluding_entry_id
            and item.entry.role is role
            and self._scope(item) == scope
        ]
        return max(ranks, default=-1) + 1

    def _active_records(self) -> list[WatchlistEntryRecord]:
        return [
            self._validate_persisted_record(record)
            for record in self.repository.list_watchlist_entries()
        ]

    def _assert_capacity(
        self,
        candidate: WatchlistEntry,
        *,
        replacing_entry_id: str | None = None,
    ) -> None:
        records = [
            item
            for item in self._active_records()
            if item.entry.entry_id != replacing_entry_id
        ]
        if any(
            item.entry.instrument.key == candidate.instrument.key for item in records
        ):
            raise WatchlistConflictError("duplicate active watchlist instrument")
        if len(records) + 1 > self.MAX_ACTIVE_ENTRIES:
            raise WatchlistLimitError(
                f"watchlist active total limit is {self.MAX_ACTIVE_ENTRIES}"
            )
        if candidate.instrument.asset_type is AssetType.STOCK:
            stocks = sum(
                item.entry.instrument.asset_type is AssetType.STOCK for item in records
            )
            if stocks + 1 > self.MAX_ACTIVE_STOCKS:
                raise WatchlistLimitError(
                    f"watchlist active stock limit is {self.MAX_ACTIVE_STOCKS}"
                )
        if candidate.role is WatchlistRole.CORE:
            cores = sum(item.entry.role is WatchlistRole.CORE for item in records)
            if cores + 1 > self.MAX_CORE_STOCKS:
                raise WatchlistLimitError(
                    f"watchlist core stock limit is {self.MAX_CORE_STOCKS}"
                )

    def create(
        self,
        spec: CreateWatchlistEntrySpec,
        *,
        now: datetime | None = None,
    ) -> WatchlistEntryRecord:
        timestamp = self._aware(now or self.clock(), "now")
        entry = WatchlistEntry(
            entry_id=self.id_factory(),
            instrument=spec.instrument,
            role=spec.role,
            display_name=spec.display_name,
            note=spec.note,
            sort_order=self._next_sort_order(
                role=spec.role,
                scope=(
                    "sectors"
                    if spec.instrument.asset_type is AssetType.SECTOR_INDEX
                    else "securities"
                ),
            ),
        )
        self._assert_capacity(entry)
        record = WatchlistEntryRecord(
            entry=entry,
            created_at=timestamp,
            updated_at=timestamp,
            revision=1,
            spec_sha256=self.canonical_spec_sha256(spec),
        )
        return self._validate_persisted_record(
            self.repository.create_watchlist_entry(record)
        )

    def get(self, entry_id: str) -> WatchlistEntryRecord:
        record = self.repository.get_watchlist_entry(entry_id)
        if record is None:
            raise WatchlistNotFoundError("watchlist entry was not found")
        return self._validate_persisted_record(record)

    def list(self) -> list[WatchlistEntryRecord]:
        return sorted(self._active_records(), key=self._sort_key)

    def patch(
        self,
        entry_id: str,
        spec: PatchWatchlistEntrySpec,
        *,
        now: datetime | None = None,
    ) -> WatchlistEntryRecord:
        current = self.get(entry_id)
        if current.revision != spec.expected_revision:
            raise WatchlistConflictError("watchlist entry revision conflict")
        updates = {
            field_name: getattr(spec, field_name)
            for field_name in _MUTABLE_FIELDS
            if field_name in spec.model_fields_set
        }
        try:
            replacement_entry = WatchlistEntry.model_validate(
                {**current.entry.model_dump(mode="python"), **updates}
            )
        except ValidationError as exc:
            raise WatchlistInputError("watchlist patch failed admission") from exc
        if replacement_entry == current.entry:
            raise WatchlistInputError("watchlist patch contains no changes")
        if replacement_entry.role is not current.entry.role:
            replacement_entry = replacement_entry.model_copy(
                update={
                    "sort_order": self._next_sort_order(
                        role=replacement_entry.role,
                        scope=self._scope(current),
                        excluding_entry_id=current.entry.entry_id,
                    )
                }
            )
        self._assert_capacity(
            replacement_entry,
            replacing_entry_id=current.entry.entry_id,
        )
        timestamp = self._aware(now or self.clock(), "now")
        replacement_spec = CreateWatchlistEntrySpec(
            instrument=replacement_entry.instrument,
            role=replacement_entry.role,
            display_name=replacement_entry.display_name,
            note=replacement_entry.note,
        )
        replacement = current.model_copy(
            update={
                "entry": replacement_entry,
                "updated_at": timestamp,
                "revision": current.revision + 1,
                "spec_sha256": self.canonical_spec_sha256(replacement_spec),
            }
        )
        return self._validate_persisted_record(
            self.repository.replace_watchlist_entry(
                replacement,
                expected_revision=spec.expected_revision,
                action="updated",
            )
        )

    def reorder(
        self,
        spec: ReorderWatchlistEntriesSpec,
        *,
        now: datetime | None = None,
    ) -> list[WatchlistEntryRecord]:
        active = self._active_records()
        eligible = [
            record
            for record in active
            if record.entry.role is spec.role and self._scope(record) == spec.scope
        ]
        requested_ids = [item.entry_id for item in spec.ordered_items]
        eligible_ids = {record.entry.entry_id for record in eligible}
        if set(requested_ids) != eligible_ids or len(requested_ids) != len(eligible):
            raise WatchlistConflictError(
                "watchlist reorder must contain the exact active group"
            )
        current_by_id = {record.entry.entry_id: record for record in eligible}
        expected_revisions = {
            item.entry_id: item.expected_revision for item in spec.ordered_items
        }
        for item in spec.ordered_items:
            if current_by_id[item.entry_id].revision != item.expected_revision:
                raise WatchlistConflictError("watchlist entry revision conflict")

        seen_unpinned = False
        for entry_id in requested_ids:
            if current_by_id[entry_id].entry.pinned:
                if seen_unpinned:
                    raise WatchlistInputError(
                        "pinned entries must precede unpinned entries"
                    )
            else:
                seen_unpinned = True

        timestamp = self._aware(now or self.clock(), "now")
        replacements: list[WatchlistEntryRecord] = []
        for sort_order, entry_id in enumerate(requested_ids):
            current = current_by_id[entry_id]
            replacements.append(
                current.model_copy(
                    update={
                        "entry": current.entry.model_copy(
                            update={"sort_order": sort_order}
                        ),
                        "updated_at": timestamp,
                        "revision": current.revision + 1,
                    }
                )
            )
        persisted = self.repository.reorder_watchlist_entries(
            replacements,
            expected_revisions=expected_revisions,
        )
        admitted = [self._validate_persisted_record(record) for record in persisted]
        persisted_by_id = {record.entry.entry_id: record for record in admitted}
        return [persisted_by_id[entry_id] for entry_id in requested_ids]

    def delete(
        self,
        entry_id: str,
        *,
        expected_revision: int,
        now: datetime | None = None,
    ) -> WatchlistEntryRecord:
        current = self.get(entry_id)
        if current.revision != expected_revision:
            raise WatchlistConflictError("watchlist entry revision conflict")
        timestamp = self._aware(now or self.clock(), "now")
        tombstone = current.model_copy(
            update={
                "updated_at": timestamp,
                "deleted_at": timestamp,
                "revision": current.revision + 1,
            }
        )
        return self._validate_persisted_record(
            self.repository.replace_watchlist_entry(
                tombstone,
                expected_revision=expected_revision,
                action="deleted",
            )
        )


__all__ = [
    "CreateWatchlistEntrySpec",
    "PatchWatchlistEntrySpec",
    "ReorderWatchlistEntriesSpec",
    "WatchlistAuditAction",
    "WatchlistConflictError",
    "WatchlistInputError",
    "WatchlistLimitError",
    "WatchlistNotFoundError",
    "WatchlistOrderItemSpec",
    "WatchlistOrderScope",
    "WatchlistRepository",
    "WatchlistService",
    "WatchlistStorageUnavailableError",
]
