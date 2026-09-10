from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

from custom_model.domain.models import DataEnvelope, DataQuery


def compute_market_bar_content_sha256(envelope: DataEnvelope) -> str:
    """Deterministic hash of cache payload-bearing market data content."""

    payload = {
        "instrument": envelope.instrument.model_dump(mode="json"),
        "timeframe": envelope.timeframe.value,
        "purpose": envelope.purpose.value,
        "adjustment": envelope.adjustment.value,
        "provider": envelope.provider,
        "source_chain": envelope.source_chain,
        "bars": [bar.model_dump(mode="json") for bar in envelope.bars],
        "as_of": envelope.as_of.isoformat(),
        "fetched_at": envelope.fetched_at.isoformat(),
        "last_bar_at": (
            envelope.last_bar_at.isoformat() if envelope.last_bar_at is not None else None
        ),
        "mode": envelope.mode.value,
        "quality": envelope.quality.value,
        "timezone": envelope.timezone,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


@runtime_checkable
class MarketBarStore(Protocol):
    def save(self, envelope: DataEnvelope) -> None:
        """Persist an admitted bar envelope and its metadata."""

    def query(self, query: DataQuery) -> list[DataEnvelope]:
        """Return cached envelopes that match and are admissible for the query."""


__all__ = ["MarketBarStore", "compute_market_bar_content_sha256"]
