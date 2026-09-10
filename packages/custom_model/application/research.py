from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

import pandas as pd

from custom_model.application.analysis_service import AnalysisService
from custom_model.application.data_gateway import DataGateway
from custom_model.domain.models import (
    AnalysisSnapshot,
    DataEnvelope,
    DataQuery,
    MarketRegimeContext,
    PairCandidate,
    SectorOpportunity,
    SignalStatus,
    Timeframe,
)


_SHARED_ENRICHMENT_WARNINGS = (
    "news enrichment disabled: legacy network sources and synthetic fallback have no DataEnvelope lineage",
    "intraday Elliott enrichment disabled: legacy minute bars have no DataEnvelope lineage",
)


@dataclass(frozen=True)
class ResearchResult:
    data: DataEnvelope
    analysis: Mapping[str, Any]
    snapshot: AnalysisSnapshot
    warnings: tuple[str, ...] = ()


class ResearchAnalysisUseCase:
    """Single application entrypoint shared by Streamlit and FastAPI adapters."""

    _LEGACY_TIMEFRAMES = {
        Timeframe.M1: "1分钟",
        Timeframe.M5: "5分钟",
        Timeframe.M15: "15分钟",
        Timeframe.M30: "30分钟",
        Timeframe.H1: "小时线",
        Timeframe.D1: "日线",
        Timeframe.W1: "周线",
        Timeframe.MO1: "月线",
    }

    def __init__(
        self,
        data_gateway: DataGateway,
        analysis_service: AnalysisService | None = None,
        *,
        indicator_builder: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
        analysis_runner: Callable[..., dict] | None = None,
    ) -> None:
        self.data_gateway = data_gateway
        self.analysis_service = analysis_service or AnalysisService()
        self._indicator_builder = indicator_builder
        self._analysis_runner = analysis_runner

    @staticmethod
    def frame_from_envelope(envelope: DataEnvelope) -> pd.DataFrame:
        records = []
        for bar in envelope.bars:
            records.append(
                {
                    "Date": bar.timestamp,
                    "Open": bar.open,
                    "High": bar.high,
                    "Low": bar.low,
                    "Close": bar.close,
                    "Volume": bar.volume,
                    "Amount": bar.amount,
                }
            )
        frame = pd.DataFrame.from_records(records).set_index("Date")
        if frame["Amount"].isna().all():
            frame = frame.drop(columns=["Amount"])
        return frame.sort_index()

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
    ) -> ResearchResult:
        envelope = self.data_gateway.fetch_bars(query)
        benchmark = None
        if benchmark_query is not None:
            benchmark = self.data_gateway.fetch_bars(benchmark_query)
        return self.analyze_envelope(
            query,
            envelope,
            info=info,
            benchmark_query=benchmark_query,
            benchmark_envelope=benchmark,
            market_regime=market_regime,
            sector_context=sector_context,
            pair_context=pair_context,
            bar_status=bar_status,
            has_position=has_position,
        )

    def analyze_envelope(
        self,
        query: DataQuery,
        envelope: DataEnvelope,
        *,
        info: Mapping[str, Any] | None = None,
        benchmark_query: DataQuery | None = None,
        benchmark_envelope: DataEnvelope | None = None,
        market_regime: MarketRegimeContext | None = None,
        sector_context: SectorOpportunity | None = None,
        pair_context: PairCandidate | None = None,
        bar_status: SignalStatus = SignalStatus.CONFIRMED,
        has_position: bool = False,
        allow_stale_provisional: bool = False,
    ) -> ResearchResult:
        """Run the canonical analysis over an already sourced, identity-bound envelope."""
        if (
            envelope.instrument != query.instrument
            or envelope.timeframe is not query.timeframe
            or envelope.purpose is not query.purpose
            or envelope.adjustment is not query.adjustment
            or envelope.as_of != query.as_of
        ):
            raise ValueError("analysis envelope does not match query identity")
        if (benchmark_query is None) != (benchmark_envelope is None):
            raise ValueError("benchmark query and envelope must be supplied together")

        frame = self.frame_from_envelope(envelope)
        indicator_builder = self._indicator_builder
        if indicator_builder is None:
            from modules.technical import add_all_indicators

            indicator_builder = add_all_indicators
        prepared = indicator_builder(frame)

        benchmark_frame = None
        additional_lineage = []
        if benchmark_query is not None and benchmark_envelope is not None:
            if (
                benchmark_envelope.instrument != benchmark_query.instrument
                or benchmark_envelope.timeframe is not benchmark_query.timeframe
                or benchmark_envelope.purpose is not benchmark_query.purpose
                or benchmark_envelope.adjustment is not benchmark_query.adjustment
                or benchmark_envelope.as_of != benchmark_query.as_of
            ):
                raise ValueError("benchmark envelope does not match benchmark query")
            benchmark_frame = self.frame_from_envelope(benchmark_envelope)
            additional_lineage.append(benchmark_envelope.lineage())

        runner = self._analysis_runner
        uses_default_legacy_runner = runner is None
        if uses_default_legacy_runner:
            from modules.analysis import run_full_analysis

            runner = run_full_analysis
        legacy_info = dict(info or {})
        legacy_info.setdefault("symbol", query.instrument.symbol)
        runner_options: dict[str, Any] = {}
        enrichment_warnings: tuple[str, ...] = ()
        if uses_default_legacy_runner:
            # The Shared path may only consume evidence fetched through the
            # gateway and bound to a DataEnvelope.  The legacy runner's news
            # and minute-Elliott fetches bypass that contract, so stop them
            # before either adapter is imported or called.
            runner_options.update(
                with_news=False,
                with_intraday_elliott=False,
            )
            enrichment_warnings = _SHARED_ENRICHMENT_WARNINGS
        analysis = runner(
            prepared,
            legacy_info,
            timeframe=self._LEGACY_TIMEFRAMES[query.timeframe],
            benchmark_df=benchmark_frame,
            **runner_options,
        )
        snapshot = self.analysis_service.build_snapshot(
            envelope,
            analysis,
            market_regime=market_regime,
            sector_context=sector_context,
            pair_context=pair_context,
            bar_status=bar_status,
            has_position=has_position,
            additional_lineage=additional_lineage,
            allow_stale_provisional=allow_stale_provisional,
        )
        if enrichment_warnings:
            snapshot = snapshot.model_copy(
                update={
                    "warnings": list(
                        dict.fromkeys([*snapshot.warnings, *enrichment_warnings])
                    )
                }
            )
        return ResearchResult(
            data=envelope,
            analysis=analysis,
            snapshot=snapshot,
            warnings=enrichment_warnings,
        )
