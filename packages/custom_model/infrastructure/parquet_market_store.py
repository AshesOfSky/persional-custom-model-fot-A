from __future__ import annotations

import os
import json
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import mkstemp
import uuid
from tempfile import NamedTemporaryFile
from typing import Any

import pandas as pd

from custom_model.application.market_bar_store import (
    MarketBarStore,
    compute_market_bar_content_sha256,
)
from custom_model.domain.models import Bar, DataEnvelope, DataQuery


def _safe_component(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    if not normalized or normalized in {".", ".."}:
        raise ValueError("unsafe storage path component")
    return normalized


def _query_upper_bound(query: DataQuery) -> Any:
    return min(query.as_of, query.end) if query.end else query.as_of


def _query_partition(root: Path, query: DataQuery) -> Path:
    return (
        root
        / "market"
        / f"market={_safe_component(query.instrument.market.value)}"
        / f"asset_type={_safe_component(query.instrument.asset_type.value)}"
        / f"symbol={_safe_component(query.instrument.symbol)}"
        / f"timeframe={_safe_component(query.timeframe.value)}"
        / f"purpose={_safe_component(query.purpose.value)}"
        / f"adjustment={_safe_component(query.adjustment.value)}"
    )


class ParquetMarketStore:
    """Parquet-based market-bar cache store with strict envelope metadata checks."""

    def __init__(
        self,
        root: str | Path,
        *,
        partition_ttl_days: float = 14.0,
        max_records_per_partition: int = 8,
    ) -> None:
        self._root = Path(root).resolve()
        self._partition_ttl = timedelta(days=partition_ttl_days)
        self._max_records_per_partition = max_records_per_partition
        self._lock_poll_seconds = 0.05
        self._lock_timeout_seconds = 8.0

    def save(self, envelope: DataEnvelope) -> Path:
        if not envelope.bars:
            raise ValueError("cannot persist an empty envelope")
        if envelope.source_chain is None:
            raise ValueError("cache envelope source_chain is missing")

        partition = _query_partition(self._root, _query_from_envelope(envelope))
        partition.mkdir(parents=True, exist_ok=True)
        filename = f"bars-{envelope.request_id}"
        parquet_path = partition / f"{filename}.parquet"
        manifest_path = partition / f"{filename}.json"

        payload = envelope.model_dump(mode="json", exclude={"bars"})
        payload["content_sha256"] = compute_market_bar_content_sha256(envelope)

        dataframe = pd.DataFrame([bar.model_dump(mode="python") for bar in envelope.bars])
        with self._lock_partition(partition):
            with NamedTemporaryFile(
                suffix=".parquet", dir=str(partition), delete=False
            ) as parquet_tmp:
                temp_parquet = Path(parquet_tmp.name)
            try:
                dataframe.to_parquet(temp_parquet, index=False)
                temp_manifest = _tmp_file(partition, suffix=".json")
                Path(temp_manifest).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temp_parquet.replace(parquet_path)
                Path(temp_manifest).replace(manifest_path)
                return manifest_path
            finally:
                if temp_parquet.exists():
                    temp_parquet.unlink(missing_ok=True)
                if "temp_manifest" in locals() and Path(temp_manifest).exists():
                    Path(temp_manifest).unlink(missing_ok=True)

    def query(self, query: DataQuery) -> list[DataEnvelope]:
        now_limit = _query_upper_bound(query)
        with self._lock_partition(_query_partition(self._root, query)):
            envelopes = [
                item
                for item in self._iter_envelopes(query, upper_bound=now_limit)
                if item.bars
            ]
        envelopes.sort(key=lambda item: item.fetched_at, reverse=True)
        return envelopes

    def cleanup(self, *, now: datetime | None = None) -> int:
        """Prune stale manifests and limit each partition size; return removed files."""

        now = now or datetime.now(timezone.utc)
        removed_count = 0
        for partition in self._discover_partitions():
            with self._lock_partition(partition):
                manifests = self._discover_manifests(partition)
                to_remove = {path for path, fetched_at in manifests if self._is_stale(fetched_at, now)}

                survivors = sorted(
                    [
                        (path, fetched_at)
                        for path, fetched_at in manifests
                        if path not in to_remove
                    ],
                    key=lambda item: item[1] or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )
                if len(survivors) > self._max_records_per_partition:
                    to_remove.update(path for path, _ in survivors[self._max_records_per_partition :])

                for manifest in sorted(to_remove):
                    removed_count += self._remove_entry(manifest)

                self._cleanup_temporary_artifacts(partition)
        return removed_count

    def _iter_envelopes(self, query: DataQuery, upper_bound: Any) -> list[DataEnvelope]:
        manifest_root = _query_partition(self._root, query)
        if not manifest_root.exists():
            return []

        candidates: list[DataEnvelope] = []
        for manifest_path in manifest_root.glob("*.json"):
            if manifest_path.name.endswith(".tmp"):
                continue
            try:
                envelope = self._load_envelope(manifest_path)
            except ValueError as exc:
                if str(exc).startswith("invalid cache manifest:"):
                    continue
                raise
            candidates.append(
                self._clip_to_query_window(self._query_envelope(query, envelope), query)
            )
        return [item for item in candidates if item is not None]

    def _load_envelope(self, manifest_path: Path) -> DataEnvelope:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid cache manifest: {manifest_path}") from exc

        content_sha = manifest.pop("content_sha256", None)
        if content_sha is None:
            raise ValueError(f"cache manifest missing content_sha256: {manifest_path}")

        parquet_path = manifest_path.with_suffix(".parquet")
        if not parquet_path.exists():
            raise ValueError(f"cache parquet missing for {manifest_path}")

        bars = self._load_bars_from_parquet(parquet_path)
        envelope_payload = manifest
        envelope_payload["bars"] = [bar.model_dump(mode="python") for bar in bars]
        envelope = DataEnvelope.model_validate(envelope_payload)

        if compute_market_bar_content_sha256(envelope) != content_sha:
            raise ValueError(
                f"cache content hash mismatch for {manifest_path.name}"
            )
        return envelope

    @staticmethod
    def _query_envelope(query: DataQuery, envelope: DataEnvelope) -> DataEnvelope:
        if envelope.instrument != query.instrument:
            raise ValueError("cache instrument mismatch")
        if envelope.timeframe != query.timeframe:
            raise ValueError("cache timeframe mismatch")
        if envelope.purpose != query.purpose:
            raise ValueError("cache purpose mismatch")
        if envelope.adjustment != query.adjustment:
            raise ValueError("cache adjustment mismatch")
        return envelope

    @staticmethod
    def _clip_to_query_window(envelope: DataEnvelope, query: DataQuery) -> DataEnvelope | None:
        upper = min(query.as_of, query.end) if query.end else query.as_of
        bars = [
            bar
            for bar in envelope.bars
            if (query.start is None or bar.timestamp >= query.start)
            and bar.timestamp <= upper
        ]
        if not bars:
            return None
        return envelope.model_copy(
            update={
                "bars": bars,
                "last_bar_at": bars[-1].timestamp,
            }
        )

    @staticmethod
    def _load_bars_from_parquet(path: Path) -> list[Bar]:
        frame = pd.read_parquet(path)
        if frame.empty:
            raise ValueError(f"cache parquet is empty: {path}")
        if "timestamp" not in frame.columns:
            raise ValueError(f"cache parquet missing timestamp column: {path}")

        required = {"open", "high", "low", "close", "volume"}
        missing = sorted(required.difference(set(frame.columns)))
        if missing:
            raise ValueError(
                f"cache parquet missing required columns: {', '.join(missing)}"
            )

        normalized = frame.sort_values("timestamp").copy()
        bars: list[Bar] = []
        for _, row in normalized.iterrows():
            timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
            bars.append(
                Bar(
                    timestamp=timestamp,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    amount=(
                        None
                        if pd.isna(row["amount"])
                        else float(row["amount"])
                    )
                    if "amount" in row
                    else None,
                )
            )
        return bars

    def _discover_partitions(self) -> list[Path]:
        return list((self._root / "market").rglob("adjustment=*"))

    def _discover_manifests(self, partition: Path) -> list[tuple[Path, datetime | None]]:
        entries = []
        for manifest in partition.glob("bars-*.json"):
            if manifest.name.endswith(".tmp"):
                continue
            fetched_at = self._read_manifest_fetched_at(manifest)
            entries.append((manifest, fetched_at))
        return entries

    def _is_stale(self, fetched_at: datetime | None, now: datetime) -> bool:
        if fetched_at is None:
            return True
        return fetched_at < now - self._partition_ttl

    def _read_manifest_fetched_at(self, manifest: Path) -> datetime | None:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            raw = payload.get("fetched_at")
            if not raw:
                return None
            parsed = datetime.fromisoformat(raw)
            return (
                parsed
                if parsed.tzinfo is not None
                else parsed.replace(tzinfo=timezone.utc)
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return None

    def _remove_entry(self, manifest_path: Path) -> int:
        removed = 0
        parquet_path = manifest_path.with_suffix(".parquet")
        if manifest_path.exists():
            manifest_path.unlink(missing_ok=True)
            removed += 1
        if parquet_path.exists():
            parquet_path.unlink(missing_ok=True)
            removed += 1
        return removed

    def _cleanup_temporary_artifacts(self, partition: Path) -> None:
        for tmp_file in partition.glob("*.tmp"):
            tmp_file.unlink(missing_ok=True)

    @contextmanager
    def _lock_partition(self, partition: Path):
        lock_path = partition / ".cache.lock"
        token = f"{os.getpid()}:{uuid.uuid4()}"
        deadline = time.monotonic() + self._lock_timeout_seconds
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(token)
                break
            except FileExistsError:
                if time.monotonic() > deadline:
                    self._clear_stale_lock(lock_path)
                    deadline = time.monotonic() + self._lock_timeout_seconds
                    continue
                time.sleep(self._lock_poll_seconds)

        try:
            yield
        finally:
            try:
                current = lock_path.read_text(encoding="utf-8")
            except OSError:
                current = ""
            if current == token:
                lock_path.unlink(missing_ok=True)

    def _clear_stale_lock(self, lock_path: Path) -> None:
        try:
            mtime = lock_path.stat().st_mtime
        except OSError:
            return
        if time.time() - mtime > max(
            2 * self._lock_timeout_seconds,
            self._lock_poll_seconds * 1000,
        ):
            lock_path.unlink(missing_ok=True)

def _query_from_envelope(envelope: DataEnvelope) -> DataQuery:
    return DataQuery(
        instrument=envelope.instrument,
        timeframe=envelope.timeframe,
        purpose=envelope.purpose,
        as_of=envelope.as_of,
        adjustment=envelope.adjustment,
    )


def _tmp_file(partition: Path, *, suffix: str) -> str:
    fd, path = mkstemp(suffix=suffix, dir=partition)
    os.close(fd)
    return path
