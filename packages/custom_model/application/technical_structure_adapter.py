from __future__ import annotations

from collections.abc import Callable, Mapping
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from custom_model.application.market_view import (
    CORE_INDICATOR_PROFILE,
    MarketBarsViewService,
)
from custom_model.application.research import ResearchAnalysisUseCase, ResearchResult
from custom_model.application.technical_structure_service import (
    ChanContinuity,
    SectionAvailability,
    TechnicalStructureBuildError,
    TechnicalStructureService,
    TechnicalStructureViewResult,
    chan_state_continuity,
    technical_structure_state_key,
    verify_chan_state,
)
from custom_model.domain.models import (
    AnalysisSnapshot,
    DataEnvelope,
    DataQuery,
    SignalStatus,
)


LegacyPayloadBuilder = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class TechnicalStructureEndpointResult:
    """Transport-ready structure result plus the canonical market-data identity."""

    view: TechnicalStructureViewResult
    data_fingerprint: str
    current_state: dict[str, Any]
    continuity: ChanContinuity


class TechnicalStructureEndpointService(Protocol):
    def analyze(
        self,
        query: DataQuery,
        *,
        info: Mapping[str, Any] | None = None,
        benchmark_query: DataQuery | None = None,
        has_position: bool = False,
        prior_state: Mapping[str, Any] | None = None,
    ) -> TechnicalStructureEndpointResult: ...

    def analyze_research(
        self,
        research: ResearchResult,
        *,
        confirmed_through: datetime,
        prior_state: Mapping[str, Any] | None = None,
    ) -> TechnicalStructureEndpointResult: ...


class LegacyTechnicalStructureBuilder:
    """Bounded adapter over the archived Web technical payload builder.

    The legacy function is allowed to *calculate* its compatibility payload,
    but only the explicitly listed structure sections cross into the shared
    Application service.  Decision summaries, scores, probabilities, actions
    and trade plans are recursively removed before mapping.
    """

    _TOP_LEVEL_FIELDS = (
        "engine",
        "engineNote",
        "sr",
        "fibonacci",
        "gann",
        "elliott",
        "chan",
        "candlestick",
        "chartPatterns",
        "divergences",
        "volumePrice",
        "multiTf",
        "overlay",
    )
    _FORBIDDEN_NORMALIZED_KEYS = frozenset(
        {
            "summary",
            "tradeplan",
            "interpretations",
            "action",
            "decision",
            "recommendation",
            "score",
            "probability",
        }
    )
    _ADAPTER_ENGINE = "legacy-v5-structure-adapter/1.0.0"
    _SECTION_METADATA = {
        "fibonacci": ("legacy-v5-technical", "fibonacci-mapped/1.0.0"),
        "gann": ("modules.gann", "gann-mapped/1.0.0"),
        "elliott": ("modules.elliott_wave", "elliott-mapped/1.0.0"),
        "chan": ("apps.web.server.chan", "chan-local-3"),
        "candlestick": (
            "modules.candlestick_patterns",
            "candlestick-mapped/1.0.0",
        ),
        "chartPatterns": ("modules.chart_patterns", "chart-pattern-mapped/1.0.0"),
        "divergences": (
            "modules.divergence_detector",
            "divergence-mapped/1.0.0",
        ),
        "volumePrice": (
            "modules.volume_price_analysis",
            "volume-price-mapped/1.0.0",
        ),
        "multiTf": ("modules.multi_timeframe", "multi-timeframe-mapped/1.0.0"),
    }

    def __init__(self, payload_builder: LegacyPayloadBuilder | None = None) -> None:
        self._payload_builder = payload_builder
        self._fingerprint_context: ContextVar[tuple[str, str] | None] = ContextVar(
            "technical_structure_data_fingerprint", default=None
        )
        self._chan_state_context: ContextVar[
            tuple[str, dict[str, Any]] | None
        ] = ContextVar("technical_structure_chan_state", default=None)

    def __call__(
        self,
        *,
        data: DataEnvelope,
        analysis: Mapping[str, Any],
        snapshot: AnalysisSnapshot,
        state_key: str,
        prior_state: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        # The core validates this binding before invoking us; retaining the
        # explicit check here prevents a future direct adapter call from
        # silently mixing a snapshot and another instrument's bars.
        if (
            not snapshot.data_lineage
            or snapshot.data_lineage[0].request_id != data.request_id
        ):
            raise TechnicalStructureBuildError(
                "technical structure lineage binding is invalid"
            )

        fingerprint = MarketBarsViewService.fingerprint(
            data, CORE_INDICATOR_PROFILE
        )
        self._fingerprint_context.set((data.request_id, fingerprint))

        frame = self._frame_from_envelope(data)
        raw_analysis = analysis if isinstance(analysis, dict) else dict(analysis)
        builder = self._payload_builder or self._load_legacy_builder()
        payload = builder(
            data.instrument.symbol,
            data.instrument.symbol,
            frame,
            raw_analysis,
            state_key=state_key,
            prior_state=prior_state,
        )
        if not isinstance(payload, Mapping):
            raise TechnicalStructureBuildError(
                "legacy technical builder returned an invalid payload"
            )
        chan_section = payload.get("chan")
        state = chan_section.get("state") if isinstance(chan_section, Mapping) else None
        if not verify_chan_state(state, state_key):
            raise TechnicalStructureBuildError(
                "legacy technical builder returned an invalid Chan state"
            )
        current_state = deepcopy(dict(state))
        self._chan_state_context.set((data.request_id, current_state))
        return self._bounded_payload(payload)

    def data_fingerprint_for(self, request_id: str) -> str:
        current = self._fingerprint_context.get()
        if current is None or current[0] != request_id:
            raise TechnicalStructureBuildError(
                "technical structure data fingerprint is unavailable"
            )
        return current[1]

    def chan_state_for(self, request_id: str) -> dict[str, Any]:
        current = self._chan_state_context.get()
        if current is None or current[0] != request_id:
            raise TechnicalStructureBuildError(
                "technical structure Chan state is unavailable"
            )
        return deepcopy(current[1])

    @staticmethod
    def _frame_from_envelope(data: DataEnvelope) -> pd.DataFrame:
        records = [
            {
                "date": bar.timestamp,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "amount": bar.amount,
            }
            for bar in data.bars
        ]
        if not records:
            raise TechnicalStructureBuildError(
                "technical structure data contains no bars"
            )
        frame = pd.DataFrame.from_records(records).set_index("date").sort_index()
        if frame["amount"].isna().all():
            frame = frame.drop(columns=["amount"])
        return frame

    @staticmethod
    def _load_legacy_builder() -> LegacyPayloadBuilder:
        from custom_model.structures.tech_analysis import build_tech_payload

        return build_tech_payload

    @classmethod
    def _bounded_payload(cls, payload: Mapping[str, Any]) -> dict[str, Any]:
        bounded = {
            key: cls._strip_decision_fields(deepcopy(payload[key]))
            for key in cls._TOP_LEVEL_FIELDS
            if key in payload
        }
        engine = bounded.get("engine")
        if isinstance(engine, str) and engine.strip().casefold() == "v5":
            bounded["engine"] = cls._ADAPTER_ENGINE
            bounded["engineNote"] = (
                "Transitional adapter over the legacy v5 structure mapper; "
                "decision fields are excluded."
            )
        cls._add_section_metadata(bounded)
        return bounded

    @classmethod
    def _strip_decision_fields(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in value.items():
                key = str(raw_key)
                normalized = "".join(
                    character
                    for character in key.casefold()
                    if character.isalnum()
                )
                if (
                    normalized in cls._FORBIDDEN_NORMALIZED_KEYS
                    or normalized.endswith("score")
                    or normalized.endswith("probability")
                    or normalized.endswith("action")
                ):
                    continue
                result[key] = cls._strip_decision_fields(raw_value)
            return result
        if isinstance(value, (list, tuple)):
            return [cls._strip_decision_fields(item) for item in value]
        return value

    @classmethod
    def _add_section_metadata(cls, payload: dict[str, Any]) -> None:
        for key, (source, method_version) in cls._SECTION_METADATA.items():
            section = payload.get(key)
            if not isinstance(section, Mapping):
                continue
            native = dict(section)
            native.setdefault("source", source)
            native.setdefault("methodVersion", method_version)
            payload[key] = native


class SharedTechnicalStructureEndpointService:
    """API facade over the shared Application service.

    The fingerprint currently lives here because the Application result does
    not yet expose it.  It deliberately reuses the market-view algorithm so a
    caller can verify that `/v1/market/bars/query` and this endpoint describe
    the identical adjusted bar series.
    """

    def __init__(
        self,
        service: TechnicalStructureService,
        builder: LegacyTechnicalStructureBuilder,
    ) -> None:
        self.service = service
        self.builder = builder

    def analyze(
        self,
        query: DataQuery,
        *,
        info: Mapping[str, Any] | None = None,
        benchmark_query: DataQuery | None = None,
        has_position: bool = False,
        prior_state: Mapping[str, Any] | None = None,
    ) -> TechnicalStructureEndpointResult:
        view = self.service.analyze(
            query,
            info=info,
            benchmark_query=benchmark_query,
            has_position=has_position,
            prior_state=prior_state,
        )
        return self._endpoint_result(view)

    def analyze_research(
        self,
        research: ResearchResult,
        *,
        confirmed_through: datetime,
        prior_state: Mapping[str, Any] | None = None,
    ) -> TechnicalStructureEndpointResult:
        """Map structures from an already analyzed provisional envelope."""
        try:
            payload = self.builder(
                data=research.data,
                analysis=research.analysis,
                snapshot=research.snapshot,
                state_key=technical_structure_state_key(
                    research.data.instrument, research.data.timeframe
                ),
                prior_state=prior_state,
            )
        except Exception as exc:
            raise TechnicalStructureBuildError(
                f"provisional technical structure builder failed: {exc}"
            ) from exc
        view = self.service.mapper.map(research, payload)
        view = self._downgrade_current_session_confirmations(
            view,
            confirmed_through=confirmed_through,
        )
        return self._endpoint_result(view)

    @staticmethod
    def _downgrade_current_session_confirmations(
        view: TechnicalStructureViewResult,
        *,
        confirmed_through: datetime,
    ) -> TechnicalStructureViewResult:
        """Keep completed-history confirmations, not provisional-bar confirmations."""

        cutoff = pd.Timestamp(confirmed_through)
        downgraded_sections = {}
        downgraded_count = 0
        for name, section in vars(view.sections).items():
            mapped_items = []
            section_changed = False
            for item in section.items:
                timestamps = [item.confirmed_time, item.time, item.end_time]
                timestamps.extend(point.time for point in item.points)
                extends_past_cutoff = False
                for raw_time in timestamps:
                    if not raw_time:
                        continue
                    try:
                        item_time = pd.Timestamp(raw_time)
                    except (TypeError, ValueError):
                        continue
                    if item_time.tzinfo is None and cutoff.tzinfo is not None:
                        item_time = item_time.tz_localize(cutoff.tzinfo)
                    elif item_time.tzinfo is not None and cutoff.tzinfo is None:
                        item_time = item_time.tz_localize(None)
                    if item_time > cutoff:
                        extends_past_cutoff = True
                        break
                if item.confirmed and extends_past_cutoff:
                    item = replace(
                        item,
                        status=SignalStatus.PROVISIONAL,
                        confirmed=False,
                        provisional=True,
                        validated=False,
                        score_eligible=False,
                        alert_eligible=False,
                    )
                    downgraded_count += 1
                    section_changed = True
                mapped_items.append(item)
            if section_changed:
                section = replace(
                    section,
                    items=tuple(mapped_items),
                    warnings=tuple(
                        dict.fromkeys(
                            [
                                *section.warnings,
                                "confirmation after completed daily baseline downgraded to provisional",
                            ]
                        )
                    ),
                )
            downgraded_sections[name] = section
        if not downgraded_count:
            return view
        return replace(
            view,
            sections=replace(view.sections, **downgraded_sections),
            warnings=tuple(
                dict.fromkeys(
                    [
                        *view.warnings,
                        f"{downgraded_count} current-session structure confirmations downgraded to provisional",
                    ]
                )
            ),
        )

    def _endpoint_result(
        self,
        view: TechnicalStructureViewResult,
    ) -> TechnicalStructureEndpointResult:
        sections = tuple(vars(view.sections).values())
        if not any(
            section.status is not SectionAvailability.UNAVAILABLE
            for section in sections
        ):
            raise TechnicalStructureBuildError(
                "technical structure builder produced no usable sections"
            )
        current_state = self.builder.chan_state_for(view.data_ref.request_id)
        return TechnicalStructureEndpointResult(
            view=view,
            data_fingerprint=self.builder.data_fingerprint_for(
                view.data_ref.request_id
            ),
            current_state=current_state,
            continuity=chan_state_continuity(current_state),
        )


def build_shared_technical_structure_service(
    research_use_case: ResearchAnalysisUseCase,
    *,
    payload_builder: LegacyPayloadBuilder | None = None,
) -> SharedTechnicalStructureEndpointService:
    builder = LegacyTechnicalStructureBuilder(payload_builder)
    return SharedTechnicalStructureEndpointService(
        TechnicalStructureService(research_use_case, builder),
        builder,
    )
