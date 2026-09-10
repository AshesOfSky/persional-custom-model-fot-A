from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from collections.abc import Mapping
from typing import Any

from custom_model.application.analysis_service import DecisionInputError
from custom_model.application.market_view import (
    MarketBarsViewResult,
    MarketBarsViewService,
    MarketViewInputError,
)
from custom_model.application.research import ResearchAnalysisUseCase, ResearchResult
from custom_model.application.realtime_signal_preview import (
    RealtimeSignalPreviewComputation,
    RealtimeSignalPreviewService,
)
from custom_model.domain.models import (
    Adjustment,
    AnalysisSnapshot,
    DataMode,
    DataPurpose,
    DataQuery,
    InstrumentId,
    QualityStatus,
    QuoteEnvelope,
    SignalStatus,
    Timeframe,
)


RESEARCH_VIEW_SCHEMA_VERSION = "research-view/v1"
EVIDENCE_SCORE_SEMANTICS = "evidence_score_not_probability"


@dataclass(frozen=True)
class DataReference:
    """Complete provenance summary without duplicating the bar payload."""

    request_id: str
    instrument: InstrumentId
    timeframe: Timeframe
    purpose: DataPurpose
    adjustment: Adjustment
    provider: str
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    last_bar_at: datetime | None
    timezone: str
    freshness_seconds: float
    is_synthetic: bool
    source_chain: tuple[str, ...]
    source_warnings: tuple[str, ...]
    bar_count: int


@dataclass(frozen=True)
class TrendStrengthEvidence:
    """One technical sub-indicator; never the canonical decision score."""

    score: float | None
    level: str | None
    signal_summary: str | None


@dataclass(frozen=True)
class MultiTimeframeEvidence:
    alignment: str | None
    dominant_trend: str | None
    summary: str | None
    alignment_score: float | None
    mtf_score: float | None


@dataclass(frozen=True)
class DivergenceEvidence:
    active_count: int
    bullish_active: bool
    bearish_active: bool
    strongest_description: str | None


@dataclass(frozen=True)
class SignalConfidenceComponents:
    version: str
    semantics: str
    independent_bucket_count: int
    bucket_score: float
    directional_trend_score: float
    trend_evidence_status: str


@dataclass(frozen=True)
class CompositeSignalPoint:
    time: str
    kind: str
    price: float
    strength: str | None
    confidence_level: str | None
    confidence: float | None
    confirmations: tuple[str, ...]
    confidence_components: SignalConfidenceComponents | None = None


@dataclass(frozen=True)
class CompositeSignalEvidence:
    current_signal: str | None
    strength: str | None
    recent_buy_count: int
    recent_sell_count: int
    signals: tuple[CompositeSignalPoint, ...] = ()


@dataclass(frozen=True)
class RealtimeSignalPreview:
    schema_version: str
    available: bool
    status: SignalStatus
    bar_time: str
    completed_through: datetime | None
    daily_request_id: str
    quote_request_id: str
    daily_provider: str
    quote_provider: str
    quote_mode: DataMode
    quote_quality: QualityStatus
    score_semantics: str
    signals: tuple[CompositeSignalPoint, ...]
    warnings: tuple[str, ...]
    advisory_only: bool
    formal_use_eligible: bool


@dataclass(frozen=True)
class TechnicalSummary:
    score_semantics: str
    trend_strength: TrendStrengthEvidence | None
    multi_timeframe: MultiTimeframeEvidence | None
    divergence: DivergenceEvidence | None
    composite_signal: CompositeSignalEvidence | None
    legacy_directional_evidence_status: str


@dataclass(frozen=True)
class ResearchSectionStatus:
    data_ref: str
    snapshot: str
    market_view: str
    technical_summary: str


@dataclass(frozen=True)
class ResearchViewResult:
    schema_version: str
    data_ref: DataReference
    snapshot: AnalysisSnapshot
    market_view: MarketBarsViewResult
    technical_summary: TechnicalSummary
    section_status: ResearchSectionStatus
    warnings: tuple[str, ...]
    realtime_signal_preview: RealtimeSignalPreview | None = None


class TechnicalSummaryMapper:
    """Whitelist stable legacy fields and normalize them to native finite values."""

    def map(
        self, analysis: Mapping[str, Any]
    ) -> tuple[TechnicalSummary, str, tuple[str, ...]]:
        warnings: list[str] = []

        trend_score = self._finite_number(
            analysis.get("trend_strength_score"),
            "trend_strength_score",
            warnings,
            minimum=0,
            maximum=100,
        )
        trend_level = self._text(
            analysis.get("trend_strength_level"),
            "trend_strength_level",
            warnings,
        )
        signal_summary = self._text(
            analysis.get("signal_summary"),
            "signal_summary",
            warnings,
        )
        trend = None
        if any(value is not None for value in (trend_score, trend_level, signal_summary)):
            trend = TrendStrengthEvidence(
                score=trend_score,
                level=trend_level,
                signal_summary=signal_summary,
            )

        mtf_raw = self._mapping(
            analysis, "multi_timeframe_summary", warnings
        )
        multi_timeframe = None
        if mtf_raw:
            multi_timeframe = MultiTimeframeEvidence(
                alignment=self._text(
                    mtf_raw.get("alignment"), "multi_timeframe.alignment", warnings
                ),
                dominant_trend=self._text(
                    mtf_raw.get("dominant_trend"),
                    "multi_timeframe.dominant_trend",
                    warnings,
                ),
                summary=self._text(
                    mtf_raw.get("summary"), "multi_timeframe.summary", warnings
                ),
                alignment_score=self._finite_number(
                    mtf_raw.get("alignment_score"),
                    "multi_timeframe.alignment_score",
                    warnings,
                    minimum=0,
                    maximum=100,
                ),
                mtf_score=self._finite_number(
                    mtf_raw.get("mtf_score"),
                    "multi_timeframe.mtf_score",
                    warnings,
                    minimum=-2,
                    maximum=2,
                ),
            )

        divergence_raw = self._mapping(analysis, "divergence_summary", warnings)
        divergence = None
        if divergence_raw:
            active = divergence_raw.get("active_divergences")
            active_count = self._native_count(
                divergence_raw.get("active_count"),
                active,
                "divergence.active_count",
                warnings,
            )
            divergence = DivergenceEvidence(
                active_count=active_count,
                bullish_active=self._native_bool(
                    divergence_raw.get("has_bullish_divergence"),
                    "divergence.has_bullish_divergence",
                    warnings,
                ),
                bearish_active=self._native_bool(
                    divergence_raw.get("has_bearish_divergence"),
                    "divergence.has_bearish_divergence",
                    warnings,
                ),
                strongest_description=self._strongest_divergence(
                    divergence_raw.get("strongest_divergence"), warnings
                ),
            )

        composite_raw = self._mapping(analysis, "composite_signals", warnings)
        composite = None
        if composite_raw:
            composite = CompositeSignalEvidence(
                current_signal=self._text(
                    composite_raw.get("current_signal"),
                    "composite_signal.current_signal",
                    warnings,
                ),
                strength=self._text(
                    composite_raw.get("signal_strength"),
                    "composite_signal.strength",
                    warnings,
                ),
                recent_buy_count=self._list_count(
                    composite_raw.get("buy_signals"),
                    "composite_signal.buy_signals",
                    warnings,
                ),
                recent_sell_count=self._list_count(
                    composite_raw.get("sell_signals"),
                    "composite_signal.sell_signals",
                    warnings,
                ),
                signals=self._signal_points(composite_raw, warnings),
            )

        directional_status = "absent"
        if (
            "comprehensive_probability" in analysis
            and analysis.get("comprehensive_probability") is not None
        ):
            directional_status = "present_uncalibrated"
            warnings.append(
                "legacy directional evidence is uncalibrated and excluded from scoring"
            )

        recognized = sum(
            value is not None for value in (trend, multi_timeframe, divergence, composite)
        )
        if recognized == 0:
            status = "unavailable"
            warnings.append("typed technical summary has no recognized legacy evidence")
        elif warnings and any("ignored" in warning for warning in warnings):
            status = "partial"
        else:
            status = "available"

        return (
            TechnicalSummary(
                score_semantics=EVIDENCE_SCORE_SEMANTICS,
                trend_strength=trend,
                multi_timeframe=multi_timeframe,
                divergence=divergence,
                composite_signal=composite,
                legacy_directional_evidence_status=directional_status,
            ),
            status,
            tuple(dict.fromkeys(warnings)),
        )

    @staticmethod
    def _mapping(
        analysis: Mapping[str, Any], key: str, warnings: list[str]
    ) -> Mapping[str, Any]:
        value = analysis.get(key)
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return value
        warnings.append(f"ignored malformed {key}")
        return {}

    @staticmethod
    def _finite_number(
        value: Any,
        field: str,
        warnings: list[str],
        *,
        minimum: float,
        maximum: float,
    ) -> float | None:
        if value is None:
            return None
        if isinstance(value, (bool, str, bytes, Mapping)) or getattr(value, "ndim", 0) not in (0, None):
            warnings.append(f"ignored non-numeric {field}")
            return None
        try:
            native = float(value)
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"ignored non-numeric {field}")
            return None
        if not math.isfinite(native) or native < minimum or native > maximum:
            warnings.append(f"ignored non-finite or out-of-range {field}")
            return None
        return native

    @staticmethod
    def _text(value: Any, field: str, warnings: list[str]) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            warnings.append(f"ignored non-text {field}")
            return None
        native = str(value).strip()
        if not native:
            return None
        if len(native) > 512:
            warnings.append(f"ignored overlong {field}")
            return None
        return native

    @staticmethod
    def _native_bool(value: Any, field: str, warnings: list[str]) -> bool:
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if type(value).__module__.startswith("numpy") and type(value).__name__ in {
            "bool",
            "bool_",
        }:
            return bool(value)
        warnings.append(f"ignored non-boolean {field}")
        return False

    @classmethod
    def _native_count(
        cls,
        explicit: Any,
        fallback_list: Any,
        field: str,
        warnings: list[str],
    ) -> int:
        if explicit is not None:
            value = cls._finite_number(
                explicit, field, warnings, minimum=0, maximum=1_000_000
            )
            if value is not None and value.is_integer():
                return int(value)
            if value is not None:
                warnings.append(f"ignored non-integral {field}")
        return cls._list_count(fallback_list, field, warnings)

    @staticmethod
    def _list_count(value: Any, field: str, warnings: list[str]) -> int:
        if value is None:
            return 0
        if isinstance(value, (list, tuple)):
            return len(value)
        warnings.append(f"ignored non-list {field}")
        return 0

    @classmethod
    def _signal_points(
        cls, composite: Mapping[str, Any], warnings: list[str]
    ) -> tuple[CompositeSignalPoint, ...]:
        points: list[CompositeSignalPoint] = []
        for kind, key in (("buy", "buy_signals"), ("sell", "sell_signals")):
            raw_points = composite.get(key) or []
            if not isinstance(raw_points, (list, tuple)):
                continue
            for index, raw in enumerate(raw_points):
                if not isinstance(raw, Mapping):
                    continue
                time = cls._text(
                    raw.get("date"), f"composite_signal.{key}[{index}].date", warnings
                )
                price_value = raw.get("price")
                if price_value is None:
                    price_value = raw.get("close")
                price = cls._finite_number(
                    price_value,
                    f"composite_signal.{key}[{index}].price",
                    warnings,
                    minimum=0.000001,
                    maximum=1_000_000_000,
                )
                if time is None or price is None:
                    continue
                raw_confirmations = raw.get("confirmations") or []
                confirmations = tuple(
                    text
                    for confirmation_index, value in enumerate(raw_confirmations)
                    if (
                        text := cls._text(
                            value,
                            f"composite_signal.{key}[{index}].confirmations[{confirmation_index}]",
                            warnings,
                        )
                    )
                ) if isinstance(raw_confirmations, (list, tuple)) else ()
                confidence_level = cls._text(
                    raw.get("confidence_level"),
                    f"composite_signal.{key}[{index}].confidence_level",
                    warnings,
                )
                if confidence_level not in {None, "弱", "中", "中强", "强"}:
                    warnings.append(
                        f"ignored unsupported composite_signal.{key}[{index}].confidence_level"
                    )
                    confidence_level = None
                points.append(
                    CompositeSignalPoint(
                        time=time[:10],
                        kind=kind,
                        price=price,
                        strength=cls._text(
                            raw.get("strength"),
                            f"composite_signal.{key}[{index}].strength",
                            warnings,
                        ),
                        confidence_level=confidence_level,
                        confidence=cls._finite_number(
                            raw.get("confidence"),
                            f"composite_signal.{key}[{index}].confidence",
                            warnings,
                            minimum=0,
                            maximum=100,
                        ),
                        confirmations=confirmations,
                        confidence_components=cls._signal_confidence_components(
                            raw.get("confidence_components"),
                            f"composite_signal.{key}[{index}].confidence_components",
                            warnings,
                        ),
                    )
                )
        return tuple(sorted(points, key=lambda point: (point.time, point.kind)))

    @classmethod
    def _signal_confidence_components(
        cls,
        value: Any,
        field: str,
        warnings: list[str],
    ) -> SignalConfidenceComponents | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            warnings.append(f"ignored malformed {field}")
            return None
        semantics = cls._text(value.get("semantics"), f"{field}.semantics", warnings)
        if semantics != "signal_evidence_score_not_probability":
            warnings.append(f"ignored unsupported {field}.semantics")
            return None
        version = cls._text(value.get("version"), f"{field}.version", warnings)
        if version != "signal-evidence-v2":
            warnings.append(f"ignored unsupported {field}.version")
            return None
        raw_count = cls._finite_number(
            value.get("independent_bucket_count"),
            f"{field}.independent_bucket_count",
            warnings,
            minimum=0,
            maximum=100,
        )
        bucket_score = cls._finite_number(
            value.get("bucket_score"),
            f"{field}.bucket_score",
            warnings,
            minimum=0,
            maximum=100,
        )
        directional_trend_score = cls._finite_number(
            value.get("directional_trend_score"),
            f"{field}.directional_trend_score",
            warnings,
            minimum=0,
            maximum=100,
        )
        trend_evidence_status = cls._text(
            value.get("trend_evidence_status"),
            f"{field}.trend_evidence_status",
            warnings,
        )
        if trend_evidence_status not in {"available", "insufficient_history"}:
            warnings.append(f"ignored unsupported {field}.trend_evidence_status")
            trend_evidence_status = None
        if (
            raw_count is None
            or not raw_count.is_integer()
            or bucket_score is None
            or directional_trend_score is None
            or trend_evidence_status is None
        ):
            if raw_count is not None and not raw_count.is_integer():
                warnings.append(f"ignored non-integral {field}.independent_bucket_count")
            return None
        return SignalConfidenceComponents(
            version=version,
            semantics=semantics,
            independent_bucket_count=int(raw_count),
            bucket_score=bucket_score,
            directional_trend_score=directional_trend_score,
            trend_evidence_status=trend_evidence_status,
        )

    @classmethod
    def _strongest_divergence(
        cls, value: Any, warnings: list[str]
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return cls._text(value, "divergence.strongest_description", warnings)
        if isinstance(value, Mapping):
            parts = []
            for key in ("indicator", "type", "type_en", "direction", "reliability"):
                text = cls._text(
                    value.get(key), f"divergence.strongest.{key}", warnings
                )
                if text:
                    parts.append(text)
            description = cls._text(
                value.get("description"),
                "divergence.strongest.description",
                warnings,
            )
            return description or (" ".join(parts) if parts else None)
        warnings.append("ignored malformed divergence.strongest_description")
        return None


class ResearchViewMapper:
    def __init__(self, technical_mapper: TechnicalSummaryMapper | None = None) -> None:
        self.technical_mapper = technical_mapper or TechnicalSummaryMapper()

    def map(
        self,
        research: ResearchResult,
        market_view: MarketBarsViewResult,
        *,
        realtime_signal_preview: RealtimeSignalPreviewComputation | None = None,
    ) -> ResearchViewResult:
        # A request id alone is not a sufficient content binding: a malformed
        # adapter could reuse an id for different bars or provenance. Requiring
        # the complete immutable envelope prevents a mixed-source research
        # response while still allowing an equal validated model copy.
        if market_view.data != research.data:
            raise DecisionInputError("market view does not reference analyzed data")

        technical, technical_status, technical_warnings = self.technical_mapper.map(
            research.analysis
        )
        data = research.data
        data_ref = DataReference(
            request_id=data.request_id,
            instrument=data.instrument,
            timeframe=data.timeframe,
            purpose=data.purpose,
            adjustment=data.adjustment,
            provider=data.provider,
            mode=data.mode,
            quality=data.quality,
            as_of=data.as_of,
            fetched_at=data.fetched_at,
            last_bar_at=data.last_bar_at,
            timezone=data.timezone,
            freshness_seconds=float(data.freshness_seconds),
            is_synthetic=data.is_synthetic,
            source_chain=tuple(data.source_chain),
            source_warnings=tuple(data.warnings),
            bar_count=len(data.bars),
        )
        warnings = tuple(
            dict.fromkeys(
                [*data.warnings, *research.snapshot.warnings, *technical_warnings]
            )
        )
        preview = self._map_realtime_signal_preview(
            research,
            realtime_signal_preview,
        )
        return ResearchViewResult(
            schema_version=RESEARCH_VIEW_SCHEMA_VERSION,
            data_ref=data_ref,
            snapshot=research.snapshot,
            market_view=market_view,
            technical_summary=technical,
            section_status=ResearchSectionStatus(
                data_ref="available",
                snapshot="available",
                market_view="available",
                technical_summary=technical_status,
            ),
            warnings=warnings,
            realtime_signal_preview=preview,
        )

    def _map_realtime_signal_preview(
        self,
        research: ResearchResult,
        preview: RealtimeSignalPreviewComputation | None,
    ) -> RealtimeSignalPreview | None:
        if preview is None:
            return None
        if preview.daily_request_id != research.data.request_id:
            raise DecisionInputError(
                "realtime signal preview does not reference analyzed daily data"
            )
        mapping_warnings: list[str] = []
        signals = self.technical_mapper._signal_points(
            preview.composite_signals,
            mapping_warnings,
        )
        return RealtimeSignalPreview(
            schema_version=preview.schema_version,
            available=preview.available,
            status=preview.status,
            bar_time=preview.bar_time,
            completed_through=preview.completed_through,
            daily_request_id=preview.daily_request_id,
            quote_request_id=preview.quote_request_id,
            daily_provider=preview.daily_provider,
            quote_provider=preview.quote_provider,
            quote_mode=preview.quote_mode,
            quote_quality=preview.quote_quality,
            score_semantics=preview.score_semantics,
            signals=signals,
            warnings=tuple(
                dict.fromkeys([*preview.warnings, *mapping_warnings])
            ),
            advisory_only=preview.advisory_only,
            formal_use_eligible=preview.formal_use_eligible,
        )


class ResearchViewService:
    """Create one bounded research response from exactly one analysis call."""

    def __init__(
        self,
        research_use_case: ResearchAnalysisUseCase,
        market_view_service: MarketBarsViewService,
        mapper: ResearchViewMapper | None = None,
        realtime_preview_service: RealtimeSignalPreviewService | None = None,
    ) -> None:
        self.research_use_case = research_use_case
        self.market_view_service = market_view_service
        self.mapper = mapper or ResearchViewMapper()
        self.realtime_preview_service = (
            realtime_preview_service or RealtimeSignalPreviewService()
        )

    def analyze(
        self,
        query: DataQuery,
        *,
        info: Mapping[str, Any] | None = None,
        benchmark_query: DataQuery | None = None,
        has_position: bool = False,
        provisional_quote: QuoteEnvelope | None = None,
    ) -> ResearchViewResult:
        research = self.research_use_case.analyze(
            query,
            info=info,
            benchmark_query=benchmark_query,
            has_position=has_position,
        )
        try:
            market_view = self.market_view_service.build_view(research.data)
        except MarketViewInputError as exc:
            raise DecisionInputError(str(exc)) from exc
        preview = (
            self.realtime_preview_service.build(research.data, provisional_quote)
            if provisional_quote is not None
            else None
        )
        return self.mapper.map(
            research,
            market_view,
            realtime_signal_preview=preview,
        )
