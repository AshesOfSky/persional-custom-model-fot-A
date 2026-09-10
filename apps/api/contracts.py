from __future__ import annotations

from datetime import datetime

import math

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from custom_model.application.technical_structure_service import ChanContinuity, SectionAvailability, chan_state_continuity, technical_structure_state_key, verify_chan_state

from custom_model.application.watchlist_service import WatchlistService

from custom_model.domain.models import Adjustment, AnalysisSnapshot, AssetType, DataEnvelope, DataMode, DataQuery, DataPurpose, FetchOrigin, FundamentalEvidenceBasis, FundamentalFieldStatus, FundamentalQuery, InstrumentId, QualityStatus, QuoteEnvelope, QuoteTemporalMode, SignalStatus, Timeframe

from custom_model.domain.watchlist import WatchlistRole, validate_watchlist_assignment

class ApiModel(BaseModel):
    """Strict transport model used as the OpenAPI source of truth."""

    model_config = ConfigDict(extra="forbid")


class ProvisionalQuoteInput(QuoteEnvelope):
    """Native quote echoed by the browser for advisory-only signal preview."""


class AnalysisRequest(ApiModel):
    query: DataQuery
    benchmark_query: DataQuery | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    has_position: bool = False
    provisional_quote: ProvisionalQuoteInput | None = None


    @model_validator(mode="after")
    def research_queries_are_aligned(self) -> "AnalysisRequest":
        if self.query.purpose is not DataPurpose.RESEARCH:
            raise ValueError("analysis query purpose must be research")
        benchmark = self.benchmark_query
        if benchmark is not None:
            if benchmark.purpose is not DataPurpose.RESEARCH:
                raise ValueError("benchmark query purpose must be research")
            if benchmark.instrument.asset_type not in {AssetType.INDEX, AssetType.ETF}:
                raise ValueError("benchmark instrument must be an index or ETF")
            for field_name in ("timeframe", "start", "end", "as_of"):
                if getattr(benchmark, field_name) != getattr(self.query, field_name):
                    raise ValueError(f"benchmark {field_name} must match analysis query")
        return self._validate_provisional_quote()

    def _validate_provisional_quote(self) -> "AnalysisRequest":
        quote = self.provisional_quote
        if quote is None:
            return self
        if self.query.timeframe is not Timeframe.D1:
            raise ValueError("provisional quote is only supported for daily analysis")
        if quote.instrument != self.query.instrument:
            raise ValueError("provisional quote instrument must match analysis query")
        if quote.purpose is not self.query.purpose:
            raise ValueError("provisional quote purpose must match analysis query")
        if quote.temporal_mode is not QuoteTemporalMode.LATEST:
            raise ValueError("provisional quote must use latest temporal mode")
        if quote.as_of != self.query.as_of:
            raise ValueError("provisional quote as_of must match analysis query")
        return self


class RealtimeAnalysisPreviewRequest(AnalysisRequest):
    """A full provisional analysis request bound to one latest native quote."""

    provisional_quote: ProvisionalQuoteInput

    @model_validator(mode="after")
    def full_preview_has_no_benchmark(self) -> "RealtimeAnalysisPreviewRequest":
        if self.benchmark_query is not None:
            raise ValueError("realtime full analysis preview does not support a benchmark")
        if self.query.adjustment is not Adjustment.FORWARD:
            raise ValueError("realtime full analysis preview requires forward adjustment")
        return self


class InstrumentSearchItem(ApiModel):
    instrument: InstrumentId
    display_name: str = Field(min_length=1, max_length=128)
    legacy_code: str = Field(min_length=1, max_length=32)
    source: str = Field(min_length=1, max_length=64)
    match_score: float = Field(ge=0, le=100)


class InstrumentSearchResponse(ApiModel):
    query: str
    items: list[InstrumentSearchItem]


class IndicatorPointView(ApiModel):
    timestamp: datetime
    value: float = Field(allow_inf_nan=False)


class IndicatorSeriesView(ApiModel):
    points: list[IndicatorPointView]


class CoreIndicatorProfileView(ApiModel):
    name: Literal["legacy-core"]
    version: Literal["1.0.0"]
    formula_source: Literal["modules.technical.add_all_indicators"]
    ema_periods: tuple[int, ...]
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bollinger_period: int
    bollinger_std_dev: float = Field(allow_inf_nan=False)
    kdj_n: int
    kdj_m1: int
    kdj_m2: int
    rsi_period: int
    atr_period: int


class EmaIndicatorsView(ApiModel):
    ema8: IndicatorSeriesView
    ema13: IndicatorSeriesView
    ema21: IndicatorSeriesView
    ema55: IndicatorSeriesView
    ema144: IndicatorSeriesView
    ema169: IndicatorSeriesView
    ema288: IndicatorSeriesView
    ema338: IndicatorSeriesView


class MacdIndicatorsView(ApiModel):
    dif: IndicatorSeriesView
    dea: IndicatorSeriesView
    hist: IndicatorSeriesView


class BollingerIndicatorsView(ApiModel):
    mid: IndicatorSeriesView
    upper: IndicatorSeriesView
    lower: IndicatorSeriesView


class KdjIndicatorsView(ApiModel):
    k: IndicatorSeriesView
    d: IndicatorSeriesView
    j: IndicatorSeriesView


class CoreIndicatorsView(ApiModel):
    ema: EmaIndicatorsView
    macd: MacdIndicatorsView
    bollinger: BollingerIndicatorsView
    kdj: KdjIndicatorsView
    rsi: IndicatorSeriesView
    atr: IndicatorSeriesView


class MarketBarsView(ApiModel):
    schema_version: Literal["market-bars-view/v1"]
    data_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data: DataEnvelope
    indicator_profile: CoreIndicatorProfileView
    indicators: CoreIndicatorsView


class QuoteViewResponse(ApiModel):
    schema_version: Literal["quote-view/v2"]
    quote: QuoteEnvelope
    formal_use_eligible: bool


class QuoteRefreshRequest(ApiModel):
    instrument: InstrumentId


class QuoteRefreshResponse(ApiModel):
    schema_version: Literal["quote-refresh/v1"] = "quote-refresh/v1"
    quote: QuoteEnvelope
    provider_unchanged: bool
    cooldown_seconds: int = Field(ge=1, le=3600)
    origin: Literal[FetchOrigin.MANUAL_REFRESH] = FetchOrigin.MANUAL_REFRESH
    cache_bypassed: Literal[True] = True
    alert_event_created: Literal[False] = False
    external_notification_sent: Literal[False] = False
    trade_created: Literal[False] = False


class ResearchDataReferenceView(ApiModel):
    request_id: str = Field(min_length=1)
    instrument: InstrumentId
    timeframe: Timeframe
    purpose: DataPurpose
    adjustment: Adjustment
    provider: str = Field(min_length=1)
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    last_bar_at: datetime | None
    timezone: str = Field(min_length=1)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    is_synthetic: bool
    source_chain: list[str]
    source_warnings: list[str]
    bar_count: int = Field(ge=0)


class TrendStrengthEvidenceView(ApiModel):
    score: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    level: str | None = Field(default=None, max_length=512)
    signal_summary: str | None = Field(default=None, max_length=512)


class MultiTimeframeEvidenceView(ApiModel):
    alignment: str | None = Field(default=None, max_length=512)
    dominant_trend: str | None = Field(default=None, max_length=512)
    summary: str | None = Field(default=None, max_length=512)
    alignment_score: float | None = Field(
        default=None, ge=0, le=100, allow_inf_nan=False
    )
    mtf_score: float | None = Field(
        default=None, ge=-2, le=2, allow_inf_nan=False
    )


class DivergenceEvidenceView(ApiModel):
    active_count: int = Field(ge=0)
    bullish_active: bool
    bearish_active: bool
    strongest_description: str | None = Field(default=None, max_length=512)


class SignalConfidenceComponentsView(ApiModel):
    version: Literal["signal-evidence-v2"]
    semantics: Literal["signal_evidence_score_not_probability"]
    independent_bucket_count: int = Field(ge=2)
    bucket_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    directional_trend_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    trend_evidence_status: Literal["available", "insufficient_history"]


class CompositeSignalPointView(ApiModel):
    time: str = Field(min_length=1, max_length=32)
    kind: Literal["buy", "sell"]
    price: float = Field(gt=0, allow_inf_nan=False)
    strength: str | None = Field(default=None, max_length=32)
    confidence_level: Literal["弱", "中", "中强", "强"] | None = None
    confidence: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    confirmations: list[str] = Field(default_factory=list)
    confidence_components: SignalConfidenceComponentsView | None = None

    @model_validator(mode="after")
    def v2_score_matches_components(self) -> "CompositeSignalPointView":
        components = self.confidence_components
        if components is None:
            return self
        expected_bucket = min(
            95.0,
            30.0 + components.independent_bucket_count * 14.0,
        )
        if abs(components.bucket_score - expected_bucket) > 0.11:
            raise ValueError("bucket_score does not match independent_bucket_count")
        if components.trend_evidence_status == "available":
            expected_score = min(
                95.0,
                components.bucket_score * 0.60
                + components.directional_trend_score * 0.40,
            )
        else:
            expected_score = components.bucket_score * 0.60
        if self.confidence is None or abs(self.confidence - round(expected_score, 1)) > 0.11:
            raise ValueError("confidence does not match signal-evidence-v2 components")
        expected_level = (
            "强"
            if self.confidence >= 75
            else "中强"
            if self.confidence >= 65
            else "中"
            if self.confidence >= 50
            else "弱"
        )
        if self.confidence_level != expected_level:
            raise ValueError("confidence_level does not match signal-evidence-v2 score")
        expected_strength = (
            "强" if components.independent_bucket_count >= 3 else "中"
        )
        if self.strength is not None and self.strength != expected_strength:
            raise ValueError("strength does not match independent evidence breadth")
        return self


class TechnicalStructureRequest(AnalysisRequest):
    """Research structure request with optional client-owned Chan continuation."""

    prior_state: dict[str, JsonValue] | None = None


class CompositeSignalEvidenceView(ApiModel):
    current_signal: str | None = Field(default=None, max_length=512)
    strength: str | None = Field(default=None, max_length=512)
    recent_buy_count: int = Field(ge=0)
    recent_sell_count: int = Field(ge=0)
    signals: list[CompositeSignalPointView] = Field(default_factory=list)


class RealtimeSignalPreviewView(ApiModel):
    schema_version: Literal["realtime-signal-preview/v1"]
    available: bool
    status: Literal[SignalStatus.PROVISIONAL]
    bar_time: str = Field(min_length=10, max_length=10)
    completed_through: datetime | None
    daily_request_id: str = Field(min_length=1)
    quote_request_id: str = Field(min_length=1)
    daily_provider: str = Field(min_length=1)
    quote_provider: str = Field(min_length=1)
    quote_mode: Literal[DataMode.LIVE, DataMode.DELAYED]
    quote_quality: Literal[QualityStatus.VALID, QualityStatus.STALE]
    score_semantics: Literal["signal_evidence_score_not_probability"]
    signals: list[CompositeSignalPointView] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    advisory_only: Literal[True]
    formal_use_eligible: Literal[False]

    @model_validator(mode="after")
    def unavailable_preview_has_no_signal(self) -> "RealtimeSignalPreviewView":
        if not self.available and self.signals:
            raise ValueError("unavailable realtime preview cannot contain signals")
        if any(signal.time != self.bar_time for signal in self.signals):
            raise ValueError("realtime preview signals must belong to the provisional bar")
        return self


class TechnicalSummaryView(ApiModel):
    score_semantics: Literal["evidence_score_not_probability"]
    trend_strength: TrendStrengthEvidenceView | None
    multi_timeframe: MultiTimeframeEvidenceView | None
    divergence: DivergenceEvidenceView | None
    composite_signal: CompositeSignalEvidenceView | None
    legacy_directional_evidence_status: Literal[
        "absent", "present_uncalibrated"
    ]


class ResearchSectionStatusView(ApiModel):
    data_ref: Literal["available"]
    snapshot: Literal["available"]
    market_view: Literal["available"]
    technical_summary: Literal["available", "partial", "unavailable"]


class ResearchAnalysisResponse(ApiModel):
    schema_version: Literal["research-view/v1"]
    data_ref: ResearchDataReferenceView
    snapshot: AnalysisSnapshot
    market_view: MarketBarsView
    technical_summary: TechnicalSummaryView
    section_status: ResearchSectionStatusView
    warnings: list[str]
    realtime_signal_preview: RealtimeSignalPreviewView | None = None


class StructurePointView(ApiModel):
    time: str | None = Field(default=None, max_length=128)
    value: float = Field(allow_inf_nan=False)


class TechnicalStructureItemView(ApiModel):
    kind: str = Field(min_length=1, max_length=128)
    source: str = Field(min_length=1, max_length=1024)
    method_version: str = Field(min_length=1, max_length=1024)
    status: SignalStatus
    confirmed: bool
    provisional: bool
    label: str | None = Field(default=None, max_length=1024)
    role: str | None = Field(default=None, max_length=1024)
    direction: str | None = Field(default=None, max_length=1024)
    time: str | None = Field(default=None, max_length=128)
    end_time: str | None = Field(default=None, max_length=128)
    confirmed_time: str | None = Field(default=None, max_length=128)
    price: float | None = Field(default=None, allow_inf_nan=False)
    end_price: float | None = Field(default=None, allow_inf_nan=False)
    high: float | None = Field(default=None, allow_inf_nan=False)
    low: float | None = Field(default=None, allow_inf_nan=False)
    value: float | None = Field(default=None, allow_inf_nan=False)
    reference_value: float | None = Field(default=None, allow_inf_nan=False)
    ratio: str | None = Field(default=None, max_length=128)
    confidence: float | None = Field(default=None, allow_inf_nan=False)
    count: int | None = Field(default=None, ge=0)
    neckline_price: float | None = Field(default=None, allow_inf_nan=False)
    target_price: float | None = Field(default=None, allow_inf_nan=False)
    stop_price: float | None = Field(default=None, allow_inf_nan=False)
    description: str | None = Field(default=None, max_length=1024)
    basis: str | None = Field(default=None, max_length=1024)
    score_eligible: Literal[False] = False
    alert_eligible: Literal[False] = False
    points: list[StructurePointView]
    tags: list[str]

    @model_validator(mode="after")
    def confirmation_flags_match_status(self) -> "TechnicalStructureItemView":
        expected_confirmed = self.status is SignalStatus.CONFIRMED
        if self.confirmed is not expected_confirmed:
            raise ValueError("confirmed must match status")
        if self.provisional is self.confirmed:
            raise ValueError("provisional must be the inverse of confirmed")
        return self


class TechnicalStructureSectionView(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    status: SectionAvailability
    source: str | None = Field(default=None, max_length=1024)
    method_version: str | None = Field(default=None, max_length=1024)
    summary: str | None = Field(default=None, max_length=1024)
    items: list[TechnicalStructureItemView]
    warnings: list[str]


class TechnicalStructureSectionsView(ApiModel):
    support_resistance: TechnicalStructureSectionView
    fibonacci: TechnicalStructureSectionView
    gann: TechnicalStructureSectionView
    elliott: TechnicalStructureSectionView
    chan: TechnicalStructureSectionView
    candlestick: TechnicalStructureSectionView
    chart_patterns: TechnicalStructureSectionView
    divergences: TechnicalStructureSectionView
    volume_price: TechnicalStructureSectionView
    multi_timeframe: TechnicalStructureSectionView
    overlay: TechnicalStructureSectionView


class TechnicalStructureSectionStatusView(ApiModel):
    support_resistance: SectionAvailability
    fibonacci: SectionAvailability
    gann: SectionAvailability
    elliott: SectionAvailability
    chan: SectionAvailability
    candlestick: SectionAvailability
    chart_patterns: SectionAvailability
    divergences: SectionAvailability
    volume_price: SectionAvailability
    multi_timeframe: SectionAvailability
    overlay: SectionAvailability


class TechnicalStructureDataReferenceView(ApiModel):
    request_id: str = Field(min_length=1, max_length=256)
    instrument: InstrumentId
    timeframe: Timeframe
    purpose: DataPurpose
    adjustment: Adjustment
    provider: str = Field(min_length=1, max_length=128)
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    last_bar_at: datetime | None
    timezone: str = Field(min_length=1, max_length=128)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    is_synthetic: bool
    source_chain: list[str]
    lineage_request_ids: list[str]
    bar_count: int = Field(ge=1)


class ChanConfirmedStructureView(ApiModel):
    direction: Literal["up", "down"]
    start_time: str = Field(alias="startTime", min_length=1, max_length=128)
    end_time: str = Field(alias="endTime", min_length=1, max_length=128)
    start_price: float = Field(alias="startPrice", allow_inf_nan=False)
    end_price: float = Field(alias="endPrice", allow_inf_nan=False)
    high: float = Field(allow_inf_nan=False)
    low: float = Field(allow_inf_nan=False)
    confirmed_time: str = Field(
        alias="confirmedTime", min_length=1, max_length=128
    )
    basis: str = Field(min_length=1, max_length=1024)
    structure_id: str = Field(
        alias="structureId", pattern=r"^chan-(?:stable-bi|segment|same-level)-[0-9a-f]{20}$"
    )
    validation_basis: str = Field(
        alias="validationBasis", min_length=1, max_length=1024
    )
    validated: Literal[True]
    confirmed: Literal[True]
    status: Literal["confirmed"]


class ChanStableBiStateView(ChanConfirmedStructureView):
    kind: Literal["stable_bi"]
    endpoint_confirmed_time: str = Field(
        alias="endpointConfirmedTime", min_length=1, max_length=128
    )


class ChanSegmentStateView(ChanConfirmedStructureView):
    kind: Literal["segment"]
    stroke_count: int = Field(alias="strokeCount", ge=3)


class ChanSameLevelStateView(ChanConfirmedStructureView):
    kind: Literal["same_level"]
    type: Literal["same_level_trend_leg"]
    level: int = Field(ge=1)
    segment_count: int = Field(alias="segmentCount", ge=3)
    child_structure_ids: list[str] = Field(alias="childStructureIds")


class ChanConfirmedStreamsView(ApiModel):
    stable_bi: list[ChanStableBiStateView] = Field(alias="stableBi")
    segment: list[ChanSegmentStateView]
    same_level: list[ChanSameLevelStateView] = Field(alias="sameLevel")


class ChanStateView(ApiModel):
    version: Literal["chan-state-1"]
    method_version: Literal["chan-local-3"] = Field(alias="methodVersion")
    reliability_version: Literal["chan-reliability-1"] = Field(
        alias="reliabilityVersion"
    )
    state_key: str = Field(alias="stateKey", min_length=1, max_length=512)
    origin_time: str | None = Field(default=None, alias="originTime", max_length=128)
    last_input_time: str | None = Field(
        default=None, alias="lastInputTime", max_length=128
    )
    continuity_status: Literal[
        "initialized",
        "continued",
        "invalid_prior_rejected",
        "conflict_prior_rejected",
    ] = Field(alias="continuityStatus")
    prefix_stable: bool = Field(alias="prefixStable")
    persistence_eligible: Literal[True] = Field(alias="persistenceEligible")
    confirmed_streams: ChanConfirmedStreamsView = Field(alias="confirmedStreams")
    content_hash: str = Field(
        alias="contentHash", pattern=r"^chan-state-[0-9a-f]{20}$"
    )

    @model_validator(mode="after")
    def canonical_hash_is_valid(self) -> "ChanStateView":
        value = self.model_dump(mode="json", by_alias=True)
        if not verify_chan_state(value, self.state_key):
            raise ValueError("current_state failed chan-state-1 verification")
        return self


class TechnicalStructureResponse(ApiModel):
    schema_version: Literal["technical-structure-view/v1"]
    data_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    data_ref: TechnicalStructureDataReferenceView
    snapshot: AnalysisSnapshot
    engine: str = Field(min_length=1, max_length=1024)
    engine_note: str | None = Field(default=None, max_length=1024)
    sections: TechnicalStructureSectionsView
    section_status: TechnicalStructureSectionStatusView
    current_state: ChanStateView
    continuity: ChanContinuity
    warnings: list[str]

    @model_validator(mode="after")
    def chan_state_matches_response_identity(self) -> "TechnicalStructureResponse":
        expected_key = technical_structure_state_key(
            self.data_ref.instrument,
            self.data_ref.timeframe,
        )
        if self.current_state.state_key != expected_key:
            raise ValueError("current_state does not match response instrument/timeframe")
        raw_state = self.current_state.model_dump(mode="json", by_alias=True)
        if chan_state_continuity(raw_state) != self.continuity:
            raise ValueError("continuity does not match current_state")
        return self


class RealtimeAnalysisBaselineView(ApiModel):
    request_id: str = Field(min_length=1, max_length=256)
    snapshot_id: str = Field(min_length=1, max_length=256)
    instrument: InstrumentId
    timeframe: Timeframe
    as_of: datetime
    completed_through: datetime
    evidence_score: float = Field(ge=0, le=100, allow_inf_nan=False)
    score_semantics: Literal["evidence_score_not_probability"]
    provider: str = Field(min_length=1, max_length=256)
    mode: DataMode
    quality: QualityStatus
    adjustment: Adjustment


class RealtimeAnalysisPreviewResponse(ApiModel):
    schema_version: Literal["realtime-analysis-preview/v1"]
    advisory_only: Literal[True]
    formal_use_eligible: Literal[False]
    session_label: str = Field(min_length=1, max_length=64)
    quote: ProvisionalQuoteInput
    completed_baseline: RealtimeAnalysisBaselineView
    preview: ResearchAnalysisResponse
    structure_data_ref: TechnicalStructureDataReferenceView
    structure_data_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    structure_engine: str = Field(min_length=1, max_length=1024)
    structures: TechnicalStructureSectionsView
    structure_status: TechnicalStructureSectionStatusView
    warnings: list[str]

    @model_validator(mode="after")
    def preview_is_provisional_and_quote_bound(self) -> "RealtimeAnalysisPreviewResponse":
        if self.preview.snapshot.bar_status is not SignalStatus.PROVISIONAL:
            raise ValueError("realtime full analysis snapshot must be provisional")
        if self.preview.data_ref.last_bar_at != self.quote.data_time:
            raise ValueError("realtime full analysis must end at the quote data time")
        if self.preview.data_ref.instrument != self.quote.instrument:
            raise ValueError("realtime full analysis instrument must match the quote")
        if (
            self.completed_baseline.instrument != self.quote.instrument
            or self.completed_baseline.timeframe != self.preview.data_ref.timeframe
            or self.completed_baseline.adjustment != self.preview.data_ref.adjustment
            or self.completed_baseline.as_of != self.quote.as_of
            or self.completed_baseline.completed_through >= self.quote.data_time
        ):
            raise ValueError("completed baseline must precede and match the preview query")
        if (
            self.structure_data_ref.request_id != self.preview.data_ref.request_id
            or self.structure_data_ref.instrument != self.preview.data_ref.instrument
            or self.structure_data_ref.last_bar_at != self.preview.data_ref.last_bar_at
        ):
            raise ValueError("realtime structures must reference the preview data")
        if self.structure_data_fingerprint != self.preview.market_view.data_fingerprint:
            raise ValueError("realtime structures must match the preview market-data fingerprint")
        if self.formal_use_eligible is not False or self.advisory_only is not True:
            raise ValueError("realtime full analysis must remain advisory-only")
        return self


class FundamentalSnapshotRequest(ApiModel):
    query: FundamentalQuery


class FundamentalRawValueView(ApiModel):
    """Lossless tagged JSON evidence without an open-ended object schema."""

    kind: Literal["string", "integer", "number", "boolean", "array", "object"]
    string_value: str | None = None
    integer_value: int | None = None
    number_value: float | None = Field(default=None, allow_inf_nan=False)
    boolean_value: bool | None = None
    items: list["FundamentalRawValueView"] = Field(default_factory=list)
    entries: list["FundamentalRawEntryView"] = Field(default_factory=list)

    @model_validator(mode="after")
    def value_matches_kind(self) -> "FundamentalRawValueView":
        populated = {
            "string": self.string_value is not None,
            "integer": self.integer_value is not None,
            "number": self.number_value is not None,
            "boolean": self.boolean_value is not None,
            "array": bool(self.items),
            "object": bool(self.entries),
        }
        if not populated[self.kind] or sum(populated.values()) != 1:
            raise ValueError("raw evidence value must match exactly one tagged kind")
        return self


class FundamentalRawEntryView(ApiModel):
    key: str = Field(min_length=1, max_length=256)
    value: FundamentalRawValueView


class FundamentalFieldView(ApiModel):
    name: str = Field(min_length=1, max_length=128)
    source_field: str = Field(min_length=1, max_length=256)
    raw_value: FundamentalRawValueView
    normalized_value: str | int | float | bool
    unit: str | None = Field(default=None, min_length=1, max_length=64)
    basis: FundamentalEvidenceBasis
    status: FundamentalFieldStatus
    period_end: datetime | None = None
    published_at: datetime | None = None
    market_time: datetime | None = None

    @field_validator("normalized_value")
    @classmethod
    def normalized_value_is_finite(
        cls, value: str | int | float | bool
    ) -> str | int | float | bool:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("normalized_value must be finite")
        return value


class FundamentalEnvelopeView(ApiModel):
    instrument: InstrumentId
    purpose: DataPurpose
    provider: str = Field(min_length=1, max_length=128)
    mode: DataMode
    quality: QualityStatus
    as_of: datetime
    fetched_at: datetime
    data_time: datetime
    data_time_basis: Literal["latest_field_evidence_time"]
    timezone: str = Field(min_length=1, max_length=128)
    currency: str = Field(min_length=3, max_length=3)
    freshness_seconds: float = Field(ge=0, allow_inf_nan=False)
    fields: list[FundamentalFieldView] = Field(min_length=1)
    warnings: list[str]
    is_synthetic: bool
    source_chain: list[str] = Field(min_length=1)
    request_id: str = Field(min_length=1, max_length=256)


class FundamentalCoverageView(ApiModel):
    required_fields: list[str] = Field(min_length=1)
    verified_financial_fields: list[str]
    missing_required_fields: list[str]
    minimum_verified_financial_fields: int = Field(ge=1)
    passed: bool


class FundamentalSnapshotResponse(ApiModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
    instrument: InstrumentId
    purpose: DataPurpose
    as_of: datetime
    engine_version: str = Field(min_length=1, max_length=128)
    data: FundamentalEnvelopeView
    coverage: FundamentalCoverageView
    use_case: Literal["risk_and_quality_filter"]
    formal_use_eligible: bool


_WATCHLIST_MUTABLE_FIELDS = frozenset({"role", "display_name", "note", "pinned"})


class WatchlistEntryCreateRequest(ApiModel):
    """Closed configuration request; it performs no market-data side effect."""

    instrument: InstrumentId
    role: WatchlistRole
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=256)

    @field_validator("display_name", "note")
    @classmethod
    def normalize_watchlist_text(cls, value: str, info: Any) -> str:
        normalized = value.strip()
        if info.field_name == "display_name" and not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def admitted_watchlist_assignment(self) -> "WatchlistEntryCreateRequest":
        validate_watchlist_assignment(self.instrument, self.role)
        return self


class WatchlistEntryPatchRequest(ApiModel):
    expected_revision: int = Field(ge=1)
    role: WatchlistRole | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=256)
    pinned: bool | None = None

    @field_validator("display_name", "note")
    @classmethod
    def normalize_optional_watchlist_text(
        cls, value: str | None, info: Any
    ) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if info.field_name == "display_name" and not normalized:
            raise ValueError("display_name cannot be blank")
        return normalized

    @model_validator(mode="after")
    def contains_a_watchlist_change(self) -> "WatchlistEntryPatchRequest":
        supplied = self.model_fields_set & _WATCHLIST_MUTABLE_FIELDS
        if not supplied:
            raise ValueError("patch must include at least one mutable field")
        if any(getattr(self, field_name) is None for field_name in supplied):
            raise ValueError("patch fields cannot be null; use an empty note to clear it")
        return self


class WatchlistOrderItemRequest(ApiModel):
    entry_id: str = Field(min_length=1, max_length=64)
    expected_revision: int = Field(ge=1)

    @field_validator("entry_id")
    @classmethod
    def normalize_order_entry_id(cls, value: str) -> str:
        return value.strip()


class WatchlistEntriesReorderRequest(ApiModel):
    scope: Literal["securities", "sectors"]
    role: WatchlistRole
    ordered_items: list[WatchlistOrderItemRequest] = Field(
        min_length=1,
        max_length=100,
    )

    @model_validator(mode="after")
    def unique_order_entry_ids(self) -> "WatchlistEntriesReorderRequest":
        entry_ids = [item.entry_id for item in self.ordered_items]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("ordered_items cannot contain duplicate entry_id values")
        return self


class WatchlistEntryView(ApiModel):
    entry_id: str = Field(min_length=1, max_length=64)
    instrument: InstrumentId
    role: WatchlistRole
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(max_length=256)
    pinned: bool = False
    sort_order: int = Field(default=0, ge=0, le=10_000)
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)
    deleted_at: datetime | None = None


class WatchlistLimitsView(ApiModel):
    max_core_stocks: Literal[10] = WatchlistService.MAX_CORE_STOCKS
    max_active_stocks: Literal[30] = WatchlistService.MAX_ACTIVE_STOCKS
    max_active_entries: Literal[100] = WatchlistService.MAX_ACTIVE_ENTRIES


def _watchlist_warnings() -> list[str]:
    return [
        "watchlist membership is configuration only and does not activate monitoring",
        "this operation does not create an alert rule, request market data, or create a trade",
    ]


class _WatchlistBoundaryResponse(ApiModel):
    configuration_only: Literal[True] = True
    monitoring_active: Literal[False] = False
    alert_rule_created: Literal[False] = False
    market_data_requested: Literal[False] = False
    trade_created: Literal[False] = False
    warnings: list[str] = Field(default_factory=_watchlist_warnings)


class WatchlistEntryResponse(_WatchlistBoundaryResponse):
    schema_version: Literal["watchlist-entry/v1"] = "watchlist-entry/v1"
    action: Literal["created", "read", "updated", "deleted"]
    entry: WatchlistEntryView


class WatchlistEntryListResponse(_WatchlistBoundaryResponse):
    schema_version: Literal["watchlist-list/v1"] = "watchlist-list/v1"
    total: int = Field(ge=0)
    items: list[WatchlistEntryView]
    limits: WatchlistLimitsView = Field(default_factory=WatchlistLimitsView)


class ProviderHealthInfo(ApiModel):
    provider: str
    healthy: bool
    production_ready: bool
    checked_at: datetime
    message: str | None = None


class WorkerHealthInfo(ApiModel):
    status: Literal["online", "stale", "offline"]
    heartbeat_at: datetime | None = None
    detail: str | None = None


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    service: Literal["custom-model-api"] = "custom-model-api"
    api_version: Literal["v1"] = "v1"
    analysis_ready: bool
    providers: list[ProviderHealthInfo] = Field(default_factory=list)
    worker: WorkerHealthInfo = Field(
        default_factory=lambda: WorkerHealthInfo(status="offline")
    )
    composition_error: str | None = None


class ValidationIssue(ApiModel):
    location: list[str | int] = Field(default_factory=list)
    message: str
    issue_type: str


class ErrorDetail(ApiModel):
    code: str
    message: str
    retryable: bool = False
    validation: list[ValidationIssue] = Field(default_factory=list)


class ErrorResponse(ApiModel):
    error: ErrorDetail


