from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from custom_model.application.watchlist_service import (
    CreateWatchlistEntrySpec,
    PatchWatchlistEntrySpec,
    WatchlistConflictError,
    WatchlistLimitError,
    WatchlistService,
    WatchlistStorageUnavailableError,
)
from custom_model.domain.models import AssetType, Exchange, InstrumentId, Market
from custom_model.domain.watchlist import WatchlistRole
from custom_model.infrastructure.watchlist_store import SQLiteWatchlistStore


NOW = datetime(2026, 8, 15, 3, 0, tzinfo=timezone.utc)


def instrument(
    symbol: str,
    *,
    asset_type: AssetType = AssetType.STOCK,
    exchange: Exchange | None = None,
) -> InstrumentId:
    if exchange is None:
        if asset_type in {AssetType.INDEX, AssetType.SECTOR_INDEX}:
            exchange = Exchange.OTHER
        else:
            exchange = Exchange.SSE if symbol.startswith(("5", "6", "9")) else Exchange.SZSE
    return InstrumentId(
        symbol=symbol,
        exchange=exchange,
        market=Market.CN,
        asset_type=asset_type,
        currency="CNY",
    )


def spec(
    symbol: str,
    *,
    role: WatchlistRole = WatchlistRole.WATCHLIST,
    asset_type: AssetType = AssetType.STOCK,
) -> CreateWatchlistEntrySpec:
    return CreateWatchlistEntrySpec(
        instrument=instrument(symbol, asset_type=asset_type),
        role=role,
        display_name=f"证券{symbol}",
    )


def service(tmp_path, ids=None) -> tuple[WatchlistService, SQLiteWatchlistStore]:
    store = SQLiteWatchlistStore(tmp_path / "watchlist.sqlite3")
    id_source = ids or (f"entry-{number}" for number in range(1, 1000)).__next__
    return (
        WatchlistService(store, clock=lambda: NOW, id_factory=id_source),
        store,
    )


def test_store_enables_wal_busy_timeout_and_atomic_schema(tmp_path) -> None:
    _, store = service(tmp_path)

    with store.connect() as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == 5000
    assert {"watchlist_entries", "watchlist_entry_audit"} <= tables


def test_duplicate_active_instrument_conflicts_but_soft_delete_allows_reuse(
    tmp_path,
) -> None:
    ids = iter(("entry-1", "entry-2")).__next__
    subject, store = service(tmp_path, ids)
    original = subject.create(spec("600519"))
    duplicate = original.model_copy(
        update={
            "entry": original.entry.model_copy(update={"entry_id": "duplicate-id"})
        }
    )

    with pytest.raises(WatchlistConflictError, match="duplicate"):
        store.create_watchlist_entry(duplicate)

    deleted = subject.delete(
        original.entry.entry_id,
        expected_revision=1,
        now=NOW + timedelta(minutes=1),
    )
    replacement = subject.create(spec("600519", role=WatchlistRole.CORE))

    assert deleted.deleted_at == NOW + timedelta(minutes=1)
    assert deleted.revision == 2
    assert replacement.entry.entry_id == "entry-2"
    assert store.watchlist_audit_actions("entry-1") == ["created", "deleted"]


def test_revision_conflict_and_deterministic_order_and_hash(tmp_path) -> None:
    subject, store = service(tmp_path)
    watch = subject.create(spec("000001"))
    core = subject.create(spec("600519", role=WatchlistRole.CORE))
    etf = subject.create(
        spec("510300", role=WatchlistRole.ETF, asset_type=AssetType.ETF)
    )

    changed = subject.patch(
        watch.entry.entry_id,
        PatchWatchlistEntrySpec(expected_revision=1, note="new note"),
    )
    with pytest.raises(WatchlistConflictError, match="revision"):
        store.replace_watchlist_entry(
            changed,
            expected_revision=1,
            action="updated",
        )

    assert [item.entry.entry_id for item in subject.list()] == [
        core.entry.entry_id,
        changed.entry.entry_id,
        etf.entry.entry_id,
    ]
    assert (
        subject.canonical_spec_sha256(spec("600519", role=WatchlistRole.CORE))
        == core.spec_sha256
    )
    assert store.watchlist_audit_actions(watch.entry.entry_id) == ["created", "updated"]


def test_atomic_reorder_rolls_back_all_rows_when_one_revision_is_stale(tmp_path) -> None:
    subject, store = service(tmp_path)
    first = subject.create(spec("000001"))
    second = subject.create(spec("000002"))
    third = subject.create(spec("000003"))
    timestamp = NOW + timedelta(minutes=1)
    replacements = [
        record.model_copy(
            update={
                "entry": record.entry.model_copy(update={"sort_order": index}),
                "updated_at": timestamp,
                "revision": record.revision + 1,
            }
        )
        for index, record in enumerate((third, first, second))
    ]
    subject.patch(
        second.entry.entry_id,
        PatchWatchlistEntrySpec(expected_revision=1, note="concurrent edit"),
        now=timestamp,
    )

    with pytest.raises(WatchlistConflictError, match="revision"):
        store.reorder_watchlist_entries(
            replacements,
            expected_revisions={
                first.entry.entry_id: 1,
                second.entry.entry_id: 1,
                third.entry.entry_id: 1,
            },
        )

    assert subject.get(first.entry.entry_id).revision == 1
    assert subject.get(first.entry.entry_id).entry.sort_order == 0
    assert subject.get(third.entry.entry_id).revision == 1
    assert subject.get(third.entry.entry_id).entry.sort_order == 2
    assert store.watchlist_audit_actions(first.entry.entry_id) == ["created"]
    assert store.watchlist_audit_actions(third.entry.entry_id) == ["created"]


def test_etf_and_index_do_not_consume_the_thirty_stock_slots(tmp_path) -> None:
    subject, _ = service(tmp_path)
    for number in range(subject.MAX_ACTIVE_STOCKS):
        subject.create(spec(f"00{number:04d}"))
    subject.create(spec("510300", role=WatchlistRole.ETF, asset_type=AssetType.ETF))
    subject.create(
        spec("000300", role=WatchlistRole.INDEX, asset_type=AssetType.INDEX)
    )

    assert len(subject.list()) == subject.MAX_ACTIVE_STOCKS + 2
    with pytest.raises(WatchlistLimitError, match="stock"):
        subject.create(spec("001999"))


def test_begin_immediate_serializes_the_core_limit_boundary(tmp_path) -> None:
    subject, _ = service(tmp_path)
    for number in range(subject.MAX_CORE_STOCKS - 1):
        subject.create(spec(f"60{number:04d}", role=WatchlistRole.CORE))

    def create_candidate(symbol: str) -> str:
        candidate = WatchlistService(
            SQLiteWatchlistStore(tmp_path / "watchlist.sqlite3"),
            clock=lambda: NOW,
            id_factory=lambda: f"entry-{symbol}",
        )
        return candidate.create(spec(symbol, role=WatchlistRole.CORE)).entry.entry_id

    outcomes: list[str] = []
    errors: list[Exception] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create_candidate, symbol) for symbol in ("601991", "601992")]
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as exc:  # noqa: BLE001 - assertion captures exact type below
                errors.append(exc)

    assert len(outcomes) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], WatchlistLimitError)
    assert len(subject.list()) == subject.MAX_CORE_STOCKS


def test_active_total_is_bounded_even_when_entries_are_not_stocks(tmp_path) -> None:
    subject, _ = service(tmp_path)
    for number in range(subject.MAX_ACTIVE_ENTRIES):
        subject.create(
            spec(
                f"IDX{number:03d}",
                role=WatchlistRole.INDEX,
                asset_type=AssetType.INDEX,
            )
        )

    with pytest.raises(WatchlistLimitError, match="total"):
        subject.create(
            spec("OVER", role=WatchlistRole.INDEX, asset_type=AssetType.INDEX)
        )


def test_corrupt_json_and_valid_json_with_wrong_fingerprint_fail_closed(tmp_path) -> None:
    subject, store = service(tmp_path)
    created = subject.create(spec("600519"))

    with store.connect() as connection:
        connection.execute(
            "UPDATE watchlist_entries SET record_json=? WHERE entry_id=?",
            ("{not-json", created.entry.entry_id),
        )
    with pytest.raises(WatchlistStorageUnavailableError, match="invalid record"):
        subject.get(created.entry.entry_id)

    with store.connect() as connection:
        payload = created.model_copy(update={"spec_sha256": "f" * 64}).model_dump_json()
        connection.execute(
            """UPDATE watchlist_entries SET record_json=?, spec_sha256=?
               WHERE entry_id=?""",
            (payload, "f" * 64, created.entry.entry_id),
        )
    with pytest.raises(WatchlistStorageUnavailableError, match="fingerprint"):
        subject.get(created.entry.entry_id)


def test_denormalized_columns_cannot_disagree_with_record_json(tmp_path) -> None:
    subject, store = service(tmp_path)
    created = subject.create(spec("600519"))

    with store.connect() as connection:
        connection.execute(
            "UPDATE watchlist_entries SET role='core' WHERE entry_id=?",
            (created.entry.entry_id,),
        )

    with pytest.raises(WatchlistStorageUnavailableError, match="metadata"):
        subject.get(created.entry.entry_id)


def test_database_constraints_block_invalid_revision(tmp_path) -> None:
    subject, store = service(tmp_path)
    created = subject.create(spec("600519"))

    # The database check itself proves malformed metadata cannot be committed.
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as connection:
            connection.execute(
                "UPDATE watchlist_entries SET revision=0 WHERE entry_id=?",
                (created.entry.entry_id,),
            )
