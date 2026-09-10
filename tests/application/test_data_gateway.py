from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from custom_model.application.data_gateway import DataGateway, DataUnavailableError
from custom_model.application.ports import ProviderCapabilities, ProviderHealth
from custom_model.infrastructure.providers.cached_market_provider import CachedMarketProvider
from custom_model.domain.models import (
    Adjustment,
    AssetType,
    Bar,
    DataEnvelope,
    DataMode,
    DataPurpose,
    DataQuery,
    Exchange,
    InstrumentId,
    Market,
    QualityStatus,
    Timeframe,
)


# A Tuesday inside the afternoon session keeps generic freshness tests focused
# on gateway routing rather than completed-session/weekend admission.
NOW = datetime(2026, 8, 11, 6, tzinfo=timezone.utc)
INSTRUMENT = InstrumentId(
    symbol="600519",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)


@pytest.mark.parametrize("purpose", [DataPurpose.RESEARCH, DataPurpose.REPORT])
def test_chart_daily_source_keeps_completed_bars_before_freshness_admission(purpose):
    before = datetime(2026, 9, 9, 16, tzinfo=timezone.utc)  # Sep 10 midnight CN
    previous = before - timedelta(days=1)
    bars = [Bar(timestamp=day, open=49, high=51, low=48, close=50, volume=1000) for day in (previous, before)]
    class Source:
        name = "real-daily-with-forming-tail"
        production_ready = True
        def supports(self, query): return True
        def fetch_bars(self, query):
            return DataEnvelope(instrument=query.instrument, timeframe=query.timeframe, purpose=query.purpose,
                bars=bars, provider=self.name, mode=DataMode.DELAYED, quality=QualityStatus.VALID,
                as_of=query.as_of, fetched_at=query.as_of, last_bar_at=before, timezone="Asia/Shanghai",
                adjustment=query.adjustment, freshness_seconds=0, request_id="forming-tail", source_chain=["test-source"])
    gateway = DataGateway([Source()])
    request = d1_query(purpose=purpose, as_of=before+timedelta(hours=11, minutes=25))
    result = gateway.fetch_bars(request)
    assert result.bars == bars[:1]
    assert result.last_bar_at == previous
    assert result.quality is QualityStatus.VALID
    assert result.provider == Source.name
    assert any("excluded 1 incomplete" in warning for warning in result.warnings)
    assert gateway.fetch_bars(request.model_copy(update={"as_of": before+timedelta(hours=15, minutes=1)})).bars == bars
    with pytest.raises(DataUnavailableError):
        gateway.fetch_bars(request.model_copy(update={"purpose": DataPurpose.SCREENER}))


def query(purpose: DataPurpose = DataPurpose.RESEARCH) -> DataQuery:
    return DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.M1,
        purpose=purpose,
        as_of=NOW,
        adjustment=Adjustment.FORWARD,
    )


def d1_query(
    *,
    purpose: DataPurpose = DataPurpose.RESEARCH,
    instrument: InstrumentId = INSTRUMENT,
    as_of: datetime | None = None,
    adjustment: Adjustment = Adjustment.NONE,
    start: datetime | None = None,
    end: datetime | None = None,
) -> DataQuery:
    # Keep D1 fixtures above the 15:00 Asia/Shanghai completed-session boundary to
    # avoid false "stale" rejections from the weekday-only daily completeness rule.
    return DataQuery(
        instrument=instrument,
        timeframe=Timeframe.D1,
        purpose=purpose,
        as_of=as_of or datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc),
        adjustment=adjustment,
        start=start,
        end=end,
    )


class FakeProvider:
    capabilities = ProviderCapabilities(
        markets=frozenset({Market.CN}),
        asset_types=frozenset({AssetType.STOCK}),
        timeframes=frozenset({Timeframe.M1}),
    )

    def __init__(
        self,
        name: str,
        *,
        age_seconds: int = 30,
        mode: DataMode = DataMode.LIVE,
        production_ready: bool = True,
        fails: bool = False,
        envelope_updates: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.age_seconds = age_seconds
        self.mode = mode
        self.production_ready = production_ready
        self.fails = fails
        self.envelope_updates = envelope_updates or {}

    def supports(self, request: DataQuery) -> bool:
        return True

    def fetch_bars(self, request: DataQuery) -> DataEnvelope:
        if self.fails:
            raise TimeoutError("provider timeout with private payload")
        timestamp = request.as_of - timedelta(seconds=self.age_seconds)
        current = Bar(
            timestamp=timestamp,
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=100,
        )
        envelope = DataEnvelope(
            instrument=request.instrument,
            timeframe=request.timeframe,
            purpose=request.purpose,
            bars=[current],
            provider=self.name,
            mode=self.mode,
            quality=QualityStatus.VALID,
            as_of=request.as_of,
            fetched_at=request.as_of,
            last_bar_at=timestamp,
            timezone="Asia/Shanghai",
            adjustment=Adjustment.FORWARD,
            freshness_seconds=self.age_seconds,
            source_chain=[self.name],
        )
        return envelope.model_copy(update=self.envelope_updates)

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, healthy=True, checked_at=NOW)


class RoutedProvider:
    def __init__(
        self,
        *,
        name: str,
        production_ready: bool = True,
        supports_query: bool = True,
        mode: DataMode = DataMode.DELAYED,
        bars: list[Bar] | None = None,
        fails: bool = False,
    ) -> None:
        self.name = name
        self.production_ready = production_ready
        self._supports_query = supports_query
        self._mode = mode
        self._bars = bars
        self._fails = fails
        self.calls: list[DataQuery] = []

    def supports(self, request: DataQuery) -> bool:
        return self._supports_query

    def fetch_bars(self, request: DataQuery) -> DataEnvelope:
        self.calls.append(request)
        if self._fails:
            raise RuntimeError("routed provider failed")
        bar_data = self._bars or [
            Bar(
                timestamp=request.as_of,
                open=10,
                high=11,
                low=9,
                close=10.5,
                volume=100,
                amount=100,
            )
        ]
        return DataEnvelope(
            instrument=request.instrument,
            timeframe=request.timeframe,
            purpose=request.purpose,
            bars=bar_data,
            provider=self.name,
            mode=self._mode,
            quality=QualityStatus.VALID,
            as_of=request.as_of,
            fetched_at=request.as_of,
            last_bar_at=bar_data[-1].timestamp,
            timezone="Asia/Shanghai",
            adjustment=request.adjustment,
            freshness_seconds=0,
            is_synthetic=False,
            warnings=(),
            source_chain=[self.name],
        )


def test_gateway_falls_through_and_records_sanitized_attempt() -> None:
    gateway = DataGateway(
        [FakeProvider("bad", fails=True), FakeProvider("good")]
    )
    result = gateway.fetch_bars(query())
    assert result.provider == "good"
    assert result.quality is QualityStatus.VALID
    assert result.warnings == ["bad: TimeoutError"]
    assert "private payload" not in " ".join(result.warnings)


def test_gateway_rejects_stale_minute_data_for_research() -> None:
    gateway = DataGateway([FakeProvider("stale", age_seconds=91)])
    with pytest.raises(DataUnavailableError, match="quality stale"):
        gateway.fetch_bars(query())


def test_gateway_rejects_fallback_mode_for_formal_workflow() -> None:
    gateway = DataGateway([FakeProvider("fallback", mode=DataMode.FALLBACK)])
    with pytest.raises(DataUnavailableError, match="mode fallback forbidden"):
        gateway.fetch_bars(query(DataPurpose.ALERT))


def test_gateway_prefers_daily_provider_for_cn_d1_queries() -> None:
    daily = RoutedProvider(name="eastmoney-daily")
    legacy = RoutedProvider(name="legacy-router")

    envelope = DataGateway((daily, legacy)).fetch_bars(d1_query())

    assert envelope.provider == "eastmoney-daily"
    assert legacy.calls == []


def test_gateway_falls_back_to_legacy_when_earlier_d1_provider_fails() -> None:
    daily = RoutedProvider(name="eastmoney-daily", fails=True)
    legacy = RoutedProvider(name="legacy-router")

    envelope = DataGateway((daily, legacy)).fetch_bars(d1_query())

    assert envelope.provider == "legacy-router"
    assert daily.calls == [d1_query()]
    assert legacy.calls == [d1_query()]


def test_gateway_routes_screener_d1_queries_to_legacy_if_daily_declines() -> None:
    daily = RoutedProvider(name="eastmoney-daily", supports_query=False)
    legacy = RoutedProvider(name="legacy-router")

    envelope = DataGateway((daily, legacy)).fetch_bars(
        d1_query(purpose=DataPurpose.SCREENER)
    )

    assert envelope.provider == "legacy-router"
    assert daily.calls == []
    assert legacy.calls == [d1_query(purpose=DataPurpose.SCREENER)]


def test_gateway_does_not_use_unadmitted_provider_for_research() -> None:
    gateway = DataGateway([FakeProvider("experimental", production_ready=False)])
    with pytest.raises(DataUnavailableError, match="experimental/not admitted"):
        gateway.fetch_bars(query())


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"as_of": NOW - timedelta(days=1)}, "as_of mismatch"),
        ({"adjustment": Adjustment.NONE}, "adjustment mismatch"),
        ({"timezone": "Bad/Zone"}, "invalid timezone"),
        ({"source_chain": []}, "unknown source"),
        ({"source_chain": ["unknown"]}, "unknown source"),
    ],
)
def test_gateway_rejects_unbound_or_unknown_envelope_evidence(
    updates: dict[str, Any],
    message: str,
) -> None:
    gateway = DataGateway([FakeProvider("forged", envelope_updates=updates)])

    with pytest.raises(DataUnavailableError, match=message):
        gateway.fetch_bars(query())


def test_gateway_rejects_bars_outside_the_point_in_time_query_window() -> None:
    request = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.M1,
        purpose=DataPurpose.RESEARCH,
        adjustment=Adjustment.FORWARD,
        start=NOW - timedelta(minutes=5),
        end=NOW - timedelta(minutes=1),
        as_of=NOW,
    )
    before = Bar(
        timestamp=request.start - timedelta(seconds=1),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
    )
    after = Bar(
        timestamp=request.end + timedelta(seconds=1),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
    )

    with pytest.raises(DataUnavailableError, match="bar before query start"):
        DataGateway(
            [
                FakeProvider(
                    "before",
                    envelope_updates={"bars": [before], "last_bar_at": before.timestamp},
                )
            ]
        ).fetch_bars(request)
    with pytest.raises(DataUnavailableError, match="bar after query upper bound"):
        DataGateway(
            [
                FakeProvider(
                    "after",
                    envelope_updates={"bars": [after], "last_bar_at": after.timestamp},
                )
            ]
        ).fetch_bars(request)

    future = Bar(
        timestamp=NOW + timedelta(seconds=1),
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
    )
    with pytest.raises(DataUnavailableError, match="bar after query upper bound"):
        DataGateway(
            [
                FakeProvider(
                    "future",
                    envelope_updates={
                        "bars": [future],
                        "last_bar_at": future.timestamp,
                    },
                )
            ]
        ).fetch_bars(query())


def test_backtest_accepts_a_bound_historical_bar_at_the_requested_end() -> None:
    request = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.M1,
        purpose=DataPurpose.BACKTEST,
        adjustment=Adjustment.FORWARD,
        start=NOW - timedelta(days=2),
        end=NOW - timedelta(days=1),
        as_of=NOW,
    )
    historical = Bar(
        timestamp=request.end,
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
    )

    result = DataGateway(
        [
            FakeProvider(
                "historical",
                mode=DataMode.DELAYED,
                envelope_updates={
                    "bars": [historical],
                    "last_bar_at": historical.timestamp,
                },
            )
        ]
    ).fetch_bars(request)

    assert result.last_bar_at == request.end
    assert result.quality is QualityStatus.VALID


class RecordingStore:
    def __init__(self, queries: list[DataEnvelope] | None = None) -> None:
        self.saved: list[DataEnvelope] = []
        self.queries = list(queries or [])
        self.queried = 0

    def save(self, envelope: DataEnvelope) -> None:
        self.saved.append(envelope)

    def query(self, _query: DataQuery) -> list[DataEnvelope]:
        self.queried += 1
        return list(self.queries)


def stale_envelope(
    request: DataQuery,
    *,
    provider: str = "cached-provider",
    age_seconds: int = 120,
    mode: DataMode = DataMode.LIVE,
) -> DataEnvelope:
    timestamp = request.as_of - timedelta(seconds=age_seconds)
    bar = Bar(
        timestamp=timestamp,
        open=10,
        high=11,
        low=9,
        close=10.5,
        volume=100,
    )
    return DataEnvelope(
        instrument=request.instrument,
        timeframe=request.timeframe,
        purpose=request.purpose,
        bars=[bar],
        provider=provider,
        mode=mode,
        quality=QualityStatus.VALID,
        as_of=request.as_of,
        fetched_at=request.as_of,
        last_bar_at=timestamp,
        timezone="Asia/Shanghai",
        adjustment=request.adjustment,
        freshness_seconds=age_seconds,
        source_chain=[provider],
    )


def test_gateway_writes_admitted_envelope_to_market_store() -> None:
    store = RecordingStore()
    gateway = DataGateway((FakeProvider("fresh"),), market_bar_store=store)

    envelope = gateway.fetch_bars(query())

    assert envelope.provider == "fresh"
    assert store.saved == [envelope]


def test_gateway_does_not_write_rejected_envelope_to_market_store() -> None:
    store = RecordingStore()
    gateway = DataGateway((FakeProvider("stale", age_seconds=91),), market_bar_store=store)

    with pytest.raises(DataUnavailableError, match="quality stale"):
        gateway.fetch_bars(query())

    assert store.saved == []


def test_gateway_prefers_fresh_cache_and_falls_back_on_stale_cache() -> None:
    request = query()
    store = RecordingStore(queries=[stale_envelope(request, age_seconds=180)])
    gateway = DataGateway(
        (
            CachedMarketProvider(store),
            FakeProvider("fresh", age_seconds=30),
        )
    )

    envelope = gateway.fetch_bars(request)

    assert envelope.provider == "fresh"
    assert "cached-market: quality stale" in envelope.warnings
    assert store.queried == 1


def test_cache_reuses_an_earlier_capture_at_the_current_query_as_of() -> None:
    request = query()
    captured_request = request.model_copy(
        update={"as_of": request.as_of - timedelta(seconds=30)}
    )
    cached = stale_envelope(captured_request, age_seconds=30)
    store = RecordingStore(queries=[cached])

    envelope = DataGateway((CachedMarketProvider(store),)).fetch_bars(request)

    assert envelope.as_of == request.as_of
    assert envelope.fetched_at == captured_request.as_of
    assert envelope.last_bar_at == request.as_of - timedelta(seconds=60)
    assert "cache record reused for current query as_of" in envelope.warnings


def test_gateway_does_not_write_a_cache_hit_back_into_the_same_store() -> None:
    request = query()
    store = RecordingStore(queries=[stale_envelope(request, age_seconds=30)])
    gateway = DataGateway(
        (CachedMarketProvider(store),),
        market_bar_store=store,
    )

    envelope = gateway.fetch_bars(request)

    assert envelope.provider == "cached-provider"
    assert store.saved == []


def test_cache_rejects_a_capture_obtained_after_the_query_as_of() -> None:
    request = query()
    future_request = request.model_copy(
        update={"as_of": request.as_of + timedelta(seconds=30)}
    )
    future_capture = stale_envelope(future_request, age_seconds=30)
    store = RecordingStore(queries=[future_capture])

    with pytest.raises(DataUnavailableError, match="RuntimeError"):
        DataGateway((CachedMarketProvider(store),)).fetch_bars(request)


def test_stale_cache_cannot_satisfy_formal_research() -> None:
    request = query()
    store = RecordingStore(queries=[stale_envelope(request, age_seconds=180)])
    gateway = DataGateway((CachedMarketProvider(store),))

    with pytest.raises(DataUnavailableError, match="quality stale"):
        gateway.fetch_bars(request)
    assert store.queried == 1


def test_production_cache_rejects_non_admitted_legacy_provenance() -> None:
    request = query()
    legacy = stale_envelope(request, age_seconds=30).model_copy(
        update={"provider": "baostock", "source_chain": ["baostock"]}
    )
    store = RecordingStore(queries=[legacy])
    gateway = DataGateway(
        (
            CachedMarketProvider(
                store,
                admitted_source_prefixes=("eastmoney:", "sse:", "szse:"),
            ),
        )
    )

    with pytest.raises(DataUnavailableError, match="RuntimeError"):
        gateway.fetch_bars(request)
    assert store.queried == 1


def test_production_cache_selects_an_admitted_candidate_when_legacy_cache_coexists() -> None:
    request = query()
    legacy = stale_envelope(request, age_seconds=30).model_copy(
        update={"provider": "baostock", "source_chain": ["baostock"]}
    )
    admitted = stale_envelope(
        request,
        provider="aicubes-daily",
        age_seconds=30,
    ).model_copy(update={"source_chain": ["aicubes:board-daily"]})
    store = RecordingStore(queries=[legacy, admitted])
    gateway = DataGateway(
        (
            CachedMarketProvider(
                store,
                admitted_source_prefixes=("aicubes:",),
            ),
        )
    )

    envelope = gateway.fetch_bars(request)

    assert envelope.provider == "aicubes-daily"
    assert envelope.source_chain == ["aicubes:board-daily", "cache-provider"]
