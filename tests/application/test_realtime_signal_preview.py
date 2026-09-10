from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

from custom_model.application.realtime_signal_preview import (
    RealtimeSignalPreviewService,
)
from custom_model.domain.models import (
    Adjustment,
    AssetType,
    Bar,
    DataEnvelope,
    DataMode,
    DataPurpose,
    Exchange,
    InstrumentId,
    Market,
    QualityStatus,
    QuoteEnvelope,
    QuoteSessionStatus,
    QuoteTemporalMode,
    SignalStatus,
    Timeframe,
)


UTC = timezone.utc
COMPLETED_AT = datetime(2026, 9, 2, 7, 0, tzinfo=UTC)
QUOTE_AT = datetime(2026, 9, 3, 2, 30, tzinfo=UTC)
INSTRUMENT = InstrumentId(
    symbol="603259.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)


def completed_daily_envelope() -> DataEnvelope:
    bars = [
        Bar(
            timestamp=COMPLETED_AT - timedelta(days=2 - index),
            open=48.0 + index,
            high=49.0 + index,
            low=47.5 + index,
            close=48.5 + index,
            volume=100_000 + index * 10_000,
            amount=(48.5 + index) * (100_000 + index * 10_000),
        )
        for index in range(3)
    ]
    return DataEnvelope(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        bars=bars,
        provider="daily-provider",
        mode=DataMode.DELAYED,
        quality=QualityStatus.VALID,
        as_of=QUOTE_AT,
        fetched_at=QUOTE_AT,
        last_bar_at=bars[-1].timestamp,
        timezone="Asia/Shanghai",
        adjustment=Adjustment.FORWARD,
        freshness_seconds=(QUOTE_AT - bars[-1].timestamp).total_seconds(),
        source_chain=["daily:completed"],
        request_id="daily-request-001",
    )


def latest_quote(
    *,
    session_status: QuoteSessionStatus = QuoteSessionStatus.OPEN,
    quality: QualityStatus = QualityStatus.VALID,
) -> QuoteEnvelope:
    requested_at = QUOTE_AT - timedelta(seconds=1)
    return QuoteEnvelope(
        instrument=INSTRUMENT,
        purpose=DataPurpose.RESEARCH,
        display_name="药明康德",
        provider="quote-provider",
        mode=DataMode.LIVE if session_status is QuoteSessionStatus.OPEN else DataMode.DELAYED,
        quality=quality,
        temporal_mode=QuoteTemporalMode.LATEST,
        requested_at=requested_at,
        as_of=QUOTE_AT,
        fetched_at=QUOTE_AT,
        data_time=QUOTE_AT,
        provider_updated_at=QUOTE_AT,
        data_time_basis="provider_timestamp",
        timezone="Asia/Shanghai",
        currency="CNY",
        freshness_seconds=0,
        session_status=session_status,
        trade_status_code=19,
        trade_status_verified=True,
        last_price=52.2,
        previous_close=50.5,
        open=50.8,
        high=52.8,
        low=50.6,
        change=1.7,
        change_percent=1.7 / 50.5 * 100,
        volume_lots=280_000,
        amount=14_500_000,
        is_synthetic=False,
        source_chain=["quote:native"],
        request_id="quote-request-001",
    )


def signal_row(bar_index: int, date: str, *, kind: str = "buy") -> dict[str, Any]:
    return {
        "bar_index": bar_index,
        "date": date,
        "price": 50.6 if kind == "buy" else 52.8,
        "close": 52.2,
        "strength": "强",
        "confidence_level": "强",
        "confidence": 78.4,
        "confirmations": ["MACD金叉", "放量(>1.5xMA5)", "OBV底背离累积"],
        "confidence_components": {
            "version": "signal-evidence-v2",
            "semantics": "signal_evidence_score_not_probability",
            "independent_bucket_count": 3,
            "bucket_score": 72.0,
            "directional_trend_score": 88.0,
            "trend_evidence_status": "available",
        },
    }


def test_preview_appends_native_quote_and_keeps_only_last_bar_signals() -> None:
    completed = completed_daily_envelope()
    captured: dict[str, pd.DataFrame] = {}

    def indicators(frame: pd.DataFrame) -> pd.DataFrame:
        captured["frame"] = frame.copy()
        return frame

    def signals(frame: pd.DataFrame, _analysis: dict, _levels: dict) -> dict:
        last_index = len(frame) - 1
        return {
            "buy_signals": [
                signal_row(last_index - 1, "2026-09-02"),
                signal_row(last_index, "2026-09-03"),
            ],
            "sell_signals": [
                signal_row(last_index - 2, "2026-09-01", kind="sell")
            ],
        }

    result = RealtimeSignalPreviewService(
        indicator_builder=indicators,
        level_builder=lambda _frame: {},
        signal_builder=signals,
    ).build(completed, latest_quote())

    appended = captured["frame"].iloc[-1]
    assert appended.to_dict() == {
        "Open": 50.8,
        "High": 52.8,
        "Low": 50.6,
        "Close": 52.2,
        "Volume": 280_000.0,
        "Amount": 14_500_000.0,
    }
    assert result.available is True
    assert result.status is SignalStatus.PROVISIONAL
    assert result.bar_time == "2026-09-03"
    assert result.completed_through == COMPLETED_AT
    assert result.daily_request_id == "daily-request-001"
    assert result.quote_request_id == "quote-request-001"
    assert len(result.composite_signals["buy_signals"]) == 1
    assert result.composite_signals["buy_signals"][0]["bar_index"] == 3
    assert result.composite_signals["sell_signals"] == ()
    assert len(completed.bars) == 3
    assert completed.last_bar_at == COMPLETED_AT


def test_preview_is_available_without_fabricating_a_signal() -> None:
    result = RealtimeSignalPreviewService(
        indicator_builder=lambda frame: frame,
        level_builder=lambda _frame: {},
        signal_builder=lambda _frame, _analysis, _levels: {
            "buy_signals": [],
            "sell_signals": [],
        },
    ).build(completed_daily_envelope(), latest_quote())

    assert result.available is True
    assert result.composite_signals == {"buy_signals": (), "sell_signals": ()}
    assert any("two independent evidence buckets" in item for item in result.warnings)


def test_preview_does_not_run_outside_an_eligible_session() -> None:
    called = False

    def indicators(frame: pd.DataFrame) -> pd.DataFrame:
        nonlocal called
        called = True
        return frame

    result = RealtimeSignalPreviewService(
        indicator_builder=indicators,
    ).build(
        completed_daily_envelope(),
        latest_quote(session_status=QuoteSessionStatus.PRE_OPEN),
    )

    assert result.available is False
    assert result.composite_signals == {"buy_signals": (), "sell_signals": ()}
    assert called is False
    assert any("session is not eligible" in item for item in result.warnings)


def test_preview_stops_when_completed_daily_data_already_covers_quote_date() -> None:
    completed = completed_daily_envelope().model_copy(
        update={
            "bars": [
                *completed_daily_envelope().bars,
                Bar(
                    timestamp=datetime(2026, 9, 3, 7, 0, tzinfo=UTC),
                    open=50.8,
                    high=52.8,
                    low=50.6,
                    close=52.2,
                    volume=280_000,
                    amount=14_500_000,
                ),
            ],
            "last_bar_at": datetime(2026, 9, 3, 7, 0, tzinfo=UTC),
            "freshness_seconds": 0,
        }
    )

    result = RealtimeSignalPreviewService().build(completed, latest_quote())

    assert result.available is False
    assert any("already covers the quote date" in item for item in result.warnings)


@pytest.mark.parametrize("session,basis,close_hour,close_minute", [
    (QuoteSessionStatus.MIDDAY_BREAK, "cn_midday_close", 3, 30),
    (QuoteSessionStatus.AFTER_CLOSE, "cn_session_close", 7, 0),
])
def test_session_close_snapshot_retains_original_provisional_confidence(session, basis, close_hour, close_minute):
    close_at = QUOTE_AT.replace(hour=close_hour, minute=close_minute)
    as_of = close_at + timedelta(minutes=30)
    completed = completed_daily_envelope().model_copy(update={"as_of": as_of, "fetched_at": as_of})
    quote = latest_quote().model_copy(update={"as_of": as_of, "requested_at": as_of-timedelta(seconds=1),
        "fetched_at": as_of, "data_time": close_at, "provider_updated_at": close_at,
        "session_status": session, "mode": DataMode.DELAYED, "quality": QualityStatus.STALE,
        "trade_status_verified": False, "data_time_basis": basis, "freshness_seconds": 1800})
    expected = signal_row(len(completed.bars), "2026-09-03")
    service = RealtimeSignalPreviewService(indicator_builder=lambda frame: frame,
        level_builder=lambda frame: {}, signal_builder=lambda frame, analysis, levels: {
            "buy_signals": [expected], "sell_signals": []})
    result = service.build(completed, quote)
    assert result.available and not result.formal_use_eligible
    assert result.composite_signals["buy_signals"] == (expected,)
    assert result.composite_signals["buy_signals"][0]["confidence"] == 78.4
    assert result.quote_quality is QualityStatus.STALE
    assert any("快照" in warning for warning in result.warnings)
    invalid_basis = service.build(completed, quote.model_copy(update={"data_time_basis": "provider_timestamp"}))
    assert not invalid_basis.available and not invalid_basis.composite_signals["buy_signals"]
    wrong_time = service.build(completed, quote.model_copy(update={"data_time": close_at-timedelta(minutes=1)}))
    assert not wrong_time.available


@pytest.mark.parametrize("quality,verified", [(QualityStatus.STALE, True), (QualityStatus.VALID, False)])
def test_open_session_still_requires_fresh_verified_quote(quality, verified):
    def forbidden(frame):
        raise AssertionError("unqualified open-session quote must not reach signal computation")
    result = RealtimeSignalPreviewService(indicator_builder=forbidden).build(
        completed_daily_envelope(), latest_quote().model_copy(update={"quality": quality,
            "mode": DataMode.DELAYED, "trade_status_verified": verified}))
    assert result.available is False
