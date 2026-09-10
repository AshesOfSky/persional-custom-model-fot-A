from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from custom_model.domain.models import (
    AnalysisSnapshot,
    DataEnvelope,
    DataLineage,
    DataMode,
    DecisionAction,
    Invalidation,
    KeyLevel,
    MarketRegimeContext,
    MarketRegimeState,
    PairCandidate,
    QualityStatus,
    ScoreComponent,
    SectorOpportunity,
    SignalStatus,
)


class DecisionInputError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceScoreSummary:
    """Canonical, recalculated legacy evidence score used by every adapter."""

    score: float
    components: tuple[ScoreComponent, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class DecisionConfig:
    version: str = "swing-a-long-cash-v2"
    engine_version: str = "canonical-decision-1.1.0"
    long_threshold: float = 65.0
    hold_threshold: float = 50.0
    exit_trend_threshold: float = 38.0
    minimum_context_score: float = 50.0


class AnalysisService:
    """Build one deterministic decision snapshot from legacy analysis evidence."""

    def __init__(self, config: DecisionConfig | None = None) -> None:
        self.config = config or DecisionConfig()

    def build_snapshot(
        self,
        envelope: DataEnvelope,
        legacy_analysis: Mapping[str, Any],
        *,
        market_regime: MarketRegimeContext | None = None,
        sector_context: SectorOpportunity | None = None,
        pair_context: PairCandidate | None = None,
        bar_status: SignalStatus = SignalStatus.CONFIRMED,
        has_position: bool = False,
        additional_lineage: list[DataLineage] | None = None,
        allow_stale_provisional: bool = False,
    ) -> AnalysisSnapshot:
        self._validate_envelope(
            envelope,
            bar_status=bar_status,
            allow_stale_provisional=allow_stale_provisional,
        )
        score_summary = self.score_legacy_evidence(legacy_analysis)
        components = list(score_summary.components)
        score_warnings = list(score_summary.warnings)
        evidence_score = score_summary.score
        action, action_reasons, action_warnings = self._action(
            evidence_score,
            legacy_analysis,
            market_regime,
            sector_context,
            pair_context,
            has_position,
        )
        key_levels = self._key_levels(legacy_analysis)
        invalidation = self._invalidation(legacy_analysis, envelope)
        explanations = self._explanations(legacy_analysis, action_reasons)
        warnings = [*envelope.warnings, *score_warnings, *action_warnings]
        if legacy_analysis.get("comprehensive_probability"):
            warnings.append("legacy comprehensive_probability is uncalibrated and excluded")

        snapshot_id = self._snapshot_id(
            envelope=envelope,
            additional_lineage=additional_lineage or [],
            evidence_score=evidence_score,
            action=action,
            components=components,
            market_regime=market_regime,
            sector_context=sector_context,
            pair_context=pair_context,
        )
        return AnalysisSnapshot(
            snapshot_id=snapshot_id,
            instrument=envelope.instrument,
            timeframe=envelope.timeframe,
            as_of=envelope.as_of,
            bar_status=bar_status,
            engine_version=self.config.engine_version,
            config_version=self.config.version,
            data_lineage=[envelope.lineage(), *(additional_lineage or [])],
            market_regime=market_regime,
            sector_context=sector_context,
            pair_context=pair_context,
            component_scores=components,
            evidence_score=evidence_score,
            action=action,
            key_levels=key_levels,
            invalidation=invalidation,
            explanations=explanations,
            warnings=list(dict.fromkeys(warnings)),
        )

    @classmethod
    def score_legacy_evidence(
        cls, legacy_analysis: Mapping[str, Any]
    ) -> EvidenceScoreSummary:
        """Recalculate the evidence score from enabled dimensions and weights."""

        components, warnings = cls._components(legacy_analysis)
        score = round(
            sum(item.score * item.weight for item in components if item.enabled), 1
        )
        return EvidenceScoreSummary(
            score=score,
            components=tuple(components),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_envelope(
        envelope: DataEnvelope,
        *,
        bar_status: SignalStatus,
        allow_stale_provisional: bool,
    ) -> None:
        if envelope.is_synthetic or envelope.mode is DataMode.DEMO:
            raise DecisionInputError("synthetic/demo data cannot create a production snapshot")
        if envelope.mode is DataMode.FALLBACK:
            raise DecisionInputError("fallback data cannot create a production snapshot")
        stale_preview_allowed = (
            allow_stale_provisional
            and bar_status is SignalStatus.PROVISIONAL
            and envelope.quality is QualityStatus.STALE
        )
        if envelope.quality is not QualityStatus.VALID and not stale_preview_allowed:
            raise DecisionInputError(f"data quality must be valid, got {envelope.quality.value}")
        if not envelope.bars:
            raise DecisionInputError("at least one bar is required")

    @staticmethod
    def _components(
        legacy_analysis: Mapping[str, Any],
    ) -> tuple[list[ScoreComponent], list[str]]:
        warnings: list[str] = []
        action_plan = legacy_analysis.get("buy_action_plan") or {}
        buy_score = action_plan.get("buy_score") or {}
        dimensions = buy_score.get("dimensions") or {}

        raw: list[tuple[str, float, float, str]] = []
        for name, item in dimensions.items():
            if not isinstance(item, Mapping):
                continue
            try:
                score = max(0.0, min(100.0, float(item["score"])))
                weight = max(0.0, float(item["weight"]))
            except (KeyError, TypeError, ValueError):
                warnings.append(f"ignored malformed score dimension: {name}")
                continue
            raw.append((str(name), score, weight, str(item.get("label") or name)))

        if not raw:
            trend = legacy_analysis.get(
                "trend_strength_score", legacy_analysis.get("trend_strength")
            )
            if not isinstance(trend, (int, float)):
                raise DecisionInputError("no canonical score dimensions or trend score")
            warnings.append("component breakdown unavailable; snapshot uses trend only")
            raw = [("trend", max(0.0, min(100.0, float(trend))), 1.0, "趋势")]

        total_weight = sum(item[2] for item in raw)
        if total_weight <= 0:
            raise DecisionInputError("enabled score weights must sum above zero")
        components = [
            ScoreComponent(
                name=name,
                score=score,
                weight=weight / total_weight,
                evidence=[f"{label}: {score:.1f}/100"],
            )
            for name, score, weight, label in raw
        ]

        legacy_total = buy_score.get("total_score")
        canonical_total = sum(item.score * item.weight for item in components)
        if isinstance(legacy_total, (int, float)) and abs(float(legacy_total) - canonical_total) > 0.2:
            warnings.append("legacy total score differed from recomputed component score")
        return components, warnings

    def _action(
        self,
        evidence_score: float,
        legacy_analysis: Mapping[str, Any],
        market_regime: MarketRegimeContext | None,
        sector_context: SectorOpportunity | None,
        pair_context: PairCandidate | None,
        has_position: bool,
    ) -> tuple[DecisionAction, list[str], list[str]]:
        reasons: list[str] = []
        warnings: list[str] = []
        trend_score = legacy_analysis.get(
            "trend_strength_score", legacy_analysis.get("trend_strength", 50)
        )
        trend_score = float(trend_score) if isinstance(trend_score, (int, float)) else 50.0
        position_type = str(
            (legacy_analysis.get("buy_action_plan") or {}).get("position_type")
            or legacy_analysis.get("position_type")
            or ""
        )

        if "破位" in position_type or trend_score < self.config.exit_trend_threshold:
            reasons.append("trend/breakdown invalidation is active")
            return (
                DecisionAction.EXIT if has_position else DecisionAction.CASH,
                reasons,
                warnings,
            )

        if market_regime and market_regime.state is MarketRegimeState.RISK_OFF:
            reasons.append("market regime is risk-off")
            return (
                DecisionAction.REDUCE if has_position else DecisionAction.CASH,
                reasons,
                warnings,
            )

        market_ok = bool(
            market_regime
            and market_regime.state in {MarketRegimeState.RISK_ON, MarketRegimeState.NEUTRAL}
            and market_regime.score >= self.config.minimum_context_score
        )
        sector_ok = bool(
            sector_context
            and sector_context.eligible
            and sector_context.score >= self.config.minimum_context_score
        )
        pair_ok = pair_context is None or pair_context.eligible

        if market_regime is None:
            warnings.append("market regime missing; LONG is gated off")
        if sector_context is None:
            warnings.append("sector context missing; LONG is gated off")
        if pair_context is not None and not pair_context.eligible:
            warnings.append("ETF/leader pair failed trend, liquidity or capacity gate")

        if evidence_score >= self.config.long_threshold and market_ok and sector_ok and pair_ok:
            reasons.extend(["evidence threshold passed", "market and sector gates passed"])
            return DecisionAction.LONG, reasons, warnings
        if has_position and evidence_score >= self.config.hold_threshold:
            reasons.append("existing position remains above hold threshold")
            return DecisionAction.HOLD, reasons, warnings
        reasons.append("entry gates are incomplete or evidence is below threshold")
        return DecisionAction.CASH, reasons, warnings

    @staticmethod
    def _key_levels(legacy_analysis: Mapping[str, Any]) -> list[KeyLevel]:
        daily = ((legacy_analysis.get("multi_tf_sr") or {}).get("daily") or {})
        levels: list[KeyLevel] = []
        for kind, key in (("support", "all_supports"), ("resistance", "all_resistances")):
            for item in list(daily.get(key) or [])[:3]:
                if isinstance(item, Mapping):
                    price = item.get("price")
                    provenance = str(item.get("method") or item.get("source") or "legacy-sr")
                else:
                    price = item
                    provenance = "legacy-sr"
                try:
                    price_value = float(price)
                except (TypeError, ValueError):
                    continue
                if price_value > 0:
                    levels.append(
                        KeyLevel(
                            kind=kind,
                            price=price_value,
                            provenance=provenance,
                            status=SignalStatus.PROVISIONAL,
                        )
                    )
        return levels

    @staticmethod
    def _invalidation(
        legacy_analysis: Mapping[str, Any], envelope: DataEnvelope
    ) -> list[Invalidation]:
        targets = ((legacy_analysis.get("buy_action_plan") or {}).get("targets") or {})
        stop = targets.get("stop_loss")
        try:
            stop_value = float(stop)
        except (TypeError, ValueError):
            return []
        if stop_value <= 0:
            return []
        confirmation = (
            "confirmed bar close below stop"
            if envelope.timeframe.value.endswith(("m", "h"))
            else "daily close below stop"
        )
        return [
            Invalidation(
                description="long thesis invalidation",
                price=stop_value,
                confirmation=confirmation,
            )
        ]

    @staticmethod
    def _explanations(
        legacy_analysis: Mapping[str, Any], action_reasons: list[str]
    ) -> list[str]:
        explanations = list(action_reasons)
        for value in (
            legacy_analysis.get("trend_strength_level"),
            (legacy_analysis.get("multi_timeframe_summary") or {}).get("summary"),
            (legacy_analysis.get("divergence_summary") or {}).get("strongest_divergence"),
            (((legacy_analysis.get("buy_action_plan") or {}).get("buy_score") or {}).get("verdict")),
        ):
            if value:
                explanations.append(str(value))
        return list(dict.fromkeys(explanations))

    def _snapshot_id(
        self,
        *,
        envelope: DataEnvelope,
        additional_lineage: list[DataLineage],
        evidence_score: float,
        action: DecisionAction,
        components: list[ScoreComponent],
        market_regime: MarketRegimeContext | None,
        sector_context: SectorOpportunity | None,
        pair_context: PairCandidate | None,
    ) -> str:
        payload = {
            "instrument": envelope.instrument.key,
            "timeframe": envelope.timeframe.value,
            "as_of": envelope.as_of.isoformat(),
            "request_id": envelope.request_id,
            "additional_lineage": [
                item.model_dump(mode="json") for item in additional_lineage
            ],
            "engine_version": self.config.engine_version,
            "config_version": self.config.version,
            "evidence_score": evidence_score,
            "action": action.value,
            "components": [item.model_dump(mode="json") for item in components],
            "market_regime": market_regime.model_dump(mode="json") if market_regime else None,
            "sector_context": sector_context.model_dump(mode="json") if sector_context else None,
            "pair_context": pair_context.model_dump(mode="json") if pair_context else None,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:32]
