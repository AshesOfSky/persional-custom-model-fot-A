from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_model.application.watchlist_service import (
    CreateWatchlistEntrySpec,
    PatchWatchlistEntrySpec,
    ReorderWatchlistEntriesSpec,
    WatchlistOrderItemSpec,
    WatchlistConflictError,
    WatchlistLimitError,
    WatchlistService,
    WatchlistStorageUnavailableError,
)
from custom_model.domain.models import AssetType, Exchange, InstrumentId, Market
from custom_model.domain.watchlist import WatchlistEntryRecord, WatchlistRole


NOW = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)


def stock(symbol: str = "600519") -> InstrumentId:
    exchange = Exchange.SSE if symbol.startswith(("5", "6", "9")) else Exchange.SZSE
    return InstrumentId(
        symbol=symbol,
        exchange=exchange,
        market=Market.CN,
        asset_type=AssetType.STOCK,
        currency="CNY",
    )


def index(symbol: str) -> InstrumentId:
    return InstrumentId(
        symbol=symbol,
        exchange=Exchange.OTHER,
        market=Market.CN,
        asset_type=AssetType.INDEX,
        currency="CNY",
    )


def sector(symbol: str) -> InstrumentId:
    return InstrumentId(
        symbol=symbol,
        exchange=Exchange.OTHER,
        market=Market.CN,
        asset_type=AssetType.SECTOR_INDEX,
        currency="CNY",
    )


def spec(
    symbol: str = "600519",
    *,
    role: WatchlistRole = WatchlistRole.WATCHLIST,
) -> CreateWatchlistEntrySpec:
    return CreateWatchlistEntrySpec(
        instrument=stock(symbol),
        role=role,
        display_name=f"证券{symbol}",
        note="观察",
    )


class FakeRepository:
    def __init__(self) -> None:
        self.records: dict[str, WatchlistEntryRecord] = {}

    def create_watchlist_entry(
        self, record: WatchlistEntryRecord
    ) -> WatchlistEntryRecord:
        if any(
            item.deleted_at is None
            and item.entry.instrument.key == record.entry.instrument.key
            for item in self.records.values()
        ):
            raise WatchlistConflictError("duplicate active watchlist instrument")
        self.records[record.entry.entry_id] = record
        return record

    def get_watchlist_entry(
        self, entry_id: str, *, include_deleted: bool = False
    ) -> WatchlistEntryRecord | None:
        record = self.records.get(entry_id)
        if record is not None and record.deleted_at is not None and not include_deleted:
            return None
        return record

    def list_watchlist_entries(self) -> list[WatchlistEntryRecord]:
        # Deliberately reverse insertion order: the service owns public ordering.
        return list(reversed([
            item for item in self.records.values() if item.deleted_at is None
        ]))

    def replace_watchlist_entry(
        self,
        record: WatchlistEntryRecord,
        *,
        expected_revision: int,
        action: str,
    ) -> WatchlistEntryRecord:
        current = self.records.get(record.entry.entry_id)
        if current is None or current.deleted_at is not None:
            raise WatchlistConflictError("watchlist entry is not active")
        if current.revision != expected_revision:
            raise WatchlistConflictError("watchlist entry revision conflict")
        self.records[record.entry.entry_id] = record
        return record

    def reorder_watchlist_entries(
        self,
        records: list[WatchlistEntryRecord],
        *,
        expected_revisions: dict[str, int],
    ) -> list[WatchlistEntryRecord]:
        for record in records:
            current = self.records.get(record.entry.entry_id)
            if (
                current is None
                or current.deleted_at is not None
                or current.revision
                != expected_revisions.get(record.entry.entry_id)
            ):
                raise WatchlistConflictError("watchlist entry revision conflict")
        for record in records:
            self.records[record.entry.entry_id] = record
        return records


def service(repository: FakeRepository | None = None) -> WatchlistService:
    ids = (f"entry-{number}" for number in range(1, 1000))
    return WatchlistService(
        repository or FakeRepository(),
        clock=lambda: NOW,
        id_factory=ids.__next__,
    )


def test_canonical_hash_is_deterministic_and_ignores_storage_metadata() -> None:
    first = service().canonical_spec_sha256(spec())
    second = service().canonical_spec_sha256(
        CreateWatchlistEntrySpec(
            note="  观察  ",
            display_name="  证券600519  ",
            role=WatchlistRole.WATCHLIST,
            instrument=stock(),
        )
    )

    assert first == second
    assert len(first) == 64


def test_create_get_list_and_role_patch_are_validated_and_sorted() -> None:
    subject = service()
    watch = subject.create(spec("000001"))
    core = subject.create(spec("600519", role=WatchlistRole.CORE))

    assert [item.entry.entry_id for item in subject.list()] == [
        core.entry.entry_id,
        watch.entry.entry_id,
    ]
    assert subject.get(core.entry.entry_id) == core

    promoted = subject.patch(
        watch.entry.entry_id,
        PatchWatchlistEntrySpec(
            expected_revision=1,
            role=WatchlistRole.CORE,
            note="提升为核心",
        ),
    )
    assert promoted.entry.role is WatchlistRole.CORE
    assert promoted.revision == 2
    assert promoted.spec_sha256 != watch.spec_sha256


def test_pin_and_reorder_are_persistent_presentation_changes() -> None:
    subject = service()
    first = subject.create(spec("000001"))
    second = subject.create(spec("000002"))
    third = subject.create(spec("000003"))
    sector_entry = subject.create(
        CreateWatchlistEntrySpec(
            instrument=sector("886015"),
            role=WatchlistRole.INDEX,
            display_name="创新药",
        )
    )

    pinned = subject.patch(
        second.entry.entry_id,
        PatchWatchlistEntrySpec(expected_revision=1, pinned=True),
    )
    assert pinned.entry.pinned is True
    assert pinned.spec_sha256 == second.spec_sha256
    assert [item.entry.entry_id for item in subject.list()[:3]] == [
        pinned.entry.entry_id,
        first.entry.entry_id,
        third.entry.entry_id,
    ]

    unpinned = subject.patch(
        pinned.entry.entry_id,
        PatchWatchlistEntrySpec(expected_revision=2, pinned=False),
    )
    reordered = subject.reorder(
        ReorderWatchlistEntriesSpec(
            scope="securities",
            role=WatchlistRole.WATCHLIST,
            ordered_items=[
                WatchlistOrderItemSpec(
                    entry_id=third.entry.entry_id,
                    expected_revision=third.revision,
                ),
                WatchlistOrderItemSpec(
                    entry_id=first.entry.entry_id,
                    expected_revision=first.revision,
                ),
                WatchlistOrderItemSpec(
                    entry_id=unpinned.entry.entry_id,
                    expected_revision=unpinned.revision,
                ),
            ],
        )
    )

    assert [item.entry.entry_id for item in reordered] == [
        third.entry.entry_id,
        first.entry.entry_id,
        unpinned.entry.entry_id,
    ]
    assert [item.entry.sort_order for item in reordered] == [0, 1, 2]
    assert subject.get(sector_entry.entry.entry_id).revision == 1


def test_reorder_requires_the_exact_role_and_scope_and_rejects_stale_revisions() -> None:
    subject = service()
    first = subject.create(spec("000001"))
    second = subject.create(spec("000002"))

    with pytest.raises(WatchlistConflictError, match="exact active group"):
        subject.reorder(
            ReorderWatchlistEntriesSpec(
                scope="securities",
                role=WatchlistRole.WATCHLIST,
                ordered_items=[
                    WatchlistOrderItemSpec(
                        entry_id=first.entry.entry_id,
                        expected_revision=first.revision,
                    )
                ],
            )
        )

    with pytest.raises(WatchlistConflictError, match="revision"):
        subject.reorder(
            ReorderWatchlistEntriesSpec(
                scope="securities",
                role=WatchlistRole.WATCHLIST,
                ordered_items=[
                    WatchlistOrderItemSpec(
                        entry_id=second.entry.entry_id,
                        expected_revision=999,
                    ),
                    WatchlistOrderItemSpec(
                        entry_id=first.entry.entry_id,
                        expected_revision=first.revision,
                    ),
                ],
            )
        )

    assert [item.entry.entry_id for item in subject.list()] == [
        first.entry.entry_id,
        second.entry.entry_id,
    ]


def test_service_enforces_active_core_stock_and_total_limits_with_a_fake() -> None:
    subject = service()
    for number in range(subject.MAX_CORE_STOCKS):
        subject.create(spec(f"60{number:04d}", role=WatchlistRole.CORE))
    with pytest.raises(WatchlistLimitError, match="core"):
        subject.create(spec("601999", role=WatchlistRole.CORE))

    subject = service()
    for number in range(subject.MAX_ACTIVE_STOCKS):
        subject.create(spec(f"00{number:04d}"))
    with pytest.raises(WatchlistLimitError, match="stock"):
        subject.create(spec("001999"))

    subject = service()
    for number in range(subject.MAX_ACTIVE_ENTRIES):
        subject.create(
            CreateWatchlistEntrySpec(
                instrument=index(f"IDX{number:03d}"),
                role=WatchlistRole.INDEX,
                display_name=f"指数{number}",
            )
        )
    with pytest.raises(WatchlistLimitError, match="total"):
        subject.create(
            CreateWatchlistEntrySpec(
                instrument=index("OVER"),
                role=WatchlistRole.INDEX,
                display_name="超额指数",
            )
        )


def test_get_and_list_re_admit_semantics_json_and_fingerprint() -> None:
    repository = FakeRepository()
    subject = service(repository)
    created = subject.create(spec())

    repository.records[created.entry.entry_id] = created.model_copy(
        update={"spec_sha256": "f" * 64}
    )
    with pytest.raises(WatchlistStorageUnavailableError, match="fingerprint"):
        subject.get(created.entry.entry_id)
    with pytest.raises(WatchlistStorageUnavailableError, match="fingerprint"):
        subject.list()

    repository.records[created.entry.entry_id] = created.model_construct(
        entry=created.entry.model_construct(
            entry_id=created.entry.entry_id,
            instrument=stock(),
            role=WatchlistRole.ETF,
            display_name="伪造 ETF",
            note="",
        ),
        created_at=created.created_at,
        updated_at=created.updated_at,
        revision=created.revision,
        deleted_at=None,
        spec_sha256=created.spec_sha256,
    )
    with pytest.raises(WatchlistStorageUnavailableError, match="failed admission"):
        subject.get(created.entry.entry_id)


def test_stale_patch_revision_is_rejected_before_replace() -> None:
    subject = service()
    created = subject.create(spec())
    subject.patch(
        created.entry.entry_id,
        PatchWatchlistEntrySpec(expected_revision=1, note="revision two"),
    )

    with pytest.raises(WatchlistConflictError, match="revision"):
        subject.patch(
            created.entry.entry_id,
            PatchWatchlistEntrySpec(expected_revision=1, note="stale"),
        )
