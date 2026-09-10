from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from typing import Any, Literal, Protocol

from custom_model.application.analysis_service import DecisionInputError
from custom_model.application.research import ResearchAnalysisUseCase, ResearchResult
from custom_model.domain.models import (
    Adjustment,
    AnalysisSnapshot,
    DataEnvelope,
    DataMode,
    DataPurpose,
    DataQuery,
    InstrumentId,
    MarketRegimeContext,
    PairCandidate,
    QualityStatus,
    SectorOpportunity,
    SignalStatus,
    Timeframe,
)


TECHNICAL_STRUCTURE_SCHEMA_VERSION = "technical-structure-view/v1"
ChanContinuity = Literal["initialized", "continued", "rejected"]


def technical_structure_state_key(
    instrument: InstrumentId,
    timeframe: Timeframe,
) -> str:
    """Bind Chan continuation to the complete instrument identity and timeframe."""

    return (
        "chan-state-1|"
        f"market={instrument.market.value}|"
        f"exchange={instrument.exchange.value}|"
        f"asset_type={instrument.asset_type.value}|"
        f"symbol={instrument.symbol}|"
        f"currency={instrument.currency}|"
        f"timeframe={timeframe.value}"
    )


def verify_chan_state(value: object, expected_state_key: str) -> bool:
    """Verify the existing ``chan-state-1`` canonical content-hash contract."""

    if not isinstance(value, Mapping):
        return False
    if (
        value.get("version") != "chan-state-1"
        or value.get("methodVersion") != "chan-local-3"
        or value.get("reliabilityVersion") != "chan-reliability-1"
        or value.get("stateKey") != expected_state_key
        or value.get("persistenceEligible") is not True
    ):
        return False
    supplied = value.get("contentHash")
    if not isinstance(supplied, str):
        return False
    body = {key: item for key, item in value.items() if key != "contentHash"}
    encoded = json.dumps(
        body,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = f"chan-state-{hashlib.sha256(encoded).hexdigest()[:20]}"
    return supplied == expected


def chan_state_continuity(value: Mapping[str, Any]) -> ChanContinuity:
    status = value.get("continuityStatus")
    if status == "initialized":
        return "initialized"
    if status == "continued":
        return "continued"
    if status in {"invalid_prior_rejected", "conflict_prior_rejected"}:
        return "rejected"
    raise TechnicalStructureBuildError(
        f"unsupported Chan continuity status: {status!r}"
    )


class TechnicalStructureInputError(DecisionInputError):
    """The requested structure view cannot satisfy the formal data contract."""


class TechnicalStructureBuildError(RuntimeError):
    """A configured structure builder failed before a typed result was produced."""


class SectionAvailability(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class StructurePoint:
    time: str | None
    value: float


@dataclass(frozen=True)
class TechnicalStructureItem:
    """One fixed-shape, provenance-bearing structure item.

    The intentionally broad but finite field set replaces legacy free-form
    dictionaries.  No arbitrary payload is retained, so an adapter cannot
    leak an unknown field into OpenAPI or a UI by accident.
    """

    kind: str
    source: str
    method_version: str
    status: SignalStatus
    confirmed: bool
    provisional: bool
    label: str | None = None
    role: str | None = None
    direction: str | None = None
    time: str | None = None
    end_time: str | None = None
    confirmed_time: str | None = None
    price: float | None = None
    end_price: float | None = None
    high: float | None = None
    low: float | None = None
    value: float | None = None
    reference_value: float | None = None
    ratio: str | None = None
    confidence: float | None = None
    count: int | None = None
    neckline_price: float | None = None
    target_price: float | None = None
    stop_price: float | None = None
    description: str | None = None
    basis: str | None = None
    structure_id: str | None = None
    parent_structure_id: str | None = None
    level: int | None = None
    validation_basis: str | None = None
    validated: bool = False
    score_eligible: bool = False
    alert_eligible: bool = False
    points: tuple[StructurePoint, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        expected_confirmed = self.status is SignalStatus.CONFIRMED
        if self.confirmed is not expected_confirmed:
            raise ValueError("confirmed must match status")
        if self.provisional is self.confirmed:
            raise ValueError("provisional must be the inverse of confirmed")
        if (self.score_eligible or self.alert_eligible) and not (
            self.validated and self.confirmed
        ):
            raise ValueError("score/alert eligibility requires a validated confirmed item")


@dataclass(frozen=True)
class TechnicalStructureSection:
    name: str
    status: SectionAvailability
    source: str | None
    method_version: str | None
    summary: str | None
    items: tuple[TechnicalStructureItem, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TechnicalStructureSections:
    support_resistance: TechnicalStructureSection
    fibonacci: TechnicalStructureSection
    gann: TechnicalStructureSection
    elliott: TechnicalStructureSection
    chan: TechnicalStructureSection
    candlestick: TechnicalStructureSection
    chart_patterns: TechnicalStructureSection
    divergences: TechnicalStructureSection
    volume_price: TechnicalStructureSection
    multi_timeframe: TechnicalStructureSection
    overlay: TechnicalStructureSection


@dataclass(frozen=True)
class TechnicalStructureSectionStatus:
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


@dataclass(frozen=True)
class TechnicalStructureDataReference:
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
    lineage_request_ids: tuple[str, ...]
    bar_count: int


@dataclass(frozen=True)
class TechnicalStructureViewResult:
    schema_version: str
    data_ref: TechnicalStructureDataReference
    snapshot: AnalysisSnapshot
    engine: str
    engine_note: str | None
    sections: TechnicalStructureSections
    section_status: TechnicalStructureSectionStatus
    warnings: tuple[str, ...]


class TechnicalStructureBuilder(Protocol):
    """Adapter boundary for the still-legacy structure algorithms."""

    def __call__(
        self,
        *,
        data: DataEnvelope,
        analysis: Mapping[str, Any],
        snapshot: AnalysisSnapshot,
        state_key: str,
        prior_state: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]: ...


class TechnicalStructureMapper:
    """Map the legacy technical-page payload into a bounded typed view."""

    _FORBIDDEN_DECISION_KEYS = frozenset(
        {
            "summary",
            "tradePlan",
            "trade_plan",
            "action",
            "decision",
            "recommendation",
            "score",
            "overallScore",
            "overall_score",
            "evidenceScore",
            "evidence_score",
            "componentScores",
            "component_scores",
            "probability",
            "comprehensiveProbability",
            "comprehensive_probability",
        }
    )
    _UNKNOWN_SOURCES = frozenset(
        {"", "unknown", "none", "n/a", "na", "unavailable", "demo", "fallback"}
    )

    def map(
        self,
        research: ResearchResult,
        payload: Mapping[str, Any],
    ) -> TechnicalStructureViewResult:
        if not isinstance(payload, Mapping):
            raise TechnicalStructureInputError("technical builder must return a mapping")

        engine = self._required_text(payload.get("engine"), "engine")
        if "fallback" in engine.casefold():
            raise TechnicalStructureInputError(
                "fallback structure engine cannot create a formal structure view"
            )
        for key in self._FORBIDDEN_DECISION_KEYS:
            if key in payload and not self._empty(payload.get(key)):
                raise TechnicalStructureInputError(
                    f"technical builder returned forbidden decision field: {key}"
                )

        support_resistance = self._map_support_resistance(payload.get("sr"), engine)
        fibonacci = self._map_fibonacci(payload.get("fibonacci"), engine)
        gann = self._map_gann(payload.get("gann"), engine)
        elliott = self._map_elliott(
            payload.get("elliott"), engine, research.snapshot
        )
        chan = self._map_chan(payload.get("chan"), engine)
        candlestick = self._map_candlestick(payload.get("candlestick"), engine)
        chart_patterns = self._map_chart_patterns(payload.get("chartPatterns"), engine)
        divergences = self._map_divergences(payload.get("divergences"), engine)
        volume_price = self._map_volume_price(payload.get("volumePrice"), engine)
        multi_timeframe = self._map_multi_timeframe(payload.get("multiTf"), engine)
        overlay = self._map_overlay(
            payload.get("overlay"),
            engine,
            fibonacci=fibonacci,
            gann=gann,
            elliott=elliott,
            chan=chan,
        )

        sections = TechnicalStructureSections(
            support_resistance=support_resistance,
            fibonacci=fibonacci,
            gann=gann,
            elliott=elliott,
            chan=chan,
            candlestick=candlestick,
            chart_patterns=chart_patterns,
            divergences=divergences,
            volume_price=volume_price,
            multi_timeframe=multi_timeframe,
            overlay=overlay,
        )
        section_status = TechnicalStructureSectionStatus(
            support_resistance=support_resistance.status,
            fibonacci=fibonacci.status,
            gann=gann.status,
            elliott=elliott.status,
            chan=chan.status,
            candlestick=candlestick.status,
            chart_patterns=chart_patterns.status,
            divergences=divergences.status,
            volume_price=volume_price.status,
            multi_timeframe=multi_timeframe.status,
            overlay=overlay.status,
        )
        data = research.data
        data_ref = TechnicalStructureDataReference(
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
            lineage_request_ids=tuple(
                lineage.request_id for lineage in research.snapshot.data_lineage
            ),
            bar_count=len(data.bars),
        )
        section_warnings = [
            warning
            for section in (
                support_resistance,
                fibonacci,
                gann,
                elliott,
                chan,
                candlestick,
                chart_patterns,
                divergences,
                volume_price,
                multi_timeframe,
                overlay,
            )
            for warning in section.warnings
        ]
        warnings = tuple(
            dict.fromkeys(
                [
                    *data.warnings,
                    *research.snapshot.warnings,
                    *section_warnings,
                    "legacy scores, probabilities, actions and trade plans are excluded",
                    *(
                        [
                            "legacy interpretations are excluded because they may contain directional scores or action hints"
                        ]
                        if not self._empty(payload.get("interpretations"))
                        else []
                    ),
                ]
            )
        )
        return TechnicalStructureViewResult(
            schema_version=TECHNICAL_STRUCTURE_SCHEMA_VERSION,
            data_ref=data_ref,
            snapshot=research.snapshot,
            engine=engine,
            engine_note=self._text(payload.get("engineNote"), "engineNote", []),
            sections=sections,
            section_status=section_status,
            warnings=warnings,
        )

    def _map_support_resistance(
        self, value: Any, engine: str
    ) -> TechnicalStructureSection:
        name = "support_resistance"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        items: list[TechnicalStructureItem] = []
        for role, key in (("support", "supports"), ("resistance", "resistances")):
            for index, item in enumerate(self._sequence(raw.get(key), f"{name}.{key}", warnings)):
                if not isinstance(item, Mapping):
                    warnings.append(f"ignored malformed {name}.{key}[{index}]")
                    continue
                price = self._positive_number(
                    item.get("price"), f"{name}.{key}[{index}].price", warnings
                )
                if price is None:
                    continue
                sources = self._text_tuple(
                    item.get("sources"), f"{name}.{key}[{index}].sources", warnings
                )
                source = "|".join(sources) or self._text(
                    item.get("source"), f"{name}.{key}[{index}].source", warnings
                ) or engine
                method = self._text(
                    self._coalesce(item.get("methodVersion"), item.get("method")),
                    f"{name}.{key}[{index}].methodVersion",
                    warnings,
                )
                if method is None:
                    method = "support-resistance-unversioned"
                    warnings.append(f"{name}.{key}[{index}] has no method version")
                items.append(
                    self._item(
                        kind=role,
                        source=source,
                        method_version=method,
                        raw=item,
                        warnings=warnings,
                        path=f"{name}.{key}[{index}]",
                        force_provisional=True,
                        role=role,
                        price=price,
                        description=self._text(
                            item.get("basis"), f"{name}.{key}[{index}].basis", warnings
                        ),
                        count=self._integer(
                            item.get("confluence"),
                            f"{name}.{key}[{index}].confluence",
                            warnings,
                            minimum=0,
                        ),
                        value=self._number(
                            item.get("distancePct"),
                            f"{name}.{key}[{index}].distancePct",
                            warnings,
                        ),
                        tags=sources,
                    )
                )
        return self._section(name, items, warnings, source="mixed", method="mixed")

    def _map_fibonacci(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "fibonacci"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="fibonacci-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        high = self._positive_number(raw.get("high"), f"{name}.high", warnings)
        low = self._positive_number(raw.get("low"), f"{name}.low", warnings)
        current_pct = self._number(
            raw.get("currentPct"), f"{name}.currentPct", warnings
        )
        if high is not None and low is not None and high >= low:
            items.append(
                self._item(
                    kind="range",
                    source=source,
                    method_version=method,
                    raw=raw,
                    warnings=warnings,
                    path=name,
                    force_provisional=True,
                    high=high,
                    low=low,
                    value=current_pct,
                )
            )
        for index, item in enumerate(
            self._sequence(raw.get("levels"), f"{name}.levels", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.levels[{index}]")
                continue
            price = self._positive_number(
                item.get("price"), f"{name}.levels[{index}].price", warnings
            )
            if price is None:
                continue
            items.append(
                self._item(
                    kind="level",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=f"{name}.levels[{index}]",
                    force_provisional=True,
                    label=self._text(
                        item.get("label"), f"{name}.levels[{index}].label", warnings
                    ),
                    role=self._text(
                        item.get("role"), f"{name}.levels[{index}].role", warnings
                    ),
                    price=price,
                    ratio=self._ratio_text(
                        item.get("ratio"), f"{name}.levels[{index}].ratio", warnings
                    ),
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_gann(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "gann"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="gann-unversioned"
        )
        if "fallback" in source.casefold() or "fallback" in method.casefold():
            warnings.append(
                "Gann fallback excluded: its formula is not equivalent to modules/gann"
            )
            return self._section(name, (), warnings, source=source, method=method)
        items: list[TechnicalStructureItem] = []
        range_raw = raw.get("range")
        if isinstance(range_raw, Mapping):
            high = self._positive_number(
                range_raw.get("high"), f"{name}.range.high", warnings
            )
            low = self._positive_number(
                range_raw.get("low"), f"{name}.range.low", warnings
            )
            if high is not None and low is not None and high >= low:
                items.append(
                    self._item(
                        kind="range",
                        source=source,
                        method_version=method,
                        raw=range_raw,
                        warnings=warnings,
                        path=f"{name}.range",
                        force_provisional=True,
                        high=high,
                        low=low,
                        count=self._integer(
                            range_raw.get("lookback"),
                            f"{name}.range.lookback",
                            warnings,
                            minimum=0,
                        ),
                    )
                )
        pivot = raw.get("pivot")
        if isinstance(pivot, Mapping):
            price = self._positive_number(
                pivot.get("price"), f"{name}.pivot.price", warnings
            )
            if price is not None:
                items.append(
                    self._item(
                        kind="pivot",
                        source=source,
                        method_version=method,
                        raw=pivot,
                        warnings=warnings,
                        path=f"{name}.pivot",
                        force_provisional=True,
                        time=self._text(
                            pivot.get("date"), f"{name}.pivot.date", warnings
                        ),
                        price=price,
                        direction=self._text(
                            pivot.get("direction"),
                            f"{name}.pivot.direction",
                            warnings,
                        ),
                    )
                )
        for key, kind in (("octaves", "octave"), ("fanLevels", "fan_level")):
            for index, item in enumerate(
                self._sequence(raw.get(key), f"{name}.{key}", warnings)
            ):
                if not isinstance(item, Mapping):
                    warnings.append(f"ignored malformed {name}.{key}[{index}]")
                    continue
                price = self._positive_number(
                    item.get("price"), f"{name}.{key}[{index}].price", warnings
                )
                if price is None:
                    continue
                items.append(
                    self._item(
                        kind=kind,
                        source=source,
                        method_version=method,
                        raw=item,
                        warnings=warnings,
                        path=f"{name}.{key}[{index}]",
                        force_provisional=True,
                        label=self._text(
                            item.get("label"), f"{name}.{key}[{index}].label", warnings
                        ),
                        role=self._text(
                            item.get("type"), f"{name}.{key}[{index}].type", warnings
                        ),
                        price=price,
                        ratio=self._ratio_text(
                            item.get("ratio"), f"{name}.{key}[{index}].ratio", warnings
                        ),
                        tags=("strong",) if self._optional_bool(item.get("strong")) else (),
                    )
                )
        for index, item in enumerate(
            self._sequence(raw.get("timeCycles"), f"{name}.timeCycles", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.timeCycles[{index}]")
                continue
            cycle = self._integer(
                item.get("cycle"), f"{name}.timeCycles[{index}].cycle", warnings, minimum=1
            )
            if cycle is None:
                continue
            items.append(
                self._item(
                    kind="time_cycle",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=f"{name}.timeCycles[{index}]",
                    force_provisional=True,
                    time=self._text(
                        item.get("date"), f"{name}.timeCycles[{index}].date", warnings
                    ),
                    count=cycle,
                    value=self._number(
                        item.get("futureBars"),
                        f"{name}.timeCycles[{index}].futureBars",
                        warnings,
                    ),
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_elliott(
        self, value: Any, engine: str, snapshot: AnalysisSnapshot
    ) -> TechnicalStructureSection:
        name = "elliott"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="elliott-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        current = raw.get("currentWave")
        if isinstance(current, Mapping):
            # The legacy mapper does not preserve the source confirmation
            # date/index for the current count.  A confirmed pivot is not the
            # same thing as a confirmed current wave count.
            if self._optional_bool(current.get("confirmed")) is True:
                warnings.append(
                    "Elliott current count forced provisional: confirmation anchor is absent"
                )
            items.append(
                self._item(
                    kind=self._text(current.get("type"), f"{name}.currentWave.type", warnings)
                    or "current_wave",
                    source=source,
                    method_version=method,
                    raw=current,
                    warnings=warnings,
                    path=f"{name}.currentWave",
                    force_provisional=True,
                    label=self._text(
                        current.get("phase"), f"{name}.currentWave.phase", warnings
                    ),
                    direction=self._text(
                        current.get("direction"),
                        f"{name}.currentWave.direction",
                        warnings,
                    ),
                    confidence=self._bounded_number(
                        current.get("confidence"),
                        f"{name}.currentWave.confidence",
                        warnings,
                        minimum=0,
                        maximum=100,
                    ),
                    count=self._integer(
                        current.get("waveNumber"),
                        f"{name}.currentWave.waveNumber",
                        warnings,
                        minimum=0,
                    ),
                    description=self._text(
                        current.get("description"),
                        f"{name}.currentWave.description",
                        warnings,
                    ),
                    tags=self._text_tuple(
                        current.get("violations"),
                        f"{name}.currentWave.violations",
                        warnings,
                    ),
                )
            )
        for index, target in enumerate(
            self._sequence(raw.get("targets"), f"{name}.targets", warnings)
        ):
            if not isinstance(target, Mapping):
                warnings.append(f"ignored malformed {name}.targets[{index}]")
                continue
            price = self._positive_number(
                target.get("price"), f"{name}.targets[{index}].price", warnings
            )
            if price is None:
                continue
            items.append(
                self._item(
                    kind="target",
                    source=source,
                    method_version=method,
                    raw=target,
                    warnings=warnings,
                    path=f"{name}.targets[{index}]",
                    force_provisional=True,
                    label=self._text(
                        target.get("label"), f"{name}.targets[{index}].label", warnings
                    ),
                    direction=self._text(
                        target.get("direction"),
                        f"{name}.targets[{index}].direction",
                        warnings,
                    ),
                    price=price,
                    ratio=self._ratio_text(
                        target.get("ratio"), f"{name}.targets[{index}].ratio", warnings
                    ),
                )
            )

        reliability = raw.get("reliability")
        if isinstance(reliability, Mapping):
            reliability_version = self._text(
                reliability.get("version"), f"{name}.reliability.version", warnings
            )
            reliability_source = self._text(
                reliability.get("source"), f"{name}.reliability.source", warnings
            ) or "apps.web.server.elliott_local"
            reliability_method = self._text(
                reliability.get("methodVersion"),
                f"{name}.reliability.methodVersion",
                warnings,
            ) or reliability_version or "elliott-reliability-unversioned"
            admitted = reliability_version == "elliott-reliability-1"
            if not admitted:
                warnings.append("Elliott reliability stream version is not admitted")
            for index, item in enumerate(
                self._sequence(
                    reliability.get("confirmedStructures"),
                    f"{name}.reliability.confirmedStructures",
                    warnings,
                )
            ):
                if not isinstance(item, Mapping):
                    warnings.append(
                        f"ignored malformed {name}.reliability.confirmedStructures[{index}]"
                    )
                    continue
                path = f"{name}.reliability.confirmedStructures[{index}]"
                items.append(
                    self._item(
                        kind=self._text(item.get("kind"), f"{path}.kind", warnings)
                        or "reliability_structure",
                        source=reliability_source,
                        method_version=reliability_method,
                        raw=item,
                        warnings=warnings,
                        path=path,
                        force_provisional=not admitted,
                        admit_validation=admitted,
                        label=self._text(item.get("pattern"), f"{path}.pattern", warnings),
                        role=self._text(item.get("pattern"), f"{path}.pattern", warnings),
                        direction=self._text(
                            item.get("direction"), f"{path}.direction", warnings
                        ),
                        time=self._text(item.get("startTime"), f"{path}.startTime", warnings),
                        end_time=self._text(
                            item.get("endTime"), f"{path}.endTime", warnings
                        ),
                        confirmed_time=self._text(
                            item.get("confirmedTime"), f"{path}.confirmedTime", warnings
                        ),
                        price=self._positive_number(
                            item.get("startPrice"), f"{path}.startPrice", warnings
                        ),
                        end_price=self._positive_number(
                            item.get("endPrice"), f"{path}.endPrice", warnings
                        ),
                        value=self._number(item.get("fibScore"), f"{path}.fibScore", warnings),
                        count=self._integer(
                            len(item.get("points", ())) if isinstance(item.get("points"), list) else None,
                            f"{path}.pointCount",
                            warnings,
                            minimum=0,
                        ),
                        basis=self._text(
                            item.get("validationBasis"), f"{path}.validationBasis", warnings
                        ),
                        structure_id=self._text(
                            item.get("structureId"), f"{path}.structureId", warnings
                        ),
                        parent_structure_id=self._text(
                            item.get("parentStructureId"),
                            f"{path}.parentStructureId",
                            warnings,
                        ),
                        level=self._integer(
                            item.get("level"), f"{path}.level", warnings, minimum=0
                        ),
                        validation_basis=self._text(
                            item.get("validationBasis"), f"{path}.validationBasis", warnings
                        ),
                        points=self._points(item.get("points"), path, warnings),
                        tags=self._text_tuple(
                            item.get("violations"), f"{path}.violations", warnings
                        ),
                    )
                )

        multi_tf = raw.get("multiTf")
        if isinstance(multi_tf, Mapping) and multi_tf:
            lineage_ids = self._lineage_ids(multi_tf)
            allowed = {item.request_id for item in snapshot.data_lineage}
            if not lineage_ids or not set(lineage_ids).issubset(allowed):
                warnings.append(
                    "excluded Elliott multi-timeframe synthesis without bound data lineage"
                )
            else:
                reasoning = self._text_tuple(
                    multi_tf.get("reasoning"), f"{name}.multiTf.reasoning", warnings
                )
                items.append(
                    self._item(
                        kind="multi_timeframe_synthesis",
                        source=source,
                        method_version=method,
                        raw=multi_tf,
                        warnings=warnings,
                        path=f"{name}.multiTf",
                        force_provisional=True,
                        label=self._text(
                            multi_tf.get("alignment"),
                            f"{name}.multiTf.alignment",
                            warnings,
                        ),
                        direction=self._text(
                            multi_tf.get("bias"), f"{name}.multiTf.bias", warnings
                        ),
                        confidence=self._bounded_number(
                            multi_tf.get("confidence"),
                            f"{name}.multiTf.confidence",
                            warnings,
                            minimum=0,
                            maximum=100,
                        ),
                        description="; ".join(reasoning) or None,
                        tags=lineage_ids,
                    )
                )
        return self._section(
            name,
            items,
            warnings,
            source=source,
            method=method,
            summary=self._text(raw.get("description"), f"{name}.description", warnings),
        )

    def _map_chan(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "chan"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        method_hint = self._text(raw.get("methodVersion"), f"{name}.methodVersion", warnings)
        inferred_source = "local" if method_hint and "local" in method_hint.casefold() else engine
        source, method = self._metadata(
            raw,
            inferred_source,
            name,
            warnings,
            default_method="chan-unversioned",
        )
        items: list[TechnicalStructureItem] = []

        def append_item(kind: str, item: Any, index: int | None = None) -> None:
            if not isinstance(item, Mapping):
                if item is not None:
                    suffix = f"[{index}]" if index is not None else ""
                    warnings.append(f"ignored malformed {name}.{kind}{suffix}")
                return
            path = f"{name}.{kind}" + (f"[{index}]" if index is not None else "")
            force_provisional = kind in {
                "last_bi",
                "active_bi",
                "bi",
                "zhongshu",
                "signal",
            }
            validation_admitted = (
                raw.get("reliabilityVersion") == "chan-reliability-1"
                and kind in {"stable_bi", "segment", "same_level"}
            )
            items.append(
                self._item(
                    kind=kind,
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    force_provisional=force_provisional,
                    admit_validation=validation_admitted,
                    label=self._text(
                        self._coalesce(item.get("label"), item.get("type")),
                        f"{path}.label",
                        warnings,
                    ),
                    role=self._text(item.get("type"), f"{path}.type", warnings),
                    direction=self._text(
                        item.get("direction"), f"{path}.direction", warnings
                    ),
                    time=self._text(
                        self._coalesce(item.get("startTime"), item.get("time")),
                        f"{path}.startTime",
                        warnings,
                    ),
                    end_time=self._text(
                        item.get("endTime"), f"{path}.endTime", warnings
                    ),
                    confirmed_time=self._text(
                        item.get("confirmedTime"), f"{path}.confirmedTime", warnings
                    ),
                    price=self._positive_number(
                        self._coalesce(item.get("startPrice"), item.get("price")),
                        f"{path}.startPrice",
                        warnings,
                    ),
                    end_price=self._positive_number(
                        item.get("endPrice"), f"{path}.endPrice", warnings
                    ),
                    high=self._positive_number(
                        self._coalesce(item.get("high"), item.get("zg")),
                        f"{path}.high",
                        warnings,
                    ),
                    low=self._positive_number(
                        self._coalesce(item.get("low"), item.get("zd")),
                        f"{path}.low",
                        warnings,
                    ),
                    value=self._number(
                        item.get("macdArea"), f"{path}.macdArea", warnings
                    ),
                    count=self._integer(
                        self._coalesce(
                            item.get("strokeCount"), item.get("evidenceSegmentCount")
                        ),
                        f"{path}.count",
                        warnings,
                        minimum=0,
                    ),
                    description=self._text(
                        item.get("description"), f"{path}.description", warnings
                    ),
                    basis=self._text(item.get("basis"), f"{path}.basis", warnings),
                    structure_id=self._text(
                        item.get("structureId"), f"{path}.structureId", warnings
                    ),
                    parent_structure_id=self._text(
                        item.get("parentStructureId"),
                        f"{path}.parentStructureId",
                        warnings,
                    ),
                    level=self._integer(
                        item.get("level"), f"{path}.level", warnings, minimum=0
                    ),
                    validation_basis=self._text(
                        item.get("validationBasis"),
                        f"{path}.validationBasis",
                        warnings,
                    ),
                    points=self._points(item.get("points"), path, warnings),
                    tags=self._text_tuple(
                        item.get("labels"), f"{path}.labels", warnings
                    ),
                )
            )

        append_item("last_bi", raw.get("lastBi"))
        append_item("active_bi", raw.get("activeBi"))
        for key, kind in (
            ("fenxing", "fenxing"),
            ("bi", "bi"),
            ("stableBi", "stable_bi"),
            ("segment", "segment"),
            ("sameLevel", "same_level"),
            ("zhongshu", "zhongshu"),
            ("signals", "signal"),
        ):
            for index, item in enumerate(
                self._sequence(raw.get(key), f"{name}.{key}", warnings)
            ):
                append_item(kind, item, index)
        append_item("trend_structure", raw.get("trendStructure"))
        return self._section(
            name,
            items,
            warnings,
            source=source,
            method=method,
            summary=self._text(raw.get("description"), f"{name}.description", warnings)
            or self._text(raw.get("trend"), f"{name}.trend", warnings),
        )

    def _map_candlestick(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "candlestick"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="candlestick-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        for index, item in enumerate(
            self._sequence(raw.get("patterns"), f"{name}.patterns", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.patterns[{index}]")
                continue
            path = f"{name}.patterns[{index}]"
            items.append(
                self._item(
                    kind="candlestick_pattern",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    label=self._text(item.get("nameCn"), f"{path}.nameCn", warnings),
                    role=self._text(item.get("type"), f"{path}.type", warnings),
                    direction=self._text(
                        item.get("direction"), f"{path}.direction", warnings
                    ),
                    time=self._text(item.get("time"), f"{path}.time", warnings),
                    description=self._text(
                        item.get("description"), f"{path}.description", warnings
                    ),
                    tags=tuple(
                        tag
                        for tag in (
                            self._text(
                                item.get("reliability"), f"{path}.reliability", warnings
                            ),
                        )
                        if tag
                    ),
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_chart_patterns(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "chart_patterns"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="chart-pattern-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        for index, item in enumerate(
            self._sequence(raw.get("patterns"), f"{name}.patterns", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.patterns[{index}]")
                continue
            path = f"{name}.patterns[{index}]"
            items.append(
                self._item(
                    kind="chart_pattern",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    label=self._text(item.get("nameCn"), f"{path}.nameCn", warnings),
                    role=self._text(item.get("type"), f"{path}.type", warnings),
                    direction=self._text(
                        item.get("direction"), f"{path}.direction", warnings
                    ),
                    confidence=self._bounded_number(
                        item.get("confidence"),
                        f"{path}.confidence",
                        warnings,
                        minimum=0,
                        maximum=100,
                    ),
                    neckline_price=self._positive_number(
                        item.get("neckline"), f"{path}.neckline", warnings
                    ),
                    target_price=self._positive_number(
                        item.get("target"), f"{path}.target", warnings
                    ),
                    stop_price=self._positive_number(
                        item.get("stop"), f"{path}.stop", warnings
                    ),
                    description=self._text(
                        item.get("description"), f"{path}.description", warnings
                    ),
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_divergences(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "divergences"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="divergence-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        for index, item in enumerate(
            self._sequence(raw.get("list"), f"{name}.list", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.list[{index}]")
                continue
            path = f"{name}.list[{index}]"
            indicator = self._text(
                item.get("indicator"), f"{path}.indicator", warnings
            )
            items.append(
                self._item(
                    kind="divergence",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    label=self._text(item.get("type"), f"{path}.type", warnings),
                    time=self._text(
                        item.get("startDate"), f"{path}.startDate", warnings
                    ),
                    end_time=self._text(
                        item.get("endDate"), f"{path}.endDate", warnings
                    ),
                    count=self._integer(
                        item.get("spanBars"), f"{path}.spanBars", warnings, minimum=0
                    ),
                    description=self._text(
                        item.get("description"), f"{path}.description", warnings
                    ),
                    tags=tuple(
                        tag
                        for tag in (
                            indicator,
                            self._text(
                                item.get("reliability"), f"{path}.reliability", warnings
                            ),
                        )
                        if tag
                    ),
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_volume_price(self, value: Any, engine: str) -> TechnicalStructureSection:
        name = "volume_price"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="volume-price-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        for key, kind in (
            ("divergence", "volume_price_divergence"),
            ("turnover", "turnover"),
            ("volumeRatio", "volume_ratio"),
            ("accumulation", "accumulation_distribution"),
            ("wyckoff", "wyckoff_phase"),
        ):
            item = raw.get(key)
            if not isinstance(item, Mapping):
                continue
            path = f"{name}.{key}"
            detected = self._optional_bool(item.get("detected"))
            tags = tuple(
                tag
                for tag in (
                    "detected" if detected is True else None,
                    self._text(item.get("level"), f"{path}.level", warnings),
                    self._text(item.get("trend"), f"{path}.trend", warnings),
                )
                if tag
            )
            items.append(
                self._item(
                    kind=kind,
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    label=self._text(
                        self._coalesce(item.get("type"), item.get("phase")),
                        f"{path}.label",
                        warnings,
                    ),
                    direction=self._text(
                        item.get("trend"), f"{path}.direction", warnings
                    ),
                    value=self._number(
                        item.get("current") if key == "turnover" else item.get("value"),
                        f"{path}.value",
                        warnings,
                    ),
                    reference_value=self._number(
                        item.get("avg20"), f"{path}.avg20", warnings
                    ),
                    confidence=self._bounded_number(
                        item.get("confidence"),
                        f"{path}.confidence",
                        warnings,
                        minimum=0,
                        maximum=100,
                    ),
                    description=self._text(
                        self._coalesce(
                            item.get("description"), item.get("interpretation")
                        ),
                        f"{path}.description",
                        warnings,
                    ),
                    tags=tags,
                )
            )
        return self._section(name, items, warnings, source=source, method=method)

    def _map_multi_timeframe(
        self, value: Any, engine: str
    ) -> TechnicalStructureSection:
        name = "multi_timeframe"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        source, method = self._metadata(
            raw, engine, name, warnings, default_method="multi-timeframe-unversioned"
        )
        items: list[TechnicalStructureItem] = []
        for index, item in enumerate(
            self._sequence(raw.get("timeframes"), f"{name}.timeframes", warnings)
        ):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {name}.timeframes[{index}]")
                continue
            path = f"{name}.timeframes[{index}]"
            items.append(
                self._item(
                    kind="timeframe",
                    source=source,
                    method_version=method,
                    raw=item,
                    warnings=warnings,
                    path=path,
                    label=self._text(item.get("tf"), f"{path}.tf", warnings),
                    direction=self._text(
                        item.get("trend"), f"{path}.trend", warnings
                    ),
                    description=self._text(
                        item.get("details"), f"{path}.details", warnings
                    ),
                    tags=tuple(
                        tag
                        for tag in (
                            self._text(
                                item.get("emaAlignment"),
                                f"{path}.emaAlignment",
                                warnings,
                            ),
                            self._text(
                                item.get("macdDirection"),
                                f"{path}.macdDirection",
                                warnings,
                            ),
                            self._text(
                                item.get("rsiZone"), f"{path}.rsiZone", warnings
                            ),
                        )
                        if tag
                    ),
                )
            )
        synthesis = self._text(
            raw.get("summary"), f"{name}.summary", warnings
        )
        if any(raw.get(key) is not None for key in ("alignment", "dominantTrend", "summary")):
            items.append(
                self._item(
                    kind="synthesis",
                    source=source,
                    method_version=method,
                    raw=raw,
                    warnings=warnings,
                    path=name,
                    label=self._text(
                        raw.get("alignment"), f"{name}.alignment", warnings
                    ),
                    direction=self._text(
                        raw.get("dominantTrend"), f"{name}.dominantTrend", warnings
                    ),
                    description=synthesis,
                )
            )
        return self._section(
            name, items, warnings, source=source, method=method, summary=synthesis
        )

    def _map_overlay(
        self,
        value: Any,
        engine: str,
        *,
        fibonacci: TechnicalStructureSection,
        gann: TechnicalStructureSection,
        elliott: TechnicalStructureSection,
        chan: TechnicalStructureSection,
    ) -> TechnicalStructureSection:
        name = "overlay"
        warnings: list[str] = []
        raw = self._section_mapping(value, name, warnings)
        if raw is None:
            return self._section(name, (), warnings)
        items: list[TechnicalStructureItem] = []

        def metadata(
            item: Mapping[str, Any], parent: TechnicalStructureSection, kind: str
        ) -> tuple[str, str]:
            source = self._text(item.get("source"), f"{name}.{kind}.source", warnings)
            method = self._text(
                item.get("methodVersion"), f"{name}.{kind}.methodVersion", warnings
            )
            source = source or parent.source or engine
            method = method or parent.method_version
            if method is None:
                method = f"overlay-{kind}-unversioned"
                warnings.append(f"overlay {kind} has no method version")
            return source, method

        for key, kind, parent, force_provisional in (
            ("fib", "fibonacci_level", fibonacci, True),
            ("gannOctaves", "gann_octave", gann, True),
            ("gannFan", "gann_fan", gann, True),
            ("elliottSegments", "elliott_segment", elliott, True),
            ("elliottLabels", "elliott_label", elliott, True),
            ("elliottTargets", "elliott_target", elliott, True),
            ("chanBi", "chan_bi", chan, True),
            ("chanZhongshu", "chan_zhongshu", chan, True),
            ("chanFenxing", "chan_fenxing", chan, False),
            ("chanSignals", "chan_signal", chan, True),
        ):
            if parent.status is SectionAvailability.UNAVAILABLE and key.startswith("gann"):
                if raw.get(key):
                    warnings.append(f"excluded {key} because Gann section is unavailable")
                continue
            for index, item in enumerate(
                self._sequence(raw.get(key), f"{name}.{key}", warnings)
            ):
                if not isinstance(item, Mapping):
                    warnings.append(f"ignored malformed {name}.{key}[{index}]")
                    continue
                path = f"{name}.{key}[{index}]"
                source, method = metadata(item, parent, kind)
                points = self._points(item.get("points"), path, warnings)
                price = self._positive_number(
                    item.get("price"), f"{path}.price", warnings
                )
                if price is None and len(points) == 1:
                    price = points[0].value
                items.append(
                    self._item(
                        kind=kind,
                        source=source,
                        method_version=method,
                        raw=item,
                        warnings=warnings,
                        path=path,
                        force_provisional=force_provisional,
                        label=self._text(
                            item.get("label"), f"{path}.label", warnings
                        ),
                        role=self._text(
                            self._coalesce(item.get("kind"), item.get("type")),
                            f"{path}.role",
                            warnings,
                        ),
                        direction=self._text(
                            self._coalesce(item.get("direction"), item.get("dir")),
                            f"{path}.direction",
                            warnings,
                        ),
                        time=self._text(
                            self._coalesce(item.get("time"), item.get("startTime")),
                            f"{path}.time",
                            warnings,
                        ),
                        end_time=self._text(
                            item.get("endTime"), f"{path}.endTime", warnings
                        ),
                        confirmed_time=self._text(
                            item.get("confirmedTime"),
                            f"{path}.confirmedTime",
                            warnings,
                        ),
                        price=price
                        or self._positive_number(
                            item.get("startPrice"), f"{path}.startPrice", warnings
                        ),
                        end_price=self._positive_number(
                            item.get("endPrice"), f"{path}.endPrice", warnings
                        ),
                        high=self._positive_number(
                            item.get("zg"), f"{path}.zg", warnings
                        ),
                        low=self._positive_number(
                            item.get("zd"), f"{path}.zd", warnings
                        ),
                        ratio=self._ratio_text(
                            item.get("ratio"), f"{path}.ratio", warnings
                        ),
                        points=points,
                    )
                )
        active = raw.get("chanActiveBi")
        if isinstance(active, Mapping):
            path = f"{name}.chanActiveBi"
            source, method = metadata(active, chan, "chan_active_bi")
            items.append(
                self._item(
                    kind="chan_active_bi",
                    source=source,
                    method_version=method,
                    raw=active,
                    warnings=warnings,
                    path=path,
                    force_provisional=True,
                    direction=self._text(
                        active.get("direction"), f"{path}.direction", warnings
                    ),
                    points=self._points(active.get("points"), path, warnings),
                )
            )
        return self._section(name, items, warnings, source="mixed", method="mixed")

    @classmethod
    def _metadata(
        cls,
        raw: Mapping[str, Any],
        default_source: str,
        name: str,
        warnings: list[str],
        *,
        default_method: str,
    ) -> tuple[str, str]:
        source = cls._text(raw.get("source"), f"{name}.source", warnings)
        method = cls._text(
            cls._coalesce(raw.get("methodVersion"), raw.get("method_version")),
            f"{name}.methodVersion",
            warnings,
        )
        if source is None:
            source = default_source
        if method is None:
            method = default_method
            warnings.append(f"{name} has no explicit method version")
        return source, method

    @classmethod
    def _item(
        cls,
        *,
        kind: str,
        source: str,
        method_version: str,
        raw: Mapping[str, Any],
        warnings: list[str],
        path: str,
        force_provisional: bool = False,
        admit_validation: bool = False,
        **kwargs: Any,
    ) -> TechnicalStructureItem:
        status = cls._status(raw, path, warnings, force_provisional=force_provisional)
        confirmed = status is SignalStatus.CONFIRMED
        requested_validated = cls._optional_bool(raw.get("validated")) is True
        structure_id = kwargs.get("structure_id")
        validation_basis = kwargs.get("validation_basis")
        confirmed_time = kwargs.get("confirmed_time")
        validated = bool(
            admit_validation
            and requested_validated
            and confirmed
            and structure_id
            and validation_basis
            and confirmed_time
        )
        if requested_validated and not validated:
            warnings.append(f"{path} validation claim was not admitted")
        return TechnicalStructureItem(
            kind=kind,
            source=source,
            method_version=method_version,
            status=status,
            confirmed=confirmed,
            provisional=not confirmed,
            validated=validated,
            score_eligible=False,
            alert_eligible=False,
            **kwargs,
        )

    @classmethod
    def _status(
        cls,
        raw: Mapping[str, Any],
        path: str,
        warnings: list[str],
        *,
        force_provisional: bool,
    ) -> SignalStatus:
        raw_status = cls._text(raw.get("status"), f"{path}.status", warnings)
        confirmed = cls._optional_bool(raw.get("confirmed"))
        if force_provisional:
            if confirmed is True or (raw_status and raw_status.casefold() == "confirmed"):
                warnings.append(f"{path} forced provisional by repaint policy")
            return SignalStatus.PROVISIONAL
        if raw_status is not None and raw_status.casefold() not in {
            "confirmed",
            "provisional",
        }:
            warnings.append(f"ignored invalid {path}.status")
            return SignalStatus.PROVISIONAL
        if raw_status and raw_status.casefold() == "confirmed" and confirmed is False:
            warnings.append(f"contradictory confirmation metadata at {path}")
            return SignalStatus.PROVISIONAL
        if raw_status and raw_status.casefold() == "provisional" and confirmed is True:
            warnings.append(f"contradictory confirmation metadata at {path}")
            return SignalStatus.PROVISIONAL
        if confirmed is True or (raw_status and raw_status.casefold() == "confirmed"):
            return SignalStatus.CONFIRMED
        return SignalStatus.PROVISIONAL

    @classmethod
    def _section(
        cls,
        name: str,
        items: Sequence[TechnicalStructureItem],
        warnings: list[str],
        *,
        source: str | None = None,
        method: str | None = None,
        summary: str | None = None,
    ) -> TechnicalStructureSection:
        native_items = tuple(items)
        if not native_items:
            status = SectionAvailability.UNAVAILABLE
            if not warnings:
                warnings.append(f"{name} unavailable: builder provided no section")
        elif warnings:
            status = SectionAvailability.PARTIAL
        else:
            status = SectionAvailability.AVAILABLE
        return TechnicalStructureSection(
            name=name,
            status=status,
            source=source,
            method_version=method,
            summary=summary,
            items=native_items,
            warnings=tuple(dict.fromkeys(warnings)),
        )

    @classmethod
    def _section_mapping(
        cls, value: Any, name: str, warnings: list[str]
    ) -> Mapping[str, Any] | None:
        if value is None:
            warnings.append(f"{name} unavailable: builder provided no section")
            return None
        if not isinstance(value, Mapping):
            warnings.append(f"{name} unavailable: builder returned a malformed section")
            return None
        return value

    @staticmethod
    def _empty(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value
        if isinstance(value, Mapping):
            return not value
        if isinstance(value, (list, tuple)):
            return not value
        return False

    @staticmethod
    def _coalesce(*values: Any) -> Any:
        return next((value for value in values if value is not None), None)

    @classmethod
    def _required_text(cls, value: Any, field: str) -> str:
        warnings: list[str] = []
        result = cls._text(value, field, warnings)
        if result is None:
            raise TechnicalStructureInputError(f"technical builder missing {field}")
        return result

    @staticmethod
    def _text(value: Any, field: str, warnings: list[str]) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            warnings.append(f"ignored non-text {field}")
            return None
        native = value.strip()
        if not native:
            return None
        if len(native) > 1024:
            warnings.append(f"ignored overlong {field}")
            return None
        return native

    @classmethod
    def _ratio_text(cls, value: Any, field: str, warnings: list[str]) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return cls._text(value, field, warnings)
        number = cls._number(value, field, warnings)
        return None if number is None else format(number, "g")

    @classmethod
    def _text_tuple(
        cls, value: Any, field: str, warnings: list[str]
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple)):
            warnings.append(f"ignored non-list {field}")
            return ()
        output: list[str] = []
        for index, item in enumerate(value[:32]):
            text = cls._text(item, f"{field}[{index}]", warnings)
            if text is not None:
                output.append(text)
        return tuple(output)

    @staticmethod
    def _optional_bool(value: Any) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        value_type = type(value)
        if value_type.__module__.startswith("numpy") and value_type.__name__ in {
            "bool",
            "bool_",
        }:
            return bool(value)
        return None

    @staticmethod
    def _number(value: Any, field: str, warnings: list[str]) -> float | None:
        if value is None:
            return None
        if isinstance(value, (bool, str, bytes, Mapping)):
            warnings.append(f"ignored non-numeric {field}")
            return None
        if getattr(value, "ndim", 0) not in (0, None):
            warnings.append(f"ignored non-scalar {field}")
            return None
        try:
            native = float(value)
        except (TypeError, ValueError, OverflowError):
            warnings.append(f"ignored non-numeric {field}")
            return None
        if not math.isfinite(native):
            warnings.append(f"ignored non-finite {field}")
            return None
        return native

    @classmethod
    def _bounded_number(
        cls,
        value: Any,
        field: str,
        warnings: list[str],
        *,
        minimum: float,
        maximum: float,
    ) -> float | None:
        native = cls._number(value, field, warnings)
        if native is None:
            return None
        if native < minimum or native > maximum:
            warnings.append(f"ignored out-of-range {field}")
            return None
        return native

    @classmethod
    def _positive_number(
        cls, value: Any, field: str, warnings: list[str]
    ) -> float | None:
        native = cls._number(value, field, warnings)
        if native is None:
            return None
        if native <= 0:
            warnings.append(f"ignored non-positive {field}")
            return None
        return native

    @classmethod
    def _integer(
        cls,
        value: Any,
        field: str,
        warnings: list[str],
        *,
        minimum: int,
    ) -> int | None:
        native = cls._number(value, field, warnings)
        if native is None:
            return None
        if not native.is_integer() or native < minimum:
            warnings.append(f"ignored invalid integer {field}")
            return None
        return int(native)

    @staticmethod
    def _sequence(value: Any, field: str, warnings: list[str]) -> Sequence[Any]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return value[:256]
        warnings.append(f"ignored non-list {field}")
        return ()

    @classmethod
    def _points(
        cls, value: Any, path: str, warnings: list[str]
    ) -> tuple[StructurePoint, ...]:
        output: list[StructurePoint] = []
        for index, item in enumerate(cls._sequence(value, f"{path}.points", warnings)):
            if not isinstance(item, Mapping):
                warnings.append(f"ignored malformed {path}.points[{index}]")
                continue
            number = cls._number(
                item.get("value", item.get("price")),
                f"{path}.points[{index}].value",
                warnings,
            )
            if number is None:
                continue
            output.append(
                StructurePoint(
                    time=cls._text(
                        item.get("time"), f"{path}.points[{index}].time", warnings
                    ),
                    value=number,
                )
            )
        return tuple(output)

    @classmethod
    def _lineage_ids(cls, raw: Mapping[str, Any]) -> tuple[str, ...]:
        direct = raw.get("lineage_request_ids")
        if isinstance(direct, (list, tuple)):
            return tuple(
                item.strip()
                for item in direct
                if isinstance(item, str) and item.strip()
            )
        lineage = raw.get("lineage")
        if not isinstance(lineage, (list, tuple)):
            return ()
        output = []
        for item in lineage:
            if not isinstance(item, Mapping):
                continue
            request_id = item.get("request_id")
            if isinstance(request_id, str) and request_id.strip():
                output.append(request_id.strip())
        return tuple(output)


class TechnicalStructureService:
    """Build one formal structure view from exactly one research analysis call."""

    _FORMAL_MODES = frozenset({DataMode.LIVE, DataMode.DELAYED, DataMode.CACHE})
    _UNKNOWN_SOURCES = TechnicalStructureMapper._UNKNOWN_SOURCES

    def __init__(
        self,
        research_use_case: ResearchAnalysisUseCase,
        builder: TechnicalStructureBuilder,
        mapper: TechnicalStructureMapper | None = None,
    ) -> None:
        self.research_use_case = research_use_case
        self.builder = builder
        self.mapper = mapper or TechnicalStructureMapper()

    def analyze(
        self,
        query: DataQuery,
        *,
        info: Mapping[str, Any] | None = None,
        benchmark_query: DataQuery | None = None,
        market_regime: MarketRegimeContext | None = None,
        sector_context: SectorOpportunity | None = None,
        pair_context: PairCandidate | None = None,
        bar_status: SignalStatus = SignalStatus.CONFIRMED,
        has_position: bool = False,
        prior_state: Mapping[str, Any] | None = None,
    ) -> TechnicalStructureViewResult:
        if query.purpose is DataPurpose.DEMO:
            raise TechnicalStructureInputError(
                "demo purpose cannot create a formal structure view"
            )
        research = self.research_use_case.analyze(
            query,
            info=info,
            benchmark_query=benchmark_query,
            market_regime=market_regime,
            sector_context=sector_context,
            pair_context=pair_context,
            bar_status=bar_status,
            has_position=has_position,
        )
        self._validate_formal_result(query, research)
        try:
            payload = self.builder(
                data=research.data,
                analysis=research.analysis,
                snapshot=research.snapshot,
                state_key=technical_structure_state_key(
                    query.instrument, query.timeframe
                ),
                prior_state=prior_state,
            )
        except Exception as exc:  # noqa: BLE001 - isolate the injected adapter boundary
            raise TechnicalStructureBuildError(
                f"technical structure builder failed: {exc}"
            ) from exc
        return self.mapper.map(research, payload)

    @classmethod
    def _validate_formal_result(
        cls, query: DataQuery, research: ResearchResult
    ) -> None:
        data = research.data
        snapshot = research.snapshot
        if query.purpose is DataPurpose.DEMO or data.purpose is DataPurpose.DEMO:
            raise TechnicalStructureInputError(
                "demo purpose cannot create a formal structure view"
            )
        if data.is_synthetic or data.mode is DataMode.DEMO:
            raise TechnicalStructureInputError(
                "synthetic/demo data cannot create a formal structure view"
            )
        if data.mode not in cls._FORMAL_MODES:
            raise TechnicalStructureInputError(
                f"data mode is not admitted for formal structures: {data.mode.value}"
            )
        if data.quality is not QualityStatus.VALID:
            raise TechnicalStructureInputError(
                f"data quality must be valid, got {data.quality.value}"
            )
        if not data.bars:
            raise TechnicalStructureInputError(
                "at least one bar is required for technical structures"
            )
        if not math.isfinite(float(data.freshness_seconds)):
            raise TechnicalStructureInputError("data freshness must be finite")
        if (
            data.instrument != query.instrument
            or data.timeframe is not query.timeframe
            or data.purpose is not query.purpose
            or data.adjustment is not query.adjustment
            or data.as_of != query.as_of
        ):
            raise TechnicalStructureInputError(
                "research data does not match the requested instrument/timeframe/purpose/as_of"
            )
        provider = data.provider.strip().casefold()
        source_chain = [item.strip() for item in data.source_chain if item.strip()]
        if provider in cls._UNKNOWN_SOURCES or not source_chain:
            raise TechnicalStructureInputError(
                "formal structure data must have a known provider and source chain"
            )
        if any(item.casefold() in cls._UNKNOWN_SOURCES for item in source_chain):
            raise TechnicalStructureInputError(
                "formal structure source chain contains an unknown or forbidden source"
            )
        if not isinstance(research.analysis, Mapping):
            raise TechnicalStructureInputError("research analysis must be a mapping")
        if (
            snapshot.instrument != data.instrument
            or snapshot.timeframe is not data.timeframe
            or snapshot.as_of != data.as_of
        ):
            raise TechnicalStructureInputError(
                "analysis snapshot is not bound to the analyzed data"
            )
        if not snapshot.data_lineage or snapshot.data_lineage[0] != data.lineage():
            raise TechnicalStructureInputError(
                "analysis snapshot primary lineage does not match the analyzed data"
            )
