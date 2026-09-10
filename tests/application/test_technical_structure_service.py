from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from typing import Any

import numpy as np
from pydantic import TypeAdapter
import pytest

from custom_model.application.analysis_service import AnalysisService
from custom_model.application.research import ResearchResult
from custom_model.application.technical_structure_service import (
    SectionAvailability,
    TechnicalStructureBuildError,
    TechnicalStructureInputError,
    TechnicalStructureMapper,
    TechnicalStructureService,
    TechnicalStructureViewResult,
    technical_structure_state_key,
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
    SignalStatus,
    Timeframe,
)


UTC = timezone.utc
AS_OF = datetime(2026, 8, 10, 7, 0, tzinfo=UTC)
INSTRUMENT = InstrumentId(
    symbol="603259.SS",
    exchange=Exchange.SSE,
    market=Market.CN,
    asset_type=AssetType.STOCK,
)
QUERY = DataQuery(
    instrument=INSTRUMENT,
    timeframe=Timeframe.D1,
    purpose=DataPurpose.RESEARCH,
    adjustment=Adjustment.FORWARD,
    as_of=AS_OF,
)


def make_envelope() -> DataEnvelope:
    bars = []
    for index in range(80):
        close = 100.0 + index * 0.2
        bars.append(
            Bar(
                timestamp=AS_OF - timedelta(days=79 - index),
                open=close - 0.3,
                high=close + 1.0,
                low=close - 1.0,
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
        provider="baostock",
        mode=DataMode.DELAYED,
        quality=QualityStatus.VALID,
        as_of=AS_OF,
        fetched_at=AS_OF,
        last_bar_at=AS_OF,
        timezone="Asia/Shanghai",
        adjustment=Adjustment.FORWARD,
        freshness_seconds=0,
        request_id="structure-data-001",
        source_chain=["baostock"],
    )


def legacy_analysis() -> dict[str, Any]:
    return {
        "trend_strength_score": 64.0,
        "buy_action_plan": {
            "buy_score": {
                "total_score": 60.0,
                "dimensions": {
                    "trend": {"label": "trend", "score": 70.0, "weight": 0.5},
                    "support": {
                        "label": "support",
                        "score": 50.0,
                        "weight": 0.5,
                    },
                },
            }
        },
    }


def make_research(data: DataEnvelope | None = None) -> ResearchResult:
    selected = data or make_envelope()
    analysis = legacy_analysis()
    snapshot = AnalysisService().build_snapshot(make_envelope(), analysis)
    return ResearchResult(data=selected, analysis=analysis, snapshot=snapshot)


def full_payload() -> dict[str, Any]:
    return {
        "engine": "v5",
        "engineNote": "authoritative legacy-v5 adapter",
        "sr": {
            "supports": [
                {
                    "price": 108.0,
                    "sources": ["classic", "gann_octave"],
                    "methodVersion": "sr-confluence-3",
                    "basis": "touch count and recency",
                    "confluence": 2,
                    "distancePct": -2.5,
                }
            ],
            "resistances": [
                {
                    "price": 120.0,
                    "sources": ["classic"],
                    "methodVersion": "sr-confluence-3",
                }
            ],
        },
        "fibonacci": {
            "source": "v5",
            "methodVersion": "fibonacci-v1",
            "high": 121.0,
            "low": 95.0,
            "currentPct": 76.0,
            "levels": [
                {
                    "ratio": 0.618,
                    "label": "61.8%",
                    "price": 111.068,
                    "role": "support",
                }
            ],
        },
        "gann": {
            "source": "modules/gann",
            "methodVersion": "gann-v1",
            "range": {"high": 121.0, "low": 95.0, "lookback": 120},
            "pivot": {"date": "2026-06-01", "price": 95.0, "direction": "up"},
            "octaves": [
                {"label": "4/8", "price": 108.0, "ratio": 0.5, "strong": True}
            ],
            "fanLevels": [
                {"label": "Gann 1x1", "price": 110.0, "type": "support"}
            ],
            "timeCycles": [{"cycle": 90, "date": "2026-09-01", "futureBars": 12}],
        },
        "elliott": {
            "source": "v5",
            "methodVersion": "v5-window-confirmed",
            "currentWave": {
                "type": "impulse",
                "waveNumber": 3,
                "direction": "bullish",
                "confidence": 76.0,
                "phase": "wave 3",
                "description": "current count lacks a bound confirmation anchor",
                "confirmed": True,
            },
            "targets": [
                {
                    "price": 125.0,
                    "label": "W3 target",
                    "ratio": "1.618",
                    "direction": "bullish",
                }
            ],
            "multiTf": {
                "alignment": "bullish",
                "bias": "up",
                "confidence": 80,
                "timeframes": ["1h", "4h"],
                "reasoning": ["extra timeframe fetch"],
            },
        },
        "chan": {
            "source": "local",
            "methodVersion": "chan-local-3",
            "trend": "up",
            "description": "prefix-stable subset plus legacy provisional structures",
            "lastBi": {
                "direction": "up",
                "startTime": "2026-07-01",
                "endTime": "2026-07-10",
                "startPrice": 100.0,
                "endPrice": 110.0,
                "confirmed": True,
                "status": "confirmed",
            },
            "activeBi": {
                "direction": "down",
                "startTime": "2026-07-10",
                "endTime": "2026-08-10",
                "startPrice": 110.0,
                "endPrice": 108.0,
                "confirmed": False,
                "status": "provisional",
            },
            "fenxing": [
                {
                    "type": "top",
                    "time": "2026-07-10",
                    "price": 110.0,
                    "confirmed": True,
                    "status": "confirmed",
                    "confirmedTime": "2026-07-12",
                }
            ],
            "bi": [
                {
                    "direction": "up",
                    "startTime": "2026-07-01",
                    "endTime": "2026-07-10",
                    "startPrice": 100.0,
                    "endPrice": 110.0,
                    "confirmed": True,
                    "status": "confirmed",
                }
            ],
            "stableBi": [
                {
                    "direction": "up",
                    "startTime": "2026-06-01",
                    "endTime": "2026-06-10",
                    "startPrice": 96.0,
                    "endPrice": 105.0,
                    "confirmed": True,
                    "status": "confirmed",
                    "basis": "confirmed_fractal_reversal",
                }
            ],
            "segment": [
                {
                    "direction": "up",
                    "startTime": "2026-05-01",
                    "endTime": "2026-06-10",
                    "startPrice": 92.0,
                    "endPrice": 105.0,
                    "confirmed": True,
                    "status": "confirmed",
                    "basis": "confirmed_bi_structural_break",
                }
            ],
            "trendStructure": {
                "type": "up",
                "label": "up trend",
                "confirmed": True,
                "status": "confirmed",
                "basis": "confirmed_same_direction_segments",
                "evidenceSegmentCount": 3,
            },
            "zhongshu": [
                {
                    "startTime": "2026-06-01",
                    "endTime": "2026-07-01",
                    "zg": 106.0,
                    "zd": 102.0,
                    "confirmed": True,
                    "status": "confirmed",
                }
            ],
            "signals": [
                {
                    "type": "buy1",
                    "label": "first buy",
                    "time": "2026-07-02",
                    "price": 103.0,
                    "confirmed": True,
                    "status": "confirmed",
                }
            ],
        },
        "candlestick": {
            "source": "v5",
            "methodVersion": "candlestick-v1",
            "patterns": [
                {
                    "nameCn": "hammer",
                    "type": "reversal",
                    "direction": "bullish",
                    "time": "2026-08-08",
                    "description": "closed-bar pattern",
                    "biasScore": 9_999,
                }
            ],
        },
        "chartPatterns": {
            "source": "v5",
            "methodVersion": "chart-pattern-v1",
            "patterns": [
                {
                    "nameCn": "ascending triangle",
                    "type": "continuation",
                    "direction": "bullish",
                    "neckline": 118.0,
                    "target": 126.0,
                    "stop": 109.0,
                    "confidence": 71.0,
                    "description": "breakout pending",
                    "biasScore": 90,
                }
            ],
        },
        "divergences": {
            "source": "v5",
            "methodVersion": "divergence-v1",
            "list": [
                {
                    "type": "regular_bullish",
                    "indicator": "RSI",
                    "startDate": "2026-07-01",
                    "endDate": "2026-08-01",
                    "spanBars": 22,
                    "reliability": "medium",
                    "description": "price/RSI divergence",
                    "biasScore": 80,
                }
            ],
        },
        "volumePrice": {
            "source": "v5",
            "methodVersion": "volume-price-v1",
            "divergence": {
                "detected": True,
                "type": "price_up_volume_down",
                "description": "volume does not confirm",
                "biasScore": -50,
            },
            "turnover": {"current": 2.5, "avg20": 2.0, "level": "normal"},
            "volumeRatio": {
                "value": 1.2,
                "level": "moderate",
                "interpretation": "slightly active",
                "biasScore": 10,
            },
            "accumulation": {"phase": "accumulation", "trend": "up"},
            "wyckoff": {
                "phase": "markup",
                "confidence": 65,
                "description": "candidate phase",
            },
            "combinedBias": 99,
        },
        "multiTf": {
            "source": "v5",
            "methodVersion": "multi-timeframe-v1",
            "timeframes": [
                {
                    "tf": "daily",
                    "trend": "up",
                    "strength": 90,
                    "score": 99,
                    "emaAlignment": "bullish",
                    "macdDirection": "up",
                    "rsiZone": "strong",
                    "details": "daily structure",
                }
            ],
            "alignment": "mixed",
            "alignmentScore": 99,
            "dominantTrend": "up",
            "summary": "daily up, weekly pending",
        },
        "overlay": {
            "fib": [{"ratio": 0.618, "label": "61.8%", "price": 111.068}],
            "gannOctaves": [{"label": "4/8", "price": 108.0}],
            "gannFan": [
                {
                    "label": "1x1",
                    "points": [
                        {"time": "2026-06-01", "value": 95.0},
                        {"time": "2026-08-10", "value": 110.0},
                    ],
                }
            ],
            "elliottSegments": [
                {
                    "kind": "impulse",
                    "confirmed": True,
                    "status": "confirmed",
                    "points": [
                        {"time": "2026-06-01", "price": 95.0},
                        {"time": "2026-08-10", "price": 115.0},
                    ],
                }
            ],
            "elliottLabels": [
                {
                    "time": "2026-08-10",
                    "price": 115.0,
                    "label": "3",
                    "confirmed": True,
                }
            ],
            "elliottTargets": [{"price": 125.0, "label": "W3"}],
            "chanBi": [
                {
                    "direction": "up",
                    "confirmed": True,
                    "status": "confirmed",
                    "points": [
                        {"time": "2026-07-01", "price": 100.0},
                        {"time": "2026-07-10", "price": 110.0},
                    ],
                }
            ],
            "chanActiveBi": {
                "direction": "down",
                "points": [
                    {"time": "2026-07-10", "price": 110.0},
                    {"time": "2026-08-10", "price": 108.0},
                ],
            },
            "chanZhongshu": [
                {
                    "startTime": "2026-06-01",
                    "endTime": "2026-07-01",
                    "zg": 106.0,
                    "zd": 102.0,
                    "confirmed": True,
                }
            ],
            "chanFenxing": [
                {
                    "time": "2026-07-10",
                    "price": 110.0,
                    "type": "top",
                    "confirmed": True,
                    "status": "confirmed",
                }
            ],
            "chanSignals": [
                {
                    "time": "2026-07-02",
                    "price": 103.0,
                    "label": "first buy",
                    "type": "buy1",
                    "confirmed": True,
                }
            ],
        },
        "interpretations": [{"biasScore": 99, "actionHint": "buy"}],
        "unknownTopLevel": {"secret": "must not pass through"},
    }


class FakeResearchUseCase:
    def __init__(self, result: ResearchResult) -> None:
        self.result = result
        self.calls: list[tuple[DataQuery, dict[str, Any]]] = []

    def analyze(self, query: DataQuery, **kwargs: Any) -> ResearchResult:
        self.calls.append((query, kwargs))
        return self.result


class RecordingBuilder:
    def __init__(self, payload: Any = None, error: Exception | None = None) -> None:
        self.payload = full_payload() if payload is None else payload
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.payload


def test_service_uses_one_research_result_and_binds_builder_to_exact_objects() -> None:
    research = make_research()
    use_case = FakeResearchUseCase(research)
    builder = RecordingBuilder()
    service = TechnicalStructureService(use_case, builder)

    result = service.analyze(QUERY, info={"name": "WuXi AppTec"}, has_position=True)

    assert len(use_case.calls) == 1
    assert len(builder.calls) == 1
    assert builder.calls[0]["data"] is research.data
    assert builder.calls[0]["analysis"] is research.analysis
    assert builder.calls[0]["snapshot"] is research.snapshot
    assert result.snapshot is research.snapshot
    assert result.data_ref.request_id == research.data.request_id
    assert result.data_ref.lineage_request_ids[0] == research.data.request_id
    assert result.snapshot.evidence_score == 60.0
    assert all(
        status is not SectionAvailability.UNAVAILABLE
        for status in vars(result.section_status).values()
    )


def test_service_binds_chan_state_to_complete_instrument_identity_and_timeframe() -> None:
    research = make_research()
    builder = RecordingBuilder()
    service = TechnicalStructureService(FakeResearchUseCase(research), builder)
    prior_state = {"version": "chan-state-1", "contentHash": "tampered"}

    service.analyze(QUERY, prior_state=prior_state)

    assert technical_structure_state_key(INSTRUMENT, Timeframe.D1) == (
        "chan-state-1|market=CN|exchange=SSE|asset_type=stock|"
        "symbol=603259.SS|currency=CNY|timeframe=1d"
    )
    assert builder.calls[0]["state_key"] == technical_structure_state_key(
        INSTRUMENT, Timeframe.D1
    )
    assert builder.calls[0]["prior_state"] is prior_state


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"mode": DataMode.FALLBACK}, "data mode"),
        ({"mode": DataMode.DEMO, "is_synthetic": True}, "synthetic/demo"),
        ({"quality": QualityStatus.STALE}, "quality must be valid"),
        ({"quality": QualityStatus.PARTIAL}, "quality must be valid"),
        ({"quality": QualityStatus.UNAVAILABLE}, "quality must be valid"),
        ({"source_chain": []}, "known provider and source chain"),
        ({"provider": "unknown", "source_chain": ["unknown"]}, "known provider"),
    ],
)
def test_formal_admission_rejects_untrusted_data_before_builder(
    updates: dict[str, Any], message: str
) -> None:
    research = make_research(make_envelope().model_copy(update=updates))
    builder = RecordingBuilder()
    service = TechnicalStructureService(FakeResearchUseCase(research), builder)

    with pytest.raises(TechnicalStructureInputError, match=message):
        service.analyze(QUERY)

    assert builder.calls == []


def test_formal_admission_rejects_snapshot_with_different_primary_lineage() -> None:
    research = make_research()
    wrong_lineage = research.snapshot.data_lineage[0].model_copy(
        update={"provider": "other-provider"}
    )
    research = replace(
        research,
        snapshot=research.snapshot.model_copy(update={"data_lineage": [wrong_lineage]}),
    )
    builder = RecordingBuilder()

    with pytest.raises(TechnicalStructureInputError, match="primary lineage"):
        TechnicalStructureService(FakeResearchUseCase(research), builder).analyze(QUERY)

    assert builder.calls == []


def test_demo_query_is_rejected_before_research_analysis() -> None:
    use_case = FakeResearchUseCase(make_research())
    builder = RecordingBuilder()
    demo_query = QUERY.model_copy(update={"purpose": DataPurpose.DEMO})

    with pytest.raises(TechnicalStructureInputError, match="demo purpose"):
        TechnicalStructureService(use_case, builder).analyze(demo_query)

    assert use_case.calls == []
    assert builder.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"engine": "fallback"},
        {"engine": "v5", "summary": {"overallScore": 80}},
        {"engine": "v5", "evidence_score": 80},
        {"engine": "v5", "probability": 0.8},
        {"engine": "v5", "action": "long"},
        {"engine": "v5", "tradePlan": {"entry": 100}},
    ],
)
def test_builder_cannot_return_fallback_or_a_second_decision(payload: dict[str, Any]) -> None:
    service = TechnicalStructureService(
        FakeResearchUseCase(make_research()), RecordingBuilder(payload)
    )

    with pytest.raises(TechnicalStructureInputError):
        service.analyze(QUERY)


def test_builder_failure_isolated_as_build_error() -> None:
    service = TechnicalStructureService(
        FakeResearchUseCase(make_research()),
        RecordingBuilder(error=RuntimeError("legacy engine exploded")),
    )

    with pytest.raises(TechnicalStructureBuildError, match="legacy engine exploded"):
        service.analyze(QUERY)


def test_mapper_normalizes_numpy_drops_nan_and_does_not_pass_unknown_fields() -> None:
    payload = full_payload()
    payload["fibonacci"]["currentPct"] = np.float64(np.nan)
    payload["fibonacci"]["levels"][0]["price"] = np.float64(111.068)
    payload["chartPatterns"]["patterns"][0]["confidence"] = np.float64(np.inf)
    payload["overlay"]["gannFan"][0]["points"][1]["value"] = np.float32(110.0)
    payload["unknownTopLevel"] = {"secret": np.float64(np.inf)}
    payload["candlestick"]["patterns"][0]["unknownNested"] = "not allowed"

    result = TechnicalStructureMapper().map(make_research(), payload)
    encoded = TypeAdapter(TechnicalStructureViewResult).dump_json(result).decode("utf-8")
    decoded = json.loads(encoded)

    assert "NaN" not in encoded
    assert "Infinity" not in encoded
    assert "secret" not in encoded
    assert "unknownNested" not in encoded
    assert "biasScore" not in encoded
    assert "alignmentScore" not in encoded
    assert decoded["sections"]["fibonacci"]["items"][1]["price"] == 111.068
    assert decoded["sections"]["fibonacci"]["items"][0]["value"] is None
    assert decoded["sections"]["chart_patterns"]["items"][0]["confidence"] is None
    assert any("non-finite" in warning for warning in result.warnings)


def test_missing_sections_are_explicitly_unavailable() -> None:
    result = TechnicalStructureMapper().map(make_research(), {"engine": "v5"})

    assert all(
        status is SectionAvailability.UNAVAILABLE
        for status in vars(result.section_status).values()
    )
    assert any("support_resistance unavailable" in warning for warning in result.warnings)
    assert any("overlay unavailable" in warning for warning in result.warnings)


def test_repaint_policy_keeps_current_elliott_and_legacy_chan_provisional() -> None:
    result = TechnicalStructureMapper().map(make_research(), full_payload())

    elliott_items = result.sections.elliott.items
    current = next(item for item in elliott_items if item.kind == "impulse")
    assert current.status is SignalStatus.PROVISIONAL
    assert current.confirmed is False
    assert current.provisional is True
    assert not any(item.kind == "multi_timeframe_synthesis" for item in elliott_items)
    assert any("without bound data lineage" in warning for warning in result.warnings)

    chan_by_kind: dict[str, list[Any]] = {}
    for item in result.sections.chan.items:
        chan_by_kind.setdefault(item.kind, []).append(item)
    for kind in ("last_bi", "active_bi", "bi", "zhongshu", "signal"):
        assert all(item.status is SignalStatus.PROVISIONAL for item in chan_by_kind[kind])
    for kind in ("stable_bi", "segment", "trend_structure"):
        assert all(item.status is SignalStatus.CONFIRMED for item in chan_by_kind[kind])

    overlay_by_kind: dict[str, list[Any]] = {}
    for item in result.sections.overlay.items:
        overlay_by_kind.setdefault(item.kind, []).append(item)
        assert item.source
        assert item.method_version
        assert item.provisional is (not item.confirmed)
    assert all(
        item.status is SignalStatus.PROVISIONAL
        for item in overlay_by_kind["elliott_segment"]
    )
    assert all(
        item.status is SignalStatus.PROVISIONAL for item in overlay_by_kind["chan_bi"]
    )
    assert all(
        item.status is SignalStatus.CONFIRMED
        for item in overlay_by_kind["chan_fenxing"]
    )


def test_elliott_multi_timeframe_requires_snapshot_bound_lineage() -> None:
    research = make_research()
    payload = full_payload()
    payload["elliott"]["multiTf"]["lineage_request_ids"] = [
        research.data.request_id
    ]

    result = TechnicalStructureMapper().map(research, payload)

    synthesis = next(
        item
        for item in result.sections.elliott.items
        if item.kind == "multi_timeframe_synthesis"
    )
    assert synthesis.tags == (research.data.request_id,)
    assert synthesis.status is SignalStatus.PROVISIONAL


def test_gann_fallback_is_unavailable_even_under_non_fallback_top_engine() -> None:
    payload = full_payload()
    payload["gann"]["source"] = "local-fallback"

    result = TechnicalStructureMapper().map(make_research(), payload)

    assert result.sections.gann.status is SectionAvailability.UNAVAILABLE
    assert result.sections.gann.items == ()
    assert any("formula is not equivalent" in warning for warning in result.warnings)
