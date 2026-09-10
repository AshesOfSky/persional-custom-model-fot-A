from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import math

import pytest

from custom_model.application.market_view import (
    CORE_INDICATOR_PROFILE,
    MarketBarsViewService,
    MarketViewInputError,
)
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


UTC = timezone.utc
START = datetime(2025, 1, 1, tzinfo=UTC)
INSTRUMENT = InstrumentId(
    symbol="603259.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)


def envelope(*, fetched_at: datetime = START, request_id: str = "request-a") -> DataEnvelope:
    bars = []
    for index in range(400):
        close = 40.0 + index * 0.08 + math.sin(index / 7.0)
        bars.append(
            Bar(
                timestamp=START + timedelta(days=index),
                open=close - 0.2,
                high=close + 0.7,
                low=close - 0.8,
                close=close,
                volume=1_000_000.0 + index * 1_000,
                amount=close * (1_000_000.0 + index * 1_000),
            )
        )
    return DataEnvelope(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        bars=bars,
        provider="stub-market",
        mode=DataMode.DELAYED,
        quality=QualityStatus.VALID,
        as_of=bars[-1].timestamp,
        fetched_at=fetched_at,
        last_bar_at=bars[-1].timestamp,
        timezone="Asia/Shanghai",
        adjustment=Adjustment.FORWARD,
        freshness_seconds=0,
        request_id=request_id,
        source_chain=["stub-market"],
    )


class StubGateway:
    def __init__(self, data: DataEnvelope) -> None:
        self.data = data
        self.calls: list[DataQuery] = []

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        self.calls.append(query)
        return self.data


def test_market_view_maps_explicit_native_finite_core_series() -> None:
    data = envelope()
    service = MarketBarsViewService(StubGateway(data))

    view = service.build_view(data)

    assert view.schema_version == "market-bars-view/v1"
    assert view.indicator_profile == CORE_INDICATOR_PROFILE
    assert not isinstance(view.indicators, Mapping)
    series = (
        view.indicators.ema.ema8,
        view.indicators.ema.ema13,
        view.indicators.ema.ema21,
        view.indicators.ema.ema55,
        view.indicators.ema.ema144,
        view.indicators.ema.ema169,
        view.indicators.ema.ema288,
        view.indicators.ema.ema338,
        view.indicators.macd.dif,
        view.indicators.macd.dea,
        view.indicators.macd.hist,
        view.indicators.bollinger.mid,
        view.indicators.bollinger.upper,
        view.indicators.bollinger.lower,
        view.indicators.kdj.k,
        view.indicators.kdj.d,
        view.indicators.kdj.j,
        view.indicators.rsi,
        view.indicators.atr,
    )
    assert all(item.points for item in series)
    assert all(
        type(point.value) is float and math.isfinite(point.value)
        for item in series
        for point in item.points
    )
    assert all(
        isinstance(point.timestamp, datetime) and point.timestamp.tzinfo is not None
        for item in series
        for point in item.points
    )


def test_market_view_uses_legacy_indicator_formulas_including_full_ema_tunnel() -> None:
    data = envelope()
    view = MarketBarsViewService(StubGateway(data)).build_view(data)

    from modules.technical import add_all_indicators

    frame = MarketBarsViewService.frame_from_envelope(data)
    expected = add_all_indicators(frame)
    close = expected["Close"]

    assert view.indicators.ema.ema8.points[-1].value == pytest.approx(expected["EMA_8"].iloc[-1])
    assert view.indicators.ema.ema144.points[-1].value == pytest.approx(expected["EMA_144"].iloc[-1])
    assert view.indicators.ema.ema169.points[-1].value == pytest.approx(
        close.ewm(span=169, adjust=False).mean().iloc[-1]
    )
    assert view.indicators.ema.ema288.points[-1].value == pytest.approx(
        close.ewm(span=288, adjust=False).mean().iloc[-1]
    )
    assert view.indicators.ema.ema338.points[-1].value == pytest.approx(
        close.ewm(span=338, adjust=False).mean().iloc[-1]
    )
    assert view.indicators.macd.dif.points[-1].value == pytest.approx(expected["MACD"].iloc[-1])
    assert view.indicators.macd.dea.points[-1].value == pytest.approx(expected["MACD_signal"].iloc[-1])
    assert view.indicators.macd.hist.points[-1].value == pytest.approx(expected["MACD_hist"].iloc[-1])
    assert view.indicators.bollinger.mid.points[-1].value == pytest.approx(expected["BB_middle"].iloc[-1])
    assert view.indicators.kdj.j.points[-1].value == pytest.approx(expected["KDJ_J"].iloc[-1])
    assert view.indicators.rsi.points[-1].value == pytest.approx(expected["RSI"].iloc[-1])
    assert view.indicators.atr.points[-1].value == pytest.approx(expected["ATR"].iloc[-1])


def test_fingerprint_is_stable_for_same_market_data_and_profile() -> None:
    first = envelope(fetched_at=START, request_id="request-a")
    second = envelope(fetched_at=START + timedelta(minutes=5), request_id="request-b").model_copy(
        update={
            "provider": "local-cache",
            "mode": DataMode.CACHE,
            "quality": QualityStatus.VALID,
            "source_chain": ["local-cache", "upstream:test-provider"],
        }
    )
    service = MarketBarsViewService(StubGateway(first))

    assert service.build_view(first).data_fingerprint == service.build_view(second).data_fingerprint
    assert service.build_view(first).data_fingerprint.startswith("sha256:")


def test_fingerprint_changes_when_market_content_changes() -> None:
    first = envelope()
    changed_bar = first.bars[-1].model_copy(update={"close": first.bars[-1].close + 0.01})
    second = first.model_copy(update={"bars": [*first.bars[:-1], changed_bar]})
    service = MarketBarsViewService(StubGateway(first))

    assert service.build_view(first).data_fingerprint != service.build_view(second).data_fingerprint


def test_query_fetches_once_and_preserves_the_envelope() -> None:
    data = envelope()
    gateway = StubGateway(data)
    service = MarketBarsViewService(gateway)
    query = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        adjustment=Adjustment.FORWARD,
        as_of=data.as_of,
    )

    view = service.query(query)

    assert gateway.calls == [query]
    assert view.data is data


def test_non_finite_source_bars_are_rejected_before_serialization() -> None:
    data = envelope()
    invalid_bar = data.bars[-1].model_copy(update={"volume": float("inf")})
    invalid = data.model_copy(update={"bars": [*data.bars[:-1], invalid_bar]})

    with pytest.raises(MarketViewInputError, match="non-finite"):
        MarketBarsViewService(StubGateway(invalid)).build_view(invalid)
