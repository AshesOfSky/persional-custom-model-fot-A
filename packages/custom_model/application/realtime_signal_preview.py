from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from custom_model.application.analysis_service import DecisionInputError
from custom_model.application.research import ResearchAnalysisUseCase
from custom_model.application.realtime_analysis_preview import RealtimeAnalysisPreviewService
from custom_model.domain.models import (
    DataEnvelope,
    DataMode,
    DataPurpose,
    DataQuery,
    QualityStatus,
    QuoteEnvelope,
    QuoteSessionStatus,
    QuoteTemporalMode,
    SignalStatus,
    Timeframe,
)


REALTIME_SIGNAL_PREVIEW_SCHEMA_VERSION = "realtime-signal-preview/v1"
SIGNAL_EVIDENCE_SEMANTICS = "signal_evidence_score_not_probability"
_ELIGIBLE_SESSIONS = frozenset(
    {
        QuoteSessionStatus.OPEN,
        QuoteSessionStatus.MIDDAY_BREAK,
        QuoteSessionStatus.AFTER_CLOSE,
    }
)


@dataclass(frozen=True)
class RealtimeSignalPreviewComputation:
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
    composite_signals: Mapping[str, tuple[Mapping[str, Any], ...]]
    warnings: tuple[str, ...]
    advisory_only: bool = True
    formal_use_eligible: bool = False


class RealtimeSignalPreviewService:
    """Calculate an advisory signal on one unclosed D1 bar in memory only."""

    def __init__(
        self,
        *,
        indicator_builder: Callable[[pd.DataFrame], pd.DataFrame] | None = None,
        level_builder: Callable[[pd.DataFrame], Mapping[str, Any]] | None = None,
        signal_builder: Callable[
            [pd.DataFrame, Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]
        ]
        | None = None,
    ) -> None:
        self._indicator_builder = indicator_builder
        self._level_builder = level_builder
        self._signal_builder = signal_builder

    def build(
        self,
        completed: DataEnvelope,
        quote: QuoteEnvelope,
    ) -> RealtimeSignalPreviewComputation:
        self._validate_inputs(completed, quote)
        quote_time = quote.data_time.astimezone(ZoneInfo(completed.timezone))
        bar_time = quote_time.date().isoformat()

        session_label = None
        if quote.session_status in {QuoteSessionStatus.MIDDAY_BREAK, QuoteSessionStatus.AFTER_CLOSE}:
            # Reuse the existing full-preview rule for sourced 11:30/15:00
            # snapshots. Their wall-clock age grows while the market is closed.
            try:
                session_label = RealtimeAnalysisPreviewService._validate_quote(
                    DataQuery(instrument=completed.instrument, timeframe=completed.timeframe,
                        purpose=completed.purpose, adjustment=completed.adjustment, as_of=completed.as_of),
                    completed, quote,
                )
            except DecisionInputError as exc:
                return self._unavailable(completed, quote, bar_time, str(exc))

        if session_label is None and quote.quality is not QualityStatus.VALID:
            return self._unavailable(
                completed,
                quote,
                bar_time,
                "realtime signal preview requires a valid quote",
            )
        if session_label is None and not quote.trade_status_verified:
            return self._unavailable(
                completed,
                quote,
                bar_time,
                "realtime signal preview requires verified trade status",
            )
        if quote.session_status not in _ELIGIBLE_SESSIONS:
            return self._unavailable(
                completed,
                quote,
                bar_time,
                "quote session is not eligible for an intraday daily-bar preview",
            )

        completed_through = completed.last_bar_at
        if completed_through is not None:
            completed_date = completed_through.astimezone(
                ZoneInfo(completed.timezone)
            ).date()
            if quote_time.date() <= completed_date:
                return self._unavailable(
                    completed,
                    quote,
                    bar_time,
                    "completed daily data already covers the quote date",
                )

        frame = ResearchAnalysisUseCase.frame_from_envelope(completed)
        provisional = pd.DataFrame.from_records(
            [
                {
                    "Date": quote_time,
                    "Open": float(quote.open),
                    "High": float(quote.high),
                    "Low": float(quote.low),
                    "Close": float(quote.last_price),
                    "Volume": float(quote.volume_lots),
                    "Amount": float(quote.amount),
                }
            ]
        ).set_index("Date")
        frame = pd.concat([frame, provisional]).sort_index()

        indicator_builder = self._indicator_builder
        if indicator_builder is None:
            from modules.technical import add_all_indicators

            indicator_builder = add_all_indicators
        prepared = indicator_builder(frame)
        if prepared.empty or prepared.index[-1] != quote_time:
            raise DecisionInputError(
                "realtime signal preview lost the provisional quote bar"
            )

        level_builder = self._level_builder
        if level_builder is None:
            from modules.support_resistance import calc_daily_levels

            level_builder = calc_daily_levels
        levels = level_builder(prepared)

        signal_builder = self._signal_builder
        if signal_builder is None:
            from modules.signal_generator import generate_composite_signals

            signal_builder = generate_composite_signals
        composite = signal_builder(prepared, {}, levels)
        last_index = len(prepared) - 1
        filtered: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for key in ("buy_signals", "sell_signals"):
            raw_points = composite.get(key) or []
            filtered[key] = tuple(
                point
                for point in raw_points
                if isinstance(point, Mapping) and point.get("bar_index") == last_index
            )

        warnings = [
            "provisional signal uses an unclosed daily bar and can change before confirmation",
            "signal evidence score is not a probability",
        ]
        if session_label is not None:
            warnings.append(f"{session_label}预估；不代表新的实时成交或正式信号")
        if not filtered["buy_signals"] and not filtered["sell_signals"]:
            warnings.append(
                "provisional bar did not trigger two independent evidence buckets"
            )
        return self._result(
            completed,
            quote,
            bar_time,
            available=True,
            composite_signals=filtered,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _validate_inputs(completed: DataEnvelope, quote: QuoteEnvelope) -> None:
        if completed.timeframe is not Timeframe.D1:
            raise DecisionInputError("realtime signal preview requires daily bars")
        if completed.purpose is not DataPurpose.RESEARCH:
            raise DecisionInputError("realtime signal preview requires research bars")
        if not completed.bars:
            raise DecisionInputError("realtime signal preview requires completed daily bars")
        if quote.instrument != completed.instrument:
            raise DecisionInputError("realtime quote instrument does not match daily bars")
        if quote.purpose is not completed.purpose:
            raise DecisionInputError("realtime quote purpose does not match daily bars")
        if quote.temporal_mode is not QuoteTemporalMode.LATEST:
            raise DecisionInputError("realtime signal preview requires a latest quote")
        if quote.as_of != completed.as_of:
            raise DecisionInputError("realtime quote and daily bars must share as_of")

    def _unavailable(
        self,
        completed: DataEnvelope,
        quote: QuoteEnvelope,
        bar_time: str,
        warning: str,
    ) -> RealtimeSignalPreviewComputation:
        return self._result(
            completed,
            quote,
            bar_time,
            available=False,
            composite_signals={"buy_signals": (), "sell_signals": ()},
            warnings=(warning,),
        )

    @staticmethod
    def _result(
        completed: DataEnvelope,
        quote: QuoteEnvelope,
        bar_time: str,
        *,
        available: bool,
        composite_signals: Mapping[str, tuple[Mapping[str, Any], ...]],
        warnings: tuple[str, ...],
    ) -> RealtimeSignalPreviewComputation:
        return RealtimeSignalPreviewComputation(
            schema_version=REALTIME_SIGNAL_PREVIEW_SCHEMA_VERSION,
            available=available,
            status=SignalStatus.PROVISIONAL,
            bar_time=bar_time,
            completed_through=completed.last_bar_at,
            daily_request_id=completed.request_id,
            quote_request_id=quote.request_id,
            daily_provider=completed.provider,
            quote_provider=quote.provider,
            quote_mode=quote.mode,
            quote_quality=quote.quality,
            score_semantics=SIGNAL_EVIDENCE_SEMANTICS,
            composite_signals=composite_signals,
            warnings=warnings,
        )
