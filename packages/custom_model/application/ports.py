from __future__ import annotations

from dataclasses import dataclass, field

from datetime import datetime

from typing import Protocol, runtime_checkable

from custom_model.domain.models import AssetType, DataEnvelope, DataQuery, FundamentalEnvelope, FundamentalQuery, InstrumentId, Market, QuoteBatchQuery, QuoteEnvelope, QuoteQuery, Timeframe

@dataclass(frozen=True)
class ProviderCapabilities:
    markets: frozenset[Market] = field(default_factory=frozenset)
    asset_types: frozenset[AssetType] = field(default_factory=frozenset)
    timeframes: frozenset[Timeframe] = field(default_factory=frozenset)
    max_symbols_per_request: int = 1
    supports_constituents: bool = False
    supports_historical_constituents: bool = False


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: datetime
    latency_ms: float | None = None
    message: str | None = None
    production_ready: bool = True


@dataclass(frozen=True)
class InstrumentSearchMatch:
    """Provider-neutral security search result used by application services."""

    instrument: InstrumentId
    display_name: str
    legacy_code: str
    source: str
    match_score: float = 0.0


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str
    capabilities: ProviderCapabilities
    production_ready: bool

    def supports(self, query: DataQuery) -> bool: ...

    def fetch_bars(self, query: DataQuery) -> DataEnvelope: ...

    def health(self) -> ProviderHealth: ...


@runtime_checkable
class InstrumentSearchProvider(Protocol):
    name: str

    def search(self, query: str, *, limit: int = 10) -> list[InstrumentSearchMatch]: ...


@runtime_checkable
class FundamentalDataProvider(Protocol):
    """Provider boundary for point-in-time fundamental evidence."""

    name: str
    production_ready: bool

    def supports(self, query: FundamentalQuery) -> bool: ...

    def fetch_fundamentals(self, query: FundamentalQuery) -> FundamentalEnvelope: ...

    def health(self) -> ProviderHealth: ...


@runtime_checkable
class QuoteDataProvider(Protocol):
    """Provider boundary for a native, source-timestamped market quote."""

    name: str
    production_ready: bool

    def supports(self, query: QuoteQuery) -> bool: ...

    def fetch_quote(self, query: QuoteQuery) -> QuoteEnvelope: ...

    def health(self) -> ProviderHealth: ...


@runtime_checkable
class BatchQuoteDataProvider(Protocol):
    """Provider boundary for one exact-route batch of native quote snapshots."""

    name: str
    production_ready: bool

    def supports(self, query: QuoteBatchQuery) -> bool: ...

    def fetch_quotes(self, query: QuoteBatchQuery) -> tuple[QuoteEnvelope, ...]: ...

    def health(self) -> ProviderHealth: ...


