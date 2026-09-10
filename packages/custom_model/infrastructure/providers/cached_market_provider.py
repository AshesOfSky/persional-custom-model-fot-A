from __future__ import annotations

from datetime import datetime, timezone

from custom_model.application.market_bar_store import MarketBarStore
from custom_model.application.ports import ProviderCapabilities, ProviderHealth
from custom_model.domain.models import (
    AssetType,
    DataEnvelope,
    DataPurpose,
    DataQuery,
    Market,
    Timeframe,
)


class CachedMarketProvider:
    """Cache-first provider that only serves data through verified parquet manifests."""

    name = "cached-market"
    production_ready = True
    capabilities = ProviderCapabilities(
        markets=frozenset(
            {
                Market.CN,
                Market.HK,
                Market.US,
                Market.GLOBAL,
            }
        ),
        asset_types=frozenset(
            {
                AssetType.STOCK,
                AssetType.ETF,
                AssetType.INDEX,
                AssetType.SECTOR_INDEX,
                AssetType.FUND,
                AssetType.FUTURES,
                AssetType.FX,
                AssetType.UNKNOWN,
            }
        ),
        timeframes=frozenset(
            {
                Timeframe.M1,
                Timeframe.M5,
                Timeframe.M15,
                Timeframe.M30,
                Timeframe.H1,
                Timeframe.D1,
                Timeframe.W1,
                Timeframe.MO1,
            }
        ),
        max_symbols_per_request=1,
    )

    def __init__(
        self,
        store: MarketBarStore,
        *,
        admitted_source_prefixes: tuple[str, ...] | None = None,
    ) -> None:
        if store is None:
            raise TypeError("cache store cannot be None")
        if admitted_source_prefixes is not None and any(
            not isinstance(prefix, str) or not prefix
            for prefix in admitted_source_prefixes
        ):
            raise ValueError("admitted cache source prefixes must be non-empty text")
        self._store = store
        self._admitted_source_prefixes = admitted_source_prefixes

    def supports(self, query: DataQuery) -> bool:
        return True

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        if query.purpose is DataPurpose.DEMO:
            raise RuntimeError("cache provider does not serve demo mode")

        try:
            candidates = self._store.query(query)
        except Exception as exc:
            raise RuntimeError("cache read failed") from exc

        if not candidates:
            raise RuntimeError("cache miss")

        time_eligible = [
            item
            for item in candidates
            if item.as_of <= query.as_of and item.fetched_at <= query.as_of
        ]
        if not time_eligible:
            raise RuntimeError("cache miss at or before query as_of")

        eligible = [item for item in time_eligible if self._source_is_admitted(item)]
        if not eligible:
            raise RuntimeError("cache provenance is not production-admitted")

        first = max(eligible, key=lambda item: item.fetched_at)
        warnings = list(first.warnings)
        source_chain = list(first.source_chain)
        if "cache-provider" not in source_chain:
            source_chain.append("cache-provider")
        return first.model_copy(
            update={
                "purpose": query.purpose,
                "as_of": query.as_of,
                "source_chain": source_chain,
                "warnings": list(
                    dict.fromkeys(
                        [
                            *warnings,
                            "cache entry used after verified manifest validation",
                            "cache record reused for current query as_of",
                        ]
                    )
                ),
            }
        )

    def _source_is_admitted(self, envelope: DataEnvelope) -> bool:
        if self._admitted_source_prefixes is None:
            return True
        tokens = [envelope.provider, *envelope.source_chain]
        return any(
            token.startswith(self._admitted_source_prefixes)
            for token in tokens
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider=self.name,
            healthy=True,
            checked_at=datetime.now(timezone.utc),
            production_ready=self.production_ready,
            message="cache-backed market-bar lookup service",
        )
