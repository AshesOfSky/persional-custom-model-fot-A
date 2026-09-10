from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from apps.api.contracts import CompositeSignalPointView, ResearchAnalysisResponse
from custom_model.application.analysis_service import AnalysisService, DecisionInputError
from custom_model.application.market_view import MarketBarsViewService, MarketViewInputError
from custom_model.application.research import ResearchResult
from custom_model.application.realtime_signal_preview import (
    RealtimeSignalPreviewComputation,
)
from custom_model.application.research_view import (
    EVIDENCE_SCORE_SEMANTICS,
    ResearchViewMapper,
    ResearchViewService,
    TechnicalSummaryMapper,
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
    QuoteEnvelope,
    QuoteSessionStatus,
    QuoteTemporalMode,
    SignalStatus,
    Timeframe,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 8, 7, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId(
    symbol="603259.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)


def make_envelope(
    *,
    request_id: str = "research-envelope-001",
    provider: str = "research-view-fixture",
) -> DataEnvelope:
    start = AS_OF - timedelta(days=399)
    bars: list[Bar] = []
    for index in range(400):
        close = 50.0 + index * 0.05 + math.sin(index / 9.0)
        bars.append(
            Bar(
                timestamp=start + timedelta(days=index),
                open=close - 0.2,
                high=close + 0.8,
                low=close - 0.9,
                close=close,
                volume=1_000_000 + index * 1_000,
                amount=close * (1_000_000 + index * 1_000),
            )
        )
    return DataEnvelope(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        bars=bars,
        provider=provider,
        mode=DataMode.DELAYED,
        quality=QualityStatus.VALID,
        as_of=AS_OF,
        fetched_at=AS_OF,
        last_bar_at=AS_OF,
        timezone="Asia/Shanghai",
        adjustment=Adjustment.FORWARD,
        freshness_seconds=0,
        request_id=request_id,
        source_chain=[provider],
        warnings=["fixture provenance warning"],
    )


def legacy_analysis() -> dict[str, Any]:
    return {
        "trend_strength_score": np.float64(92.0),
        "trend_strength_level": "强趋势",
        "signal_summary": "趋势子分较强，但不等于顶层证据评分",
        "multi_timeframe_summary": {
            "alignment": "mixed",
            "dominant_trend": "up",
            "summary": "日线向上、周线待确认",
            "alignment_score": np.float64(67.5),
            "mtf_score": np.float32(0.75),
        },
        "divergence_summary": {
            "active_count": np.int64(1),
            "has_bullish_divergence": np.bool_(True),
            "has_bearish_divergence": np.bool_(False),
            "strongest_divergence": {"description": "RSI 底背离待确认"},
        },
        "composite_signals": {
            "current_signal": "观察",
            "signal_strength": "中",
            "buy_signals": [{"at": "bar-1"}],
            "sell_signals": [],
        },
        # Zero is still an uncalibrated legacy probability field and must not
        # silently become "absent" merely because it is false-y.
        "comprehensive_probability": np.float64(0.0),
        "buy_action_plan": {
            "buy_score": {
                "total_score": np.float64(50.0),
                "dimensions": {
                    "trend": {
                        "label": "趋势",
                        "score": np.float64(80.0),
                        "weight": np.float64(0.25),
                    },
                    "momentum": {
                        "label": "动量",
                        "score": np.float64(40.0),
                        "weight": np.float64(0.75),
                    },
                },
            }
        },
    }


def make_research(
    data: DataEnvelope | None = None,
    analysis: dict[str, Any] | None = None,
) -> ResearchResult:
    selected_data = data or make_envelope()
    selected_analysis = analysis or legacy_analysis()
    snapshot = AnalysisService().build_snapshot(selected_data, selected_analysis)
    return ResearchResult(
        data=selected_data,
        analysis=selected_analysis,
        snapshot=snapshot,
    )


def build_market_view(data: DataEnvelope):
    # build_view deliberately does not consult its gateway. A sentinel makes a
    # second data fetch fail loudly if that behavior ever changes.
    class NeverFetchGateway:
        def fetch_bars(self, _query):  # pragma: no cover - defensive sentinel
            raise AssertionError("market view attempted a second data fetch")

    return MarketBarsViewService(NeverFetchGateway()).build_view(data)


def make_preview() -> RealtimeSignalPreviewComputation:
    return RealtimeSignalPreviewComputation(
        schema_version="realtime-signal-preview/v1",
        available=True,
        status=SignalStatus.PROVISIONAL,
        bar_time="2026-08-09",
        completed_through=AS_OF,
        daily_request_id="research-envelope-001",
        quote_request_id="quote-preview-001",
        daily_provider="research-view-fixture",
        quote_provider="quote-provider",
        quote_mode=DataMode.LIVE,
        quote_quality=QualityStatus.VALID,
        score_semantics="signal_evidence_score_not_probability",
        composite_signals={
            "buy_signals": (
                {
                    "date": "2026-08-09",
                    "price": 71.2,
                    "strength": "强",
                    "confidence_level": "强",
                    "confidence": 78.4,
                    "confirmations": [
                        "MACD金叉",
                        "放量(>1.5xMA5)",
                        "OBV底背离累积",
                    ],
                    "confidence_components": {
                        "version": "signal-evidence-v2",
                        "semantics": "signal_evidence_score_not_probability",
                        "independent_bucket_count": 3,
                        "bucket_score": 72.0,
                        "directional_trend_score": 88.0,
                        "trend_evidence_status": "available",
                    },
                },
            ),
            "sell_signals": (),
        },
        warnings=("provisional signal can change before confirmation",),
    )


def make_latest_quote() -> QuoteEnvelope:
    quote_at = AS_OF + timedelta(hours=19)
    return QuoteEnvelope(
        instrument=INSTRUMENT,
        purpose=DataPurpose.RESEARCH,
        display_name="药明康德",
        provider="quote-provider",
        mode=DataMode.LIVE,
        quality=QualityStatus.VALID,
        temporal_mode=QuoteTemporalMode.LATEST,
        requested_at=quote_at - timedelta(seconds=1),
        as_of=quote_at,
        fetched_at=quote_at,
        data_time=quote_at,
        provider_updated_at=quote_at,
        data_time_basis="provider_timestamp",
        timezone="Asia/Shanghai",
        currency="CNY",
        freshness_seconds=0,
        session_status=QuoteSessionStatus.OPEN,
        trade_status_code=19,
        trade_status_verified=True,
        last_price=71.2,
        previous_close=70.0,
        open=70.1,
        high=71.5,
        low=69.8,
        change=1.2,
        change_percent=1.2 / 70.0 * 100,
        volume_lots=1_800_000,
        amount=126_000_000,
        is_synthetic=False,
        source_chain=["quote:native"],
        request_id="quote-preview-001",
    )


def test_mapper_preserves_one_envelope_and_keeps_evidence_score_semantics() -> None:
    research = make_research()
    market_view = build_market_view(research.data)

    result = ResearchViewMapper().map(research, market_view)

    assert result.data_ref.request_id == research.data.request_id
    assert result.market_view.data.request_id == research.data.request_id
    assert result.snapshot.data_lineage[0].request_id == research.data.request_id
    assert result.data_ref.bar_count == len(research.data.bars)
    assert result.snapshot.evidence_score == pytest.approx(50.0)
    assert result.technical_summary.trend_strength is not None
    assert result.technical_summary.trend_strength.score == pytest.approx(92.0)
    assert result.snapshot.evidence_score != result.technical_summary.trend_strength.score
    assert result.technical_summary.score_semantics == EVIDENCE_SCORE_SEMANTICS
    assert (
        result.technical_summary.legacy_directional_evidence_status
        == "present_uncalibrated"
    )
    assert result.section_status.technical_summary == "available"


def test_mapper_keeps_realtime_preview_separate_from_confirmed_snapshot() -> None:
    research = make_research()

    result = ResearchViewMapper().map(
        research,
        build_market_view(research.data),
        realtime_signal_preview=make_preview(),
    )

    assert result.snapshot.bar_status is SignalStatus.CONFIRMED
    assert result.realtime_signal_preview is not None
    assert result.realtime_signal_preview.status is SignalStatus.PROVISIONAL
    assert result.realtime_signal_preview.advisory_only is True
    assert result.realtime_signal_preview.formal_use_eligible is False
    assert result.realtime_signal_preview.daily_request_id == research.data.request_id
    assert result.realtime_signal_preview.quote_request_id == "quote-preview-001"
    assert len(result.realtime_signal_preview.signals) == 1
    point = result.realtime_signal_preview.signals[0]
    assert point.kind == "buy"
    assert point.confidence_level == "强"
    assert point.confidence == pytest.approx(78.4)

    response = ResearchAnalysisResponse.model_validate(result, from_attributes=True)
    assert response.snapshot.bar_status is SignalStatus.CONFIRMED
    assert response.realtime_signal_preview is not None
    assert response.realtime_signal_preview.status is SignalStatus.PROVISIONAL


def test_mapper_rejects_same_request_id_bound_to_different_envelope() -> None:
    research = make_research()
    different_data = research.data.model_copy(
        update={
            "provider": "different-provider",
            "source_chain": ["different-provider"],
        }
    )
    market_view = build_market_view(different_data)

    with pytest.raises(
        DecisionInputError, match="market view does not reference analyzed data"
    ):
        ResearchViewMapper().map(research, market_view)


def test_technical_mapper_normalizes_numpy_and_drops_non_finite_values() -> None:
    summary, status, warnings = TechnicalSummaryMapper().map(
        {
            "trend_strength_score": np.float64(71.25),
            "multi_timeframe_summary": {
                "alignment": "mixed",
                "alignment_score": np.float64(np.inf),
                "mtf_score": np.array([1.0, 2.0]),
            },
            "divergence_summary": {
                "active_count": np.int64(2),
                "has_bullish_divergence": np.bool_(True),
                "has_bearish_divergence": "false",
            },
            "composite_signals": {
                "current_signal": "观察",
                "buy_signals": ("a", "b"),
                "sell_signals": np.array(["c"]),
            },
            "comprehensive_probability": np.array([0.2, 0.8]),
        }
    )

    assert status == "partial"
    assert summary.trend_strength is not None
    assert type(summary.trend_strength.score) is float
    assert summary.multi_timeframe is not None
    assert summary.multi_timeframe.alignment_score is None
    assert summary.multi_timeframe.mtf_score is None
    assert summary.divergence is not None
    assert type(summary.divergence.active_count) is int
    assert type(summary.divergence.bullish_active) is bool
    assert summary.divergence.bullish_active is True
    assert summary.divergence.bearish_active is False
    assert summary.composite_signal is not None
    assert summary.composite_signal.recent_buy_count == 2
    assert summary.composite_signal.recent_sell_count == 0
    assert summary.legacy_directional_evidence_status == "present_uncalibrated"
    assert any("non-finite or out-of-range" in warning for warning in warnings)
    assert any("ignored non-list" in warning for warning in warnings)


def test_signal_mapper_preserves_non_probability_score_breakdown() -> None:
    summary, status, warnings = TechnicalSummaryMapper().map(
        {
            "composite_signals": {
                "current_signal": "买入",
                "signal_strength": "中",
                "buy_signals": [
                    {
                        "date": "2026-08-08",
                        "price": 51.25,
                        "strength": "中",
                        "confidence_level": "中强",
                        "confidence": 66.0,
                        "confirmations": ["EMA金叉(8×21)", "MACD金叉"],
                        "confidence_components": {
                            "version": "signal-evidence-v2",
                            "semantics": "signal_evidence_score_not_probability",
                            "independent_bucket_count": 2,
                            "bucket_score": 58.0,
                            "directional_trend_score": 78.0,
                            "trend_evidence_status": "available",
                        },
                    }
                ],
                "sell_signals": [],
            }
        }
    )

    assert status == "available"
    assert warnings == ()
    assert summary.composite_signal is not None
    point = summary.composite_signal.signals[0]
    assert point.strength == "中"
    assert point.confidence_level == "中强"
    assert point.confidence == pytest.approx(66.0)
    assert point.confidence_components is not None
    assert point.confidence_components.version == "signal-evidence-v2"
    assert point.confidence_components.semantics == "signal_evidence_score_not_probability"
    assert point.confidence_components.independent_bucket_count == 2
    assert point.confidence_components.bucket_score == pytest.approx(58.0)
    assert point.confidence_components.directional_trend_score == pytest.approx(78.0)
    assert point.confidence_components.trend_evidence_status == "available"

    response = ResearchAnalysisResponse.model_validate(
        ResearchViewMapper().map(make_research(), build_market_view(make_envelope())),
        from_attributes=True,
    )
    assert response.technical_summary.score_semantics == "evidence_score_not_probability"


def test_signal_mapper_rejects_unknown_confidence_level() -> None:
    summary, _status, warnings = TechnicalSummaryMapper().map(
        {
            "composite_signals": {
                "buy_signals": [
                    {
                        "date": "2026-08-08",
                        "price": 51.25,
                        "confidence_level": "极高",
                    }
                ],
                "sell_signals": [],
            }
        }
    )

    assert summary.composite_signal is not None
    assert summary.composite_signal.signals[0].confidence_level is None
    assert any("ignored unsupported" in warning for warning in warnings)


def test_signal_contract_rejects_score_or_level_drift_from_v2_components() -> None:
    payload = {
        "time": "2026-08-08",
        "kind": "buy",
        "price": 51.25,
        "strength": "中",
        "confidence_level": "中强",
        "confidence": 66.0,
        "confirmations": ["EMA金叉(8×21)", "MACD金叉"],
        "confidence_components": {
            "version": "signal-evidence-v2",
            "semantics": "signal_evidence_score_not_probability",
            "independent_bucket_count": 2,
            "bucket_score": 58.0,
            "directional_trend_score": 78.0,
            "trend_evidence_status": "available",
        },
    }

    assert CompositeSignalPointView.model_validate(payload).confidence == 66.0
    with pytest.raises(ValidationError, match="confidence does not match"):
        CompositeSignalPointView.model_validate({**payload, "confidence": 58.0})
    with pytest.raises(ValidationError, match="confidence_level does not match"):
        CompositeSignalPointView.model_validate({**payload, "confidence_level": "中"})


def test_mapped_response_serializes_as_native_finite_json() -> None:
    research = make_research()
    result = ResearchViewMapper().map(research, build_market_view(research.data))

    response = ResearchAnalysisResponse.model_validate(result, from_attributes=True)
    encoded = response.model_dump_json()
    decoded = json.loads(encoded)

    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert decoded["snapshot"]["evidence_score"] == 50.0
    assert decoded["technical_summary"]["trend_strength"]["score"] == 92.0
    assert all(
        math.isfinite(point["value"])
        for series in decoded["market_view"]["indicators"]["ema"].values()
        for point in series["points"]
    )


class FakeResearchUseCase:
    def __init__(self, result: ResearchResult) -> None:
        self.result = result
        self.calls: list[tuple[DataQuery, dict[str, Any]]] = []

    def analyze(self, query: DataQuery, **kwargs: Any) -> ResearchResult:
        self.calls.append((query, kwargs))
        return self.result


class RecordingMarketViewService:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.envelopes: list[DataEnvelope] = []

    def build_view(self, envelope: DataEnvelope):
        self.envelopes.append(envelope)
        if self.error is not None:
            raise self.error
        return self.result


class RecordingPreviewService:
    def __init__(self, result: RealtimeSignalPreviewComputation) -> None:
        self.result = result
        self.calls: list[tuple[DataEnvelope, QuoteEnvelope]] = []

    def build(
        self, completed: DataEnvelope, quote: QuoteEnvelope
    ) -> RealtimeSignalPreviewComputation:
        self.calls.append((completed, quote))
        return self.result


def test_service_analyzes_once_and_builds_market_view_from_exact_envelope() -> None:
    research = make_research()
    use_case = FakeResearchUseCase(research)
    market_service = RecordingMarketViewService(build_market_view(research.data))
    service = ResearchViewService(use_case, market_service)
    query = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        adjustment=Adjustment.FORWARD,
        as_of=AS_OF,
    )

    result = service.analyze(
        query,
        info={"name": "药明康德"},
        has_position=True,
    )

    assert len(use_case.calls) == 1
    assert use_case.calls[0] == (
        query,
        {
            "info": {"name": "药明康德"},
            "benchmark_query": None,
            "has_position": True,
        },
    )
    assert market_service.envelopes == [research.data]
    assert market_service.envelopes[0] is research.data
    assert result.market_view.data is research.data


def test_service_builds_preview_from_same_confirmed_envelope() -> None:
    research = make_research()
    use_case = FakeResearchUseCase(research)
    market_service = RecordingMarketViewService(build_market_view(research.data))
    preview_service = RecordingPreviewService(make_preview())
    service = ResearchViewService(
        use_case,
        market_service,
        realtime_preview_service=preview_service,
    )
    query = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        adjustment=Adjustment.FORWARD,
        as_of=AS_OF,
    )
    quote = make_latest_quote()

    result = service.analyze(query, provisional_quote=quote)

    assert len(use_case.calls) == 1
    assert preview_service.calls == [(research.data, quote)]
    assert result.snapshot.bar_status is SignalStatus.CONFIRMED
    assert result.realtime_signal_preview is not None
    assert result.realtime_signal_preview.status is SignalStatus.PROVISIONAL


def test_service_converts_market_view_validation_to_decision_input_error() -> None:
    research = make_research()
    service = ResearchViewService(
        FakeResearchUseCase(research),
        RecordingMarketViewService(error=MarketViewInputError("non-finite bars")),
    )
    query = DataQuery(
        instrument=INSTRUMENT,
        timeframe=Timeframe.D1,
        purpose=DataPurpose.RESEARCH,
        as_of=AS_OF,
    )

    with pytest.raises(DecisionInputError, match="non-finite bars"):
        service.analyze(query)
