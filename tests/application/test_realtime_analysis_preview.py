from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytz
import pytest

from custom_model.application.analysis_service import DecisionInputError
from custom_model.application.realtime_analysis_preview import (
    REALTIME_ANALYSIS_PREVIEW_SCHEMA_VERSION,
    RealtimeAnalysisPreviewService,
)
from custom_model.application.research import ResearchAnalysisUseCase
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
    QuoteEnvelope,
    QuoteSessionStatus,
    QuoteTemporalMode,
    SignalStatus,
    Timeframe,
)


CN = ZoneInfo("Asia/Shanghai")
OPEN_AS_OF = datetime(2026, 9, 3, 10, 30, tzinfo=CN)
MIDDAY_AS_OF = datetime(2026, 9, 3, 11, 30, tzinfo=CN)
AFTER_CLOSE_AS_OF = datetime(2026, 9, 3, 15, 0, tzinfo=CN)
INSTRUMENT = InstrumentId(
    symbol="603259.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)
OTHER_INSTRUMENT = InstrumentId(
    symbol="600519.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)


def make_query(as_of: datetime = OPEN_AS_OF) -> DataQuery:
    return DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        adjustment=Adjustment.FORWARD,
        as_of=as_of,
    )


def make_completed_envelope(
    query: DataQuery,
    *,
    include_quote_date: bool = False,
) -> DataEnvelope:
    bars = [
        Bar(
            timestamp=datetime(2026, 8, 31, 15, 0, tzinfo=CN),
            open=49.0,
            high=50.0,
            low=48.5,
            close=50.0,
            volume=100_000,
            amount=5_000_000,
        ),
        Bar(
            timestamp=datetime(2026, 9, 1, 15, 0, tzinfo=CN),
            open=50.0,
            high=51.0,
            low=49.5,
            close=51.0,
            volume=110_000,
            amount=5_610_000,
        ),
        Bar(
            timestamp=datetime(2026, 9, 2, 15, 0, tzinfo=CN),
            open=51.0,
            high=52.0,
            low=50.5,
            close=52.0,
            volume=120_000,
            amount=6_240_000,
        ),
    ]
    if include_quote_date:
        bars.append(
            Bar(
                timestamp=datetime(2026, 9, 3, 15, 0, tzinfo=CN),
                open=59.0,
                high=61.0,
                low=58.5,
                close=60.0,
                volume=130_000,
                amount=7_800_000,
            )
        )
    return DataEnvelope(
        instrument=query.instrument,
        timeframe=query.timeframe,
        purpose=query.purpose,
        bars=bars,
        provider="daily-provider",
        mode=DataMode.DELAYED,
        quality=QualityStatus.VALID,
        as_of=query.as_of,
        fetched_at=query.as_of,
        last_bar_at=bars[-1].timestamp,
        timezone="Asia/Shanghai",
        adjustment=query.adjustment,
        freshness_seconds=max(
            0.0, (query.as_of - bars[-1].timestamp).total_seconds()
        ),
        source_chain=["daily:completed"],
        request_id="daily-request-001",
    )


def make_quote(
    *,
    as_of: datetime = OPEN_AS_OF,
    data_time: datetime | None = None,
    instrument: InstrumentId = INSTRUMENT,
    session_status: QuoteSessionStatus = QuoteSessionStatus.OPEN,
    quality: QualityStatus = QualityStatus.VALID,
    mode: DataMode | None = None,
    trade_status_verified: bool = True,
) -> QuoteEnvelope:
    data_time = data_time or as_of
    if mode is None:
        mode = (
            DataMode.LIVE
            if session_status is QuoteSessionStatus.OPEN
            and quality is QualityStatus.VALID
            else DataMode.DELAYED
        )
    data_time_basis = {
        QuoteSessionStatus.OPEN: "provider_timestamp",
        QuoteSessionStatus.MIDDAY_BREAK: "cn_midday_close",
        QuoteSessionStatus.AFTER_CLOSE: "cn_session_close",
    }[session_status]
    previous_close = 58.0
    last_price = 60.0
    change = last_price - previous_close
    return QuoteEnvelope(
        instrument=instrument,
        purpose=DataPurpose.RESEARCH,
        display_name="药明康德" if instrument == INSTRUMENT else "贵州茅台",
        provider="quote-provider",
        mode=mode,
        quality=quality,
        temporal_mode=QuoteTemporalMode.LATEST,
        requested_at=as_of - timedelta(seconds=1),
        as_of=as_of,
        fetched_at=as_of,
        data_time=data_time,
        provider_updated_at=data_time,
        data_time_basis=data_time_basis,
        timezone="Asia/Shanghai",
        currency="CNY",
        freshness_seconds=(as_of - data_time).total_seconds(),
        session_status=session_status,
        trade_status_code=19,
        trade_status_verified=trade_status_verified,
        last_price=last_price,
        previous_close=previous_close,
        open=59.0,
        high=61.0,
        low=58.5,
        change=change,
        change_percent=change / previous_close * 100.0,
        volume_lots=130_000,
        amount=7_800_000,
        is_synthetic=False,
        source_chain=["quote:native"],
        request_id="quote-request-001",
    )


class StaticGateway:
    def __init__(self, envelope: DataEnvelope) -> None:
        self.envelope = envelope
        self.queries: list[DataQuery] = []

    def fetch_bars(self, query: DataQuery) -> DataEnvelope:
        self.queries.append(query)
        return self.envelope


def build_preview_service(
    completed: DataEnvelope,
) -> tuple[RealtimeAnalysisPreviewService, StaticGateway, list[pd.DataFrame]]:
    gateway = StaticGateway(completed)
    captured_frames: list[pd.DataFrame] = []

    def runner(
        frame: pd.DataFrame,
        _info: dict[str, Any],
        *,
        timeframe: str,
        benchmark_df: pd.DataFrame | None,
    ) -> dict[str, Any]:
        assert timeframe == "日线"
        assert benchmark_df is None
        captured_frames.append(frame.copy())
        last_close = float(frame["Close"].iloc[-1])
        return {
            "observed_last_close": last_close,
            "trend_strength_score": last_close,
            "buy_action_plan": {
                "buy_score": {
                    "total_score": last_close,
                    "dimensions": {
                        "latest_close": {
                            "label": "最新收盘",
                            "score": last_close,
                            "weight": 1.0,
                        }
                    },
                }
            },
        }

    use_case = ResearchAnalysisUseCase(
        gateway,
        indicator_builder=lambda frame: frame,
        analysis_runner=runner,
    )
    return RealtimeAnalysisPreviewService(use_case), gateway, captured_frames


def test_open_quote_appends_provisional_bar_and_reanalyzes_new_bar() -> None:
    query = make_query()
    completed = make_completed_envelope(query)
    original_query = query.model_dump(mode="python")
    original_completed = completed.model_dump(mode="python")
    original_bars = list(completed.bars)
    service, gateway, captured_frames = build_preview_service(completed)

    result = service.analyze(query, make_quote())

    assert result.schema_version == REALTIME_ANALYSIS_PREVIEW_SCHEMA_VERSION
    assert result.session_label == "盘中实时快照"
    assert result.advisory_only is True
    assert result.formal_use_eligible is False

    assert len(gateway.queries) == 1
    assert gateway.queries[0] == query
    assert len(captured_frames) == 2
    assert len(captured_frames[0]) == len(completed.bars)
    assert len(captured_frames[1]) == len(completed.bars) + 1
    assert captured_frames[0]["Close"].iloc[-1] == pytest.approx(52.0)
    assert captured_frames[1]["Close"].iloc[-1] == pytest.approx(60.0)

    assert result.completed.analysis["observed_last_close"] == pytest.approx(52.0)
    assert result.preview.analysis["observed_last_close"] == pytest.approx(60.0)
    assert result.completed.snapshot.evidence_score == pytest.approx(52.0)
    assert result.preview.snapshot.evidence_score == pytest.approx(60.0)
    assert result.preview.data.bars[-1].timestamp == OPEN_AS_OF
    assert result.preview.data.bars[-1].close == pytest.approx(60.0)
    assert result.preview.data.provider == "daily-provider+quote-provider:provisional"
    assert result.preview.data.last_bar_at == OPEN_AS_OF
    assert "盘中实时快照 appended as an unclosed daily bar" in result.preview.data.warnings

    assert result.completed.snapshot.bar_status is SignalStatus.CONFIRMED
    assert result.preview.snapshot.bar_status is SignalStatus.PROVISIONAL
    assert result.completed.data.model_dump(mode="python") == original_completed
    assert completed.model_dump(mode="python") == original_completed
    assert completed.bars == original_bars
    assert query.model_dump(mode="python") == original_query


def test_stale_1130_midday_latest_snapshot_is_allowed_without_verified_trade_status() -> None:
    query = make_query(MIDDAY_AS_OF)
    completed = make_completed_envelope(query)
    service, _gateway, captured_frames = build_preview_service(completed)
    quote = make_quote(
        as_of=MIDDAY_AS_OF,
        session_status=QuoteSessionStatus.MIDDAY_BREAK,
        quality=QualityStatus.STALE,
        mode=DataMode.DELAYED,
        trade_status_verified=False,
    )

    result = service.analyze(query, quote)

    assert result.session_label == "午间11:30快照"
    assert result.quote.quality is QualityStatus.STALE
    assert result.quote.trade_status_verified is False
    assert result.preview.data.quality is QualityStatus.STALE
    assert result.preview.snapshot.bar_status is SignalStatus.PROVISIONAL
    assert result.completed.snapshot.bar_status is SignalStatus.CONFIRMED
    assert result.preview.data.bars[-1].timestamp == MIDDAY_AS_OF
    assert len(captured_frames) == 2
    assert captured_frames[1]["Close"].iloc[-1] == pytest.approx(60.0)


def test_preview_keeps_pytz_daily_index_resampleable_when_quote_uses_zoneinfo() -> None:
    query = make_query()
    base = make_completed_envelope(query)
    historical_timezone = pytz.timezone("Asia/Shanghai")
    historical_bars = [
        Bar(
            timestamp=bar.timestamp.astimezone(historical_timezone),
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            amount=bar.amount,
        )
        for bar in base.bars
    ]
    completed = DataEnvelope(
        instrument=base.instrument,
        timeframe=base.timeframe,
        purpose=base.purpose,
        bars=historical_bars,
        provider=base.provider,
        mode=base.mode,
        quality=base.quality,
        as_of=base.as_of,
        fetched_at=base.fetched_at,
        last_bar_at=historical_bars[-1].timestamp,
        timezone=base.timezone,
        adjustment=base.adjustment,
        freshness_seconds=base.freshness_seconds,
        source_chain=base.source_chain,
        request_id=base.request_id,
    )
    gateway = StaticGateway(completed)
    captured_indexes: list[pd.Index] = []

    def runner(
        frame: pd.DataFrame,
        _info: dict[str, Any],
        *,
        timeframe: str,
        benchmark_df: pd.DataFrame | None,
    ) -> dict[str, Any]:
        assert timeframe == "日线"
        assert benchmark_df is None
        captured_indexes.append(frame.index)
        assert isinstance(frame.index, pd.DatetimeIndex)
        # These are the operations that failed when pytz and zoneinfo were
        # mixed in the provisional envelope and pandas fell back to object.
        frame.resample("W").last()
        frame.resample("ME").last()
        last_close = float(frame["Close"].iloc[-1])
        return {
            "observed_last_close": last_close,
            "trend_strength_score": last_close,
            "buy_action_plan": {
                "buy_score": {
                    "total_score": last_close,
                    "dimensions": {
                        "latest_close": {
                            "label": "最新收盘",
                            "score": last_close,
                            "weight": 1.0,
                        }
                    },
                }
            },
        }

    use_case = ResearchAnalysisUseCase(
        gateway,
        indicator_builder=lambda frame: frame,
        analysis_runner=runner,
    )
    result = RealtimeAnalysisPreviewService(use_case).analyze(
        query,
        make_quote(),
    )

    assert len(captured_indexes) == 2
    assert all(isinstance(index, pd.DatetimeIndex) for index in captured_indexes)
    assert result.preview.data.bars[-1].timestamp.tzinfo == historical_bars[-1].timestamp.tzinfo
    assert type(result.preview.data.bars[-1].timestamp.tzinfo) is type(
        historical_bars[-1].timestamp.tzinfo
    )


def test_ordinary_stale_open_quote_is_rejected() -> None:
    query = make_query()
    service, _gateway, _captured_frames = build_preview_service(
        make_completed_envelope(query)
    )

    with pytest.raises(DecisionInputError, match="open-session quote is not valid"):
        service.analyze(
            query,
            make_quote(
                quality=QualityStatus.STALE,
                mode=DataMode.DELAYED,
            ),
        )


def test_cross_instrument_quote_is_rejected() -> None:
    query = make_query()
    service, _gateway, _captured_frames = build_preview_service(
        make_completed_envelope(query)
    )

    with pytest.raises(
        DecisionInputError,
        match="quote instrument does not match analysis",
    ):
        service.analyze(query, make_quote(instrument=OTHER_INSTRUMENT))


def test_cross_day_quote_is_rejected() -> None:
    query = make_query()
    service, _gateway, _captured_frames = build_preview_service(
        make_completed_envelope(query)
    )

    with pytest.raises(
        DecisionInputError,
        match="quote is not from the current session date",
    ):
        service.analyze(
            query,
            make_quote(data_time=datetime(2026, 9, 2, 10, 30, tzinfo=CN)),
        )


def test_quote_is_rejected_when_completed_daily_data_already_covers_date() -> None:
    query = make_query(AFTER_CLOSE_AS_OF)
    completed = make_completed_envelope(query, include_quote_date=True)
    service, _gateway, _captured_frames = build_preview_service(completed)

    with pytest.raises(
        DecisionInputError,
        match="completed daily data already covers the quote date",
    ):
        service.analyze(
            query,
            make_quote(
                as_of=AFTER_CLOSE_AS_OF,
                session_status=QuoteSessionStatus.AFTER_CLOSE,
                mode=DataMode.DELAYED,
            ),
        )
