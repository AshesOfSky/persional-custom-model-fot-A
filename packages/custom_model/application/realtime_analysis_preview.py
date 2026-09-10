from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from custom_model.application.analysis_service import DecisionInputError
from custom_model.application.research import ResearchAnalysisUseCase, ResearchResult
from custom_model.domain.models import (
    Bar,
    DataEnvelope,
    DataMode,
    DataQuery,
    QualityStatus,
    QuoteEnvelope,
    QuoteSessionStatus,
    QuoteTemporalMode,
    SignalStatus,
    Timeframe,
)


REALTIME_ANALYSIS_PREVIEW_SCHEMA_VERSION = "realtime-analysis-preview/v1"


@dataclass(frozen=True)
class RealtimeAnalysisPreviewComputation:
    schema_version: str
    completed: ResearchResult
    preview: ResearchResult
    quote: QuoteEnvelope
    session_label: str
    advisory_only: bool = True
    formal_use_eligible: bool = False


class RealtimeAnalysisPreviewService:
    """Rerun the canonical daily analysis over one unclosed native quote bar."""

    def __init__(self, research_use_case: ResearchAnalysisUseCase) -> None:
        self.research_use_case = research_use_case

    def analyze(
        self,
        query: DataQuery,
        quote: QuoteEnvelope,
        *,
        info: Mapping[str, Any] | None = None,
        has_position: bool = False,
    ) -> RealtimeAnalysisPreviewComputation:
        if query.timeframe is not Timeframe.D1:
            raise DecisionInputError("realtime full analysis preview requires daily bars")
        completed = self.research_use_case.analyze(
            query,
            info=info,
            has_position=has_position,
        )
        session_label = self._validate_quote(query, completed.data, quote)
        preview_envelope = self._append_quote(completed.data, quote, session_label)
        preview = self.research_use_case.analyze_envelope(
            query,
            preview_envelope,
            info=info,
            bar_status=SignalStatus.PROVISIONAL,
            has_position=has_position,
            allow_stale_provisional=quote.quality is QualityStatus.STALE,
        )
        return RealtimeAnalysisPreviewComputation(
            schema_version=REALTIME_ANALYSIS_PREVIEW_SCHEMA_VERSION,
            completed=completed,
            preview=preview,
            quote=quote,
            session_label=session_label,
        )

    @staticmethod
    def _validate_quote(
        query: DataQuery,
        completed: DataEnvelope,
        quote: QuoteEnvelope,
    ) -> str:
        if quote.instrument != query.instrument or quote.instrument != completed.instrument:
            raise DecisionInputError("realtime quote instrument does not match analysis")
        if quote.purpose is not query.purpose or quote.purpose is not completed.purpose:
            raise DecisionInputError("realtime quote purpose does not match analysis")
        if quote.temporal_mode is not QuoteTemporalMode.LATEST:
            raise DecisionInputError("realtime analysis requires a latest native quote")
        if quote.as_of != query.as_of or completed.as_of != query.as_of:
            raise DecisionInputError("realtime quote and completed bars must share as_of")
        if quote.is_synthetic or not quote.source_chain:
            raise DecisionInputError("realtime analysis requires a sourced native quote")
        if quote.timezone != completed.timezone:
            raise DecisionInputError("realtime quote timezone does not match daily bars")

        zone = ZoneInfo(completed.timezone)
        quote_time = quote.data_time.astimezone(zone)
        as_of = quote.as_of.astimezone(zone)
        if quote_time.date() != as_of.date():
            raise DecisionInputError("realtime quote is not from the current session date")
        if completed.last_bar_at is not None:
            completed_day = completed.last_bar_at.astimezone(zone).date()
            if quote_time.date() <= completed_day:
                raise DecisionInputError("completed daily data already covers the quote date")

        if quote.session_status is QuoteSessionStatus.OPEN:
            if (
                quote.quality is not QualityStatus.VALID
                or not quote.trade_status_verified
                or quote.data_time_basis != "provider_timestamp"
            ):
                raise DecisionInputError("open-session quote is not valid and verified")
            return "盘中实时快照"

        if quote.session_status is QuoteSessionStatus.MIDDAY_BREAK:
            if (
                quote.data_time_basis != "cn_midday_close"
                or quote_time.time().replace(tzinfo=None) != time(11, 30)
                or quote.mode not in {DataMode.LIVE, DataMode.DELAYED}
                or quote.quality not in {QualityStatus.VALID, QualityStatus.STALE}
            ):
                raise DecisionInputError("midday quote is not the verified 11:30 session snapshot")
            return "午间11:30快照"

        if quote.session_status is QuoteSessionStatus.AFTER_CLOSE:
            if (
                quote.data_time_basis != "cn_session_close"
                or quote_time.time().replace(tzinfo=None) != time(15, 0)
                or quote.mode not in {DataMode.LIVE, DataMode.DELAYED}
                or quote.quality not in {QualityStatus.VALID, QualityStatus.STALE}
            ):
                raise DecisionInputError("after-close quote is not the verified 15:00 snapshot")
            return "收盘15:00临时快照"

        raise DecisionInputError("quote session is not eligible for intraday analysis")

    @staticmethod
    def _append_quote(
        completed: DataEnvelope,
        quote: QuoteEnvelope,
        session_label: str,
    ) -> DataEnvelope:
        index_timezone = (
            completed.bars[-1].timestamp.tzinfo
            if completed.bars and completed.bars[-1].timestamp.tzinfo is not None
            else ZoneInfo(completed.timezone)
        )
        quote_time = quote.data_time.astimezone(index_timezone)
        provisional_bar = Bar(
            timestamp=quote_time,
            open=float(quote.open),
            high=float(quote.high),
            low=float(quote.low),
            close=float(quote.last_price),
            volume=float(quote.volume_lots),
            amount=float(quote.amount),
        )
        return DataEnvelope(
            instrument=completed.instrument,
            timeframe=completed.timeframe,
            purpose=completed.purpose,
            bars=[*completed.bars, provisional_bar],
            provider=f"{completed.provider}+{quote.provider}:provisional",
            mode=quote.mode,
            quality=quote.quality,
            as_of=quote.as_of,
            fetched_at=quote.fetched_at,
            last_bar_at=quote_time,
            timezone=completed.timezone,
            adjustment=completed.adjustment,
            freshness_seconds=float(quote.freshness_seconds),
            is_synthetic=False,
            warnings=list(
                dict.fromkeys(
                    [
                        *completed.warnings,
                        *quote.warnings,
                        f"{session_label} appended as an unclosed daily bar",
                        "provisional analysis can change before the daily close is confirmed",
                    ]
                )
            ),
            request_id=f"realtime-preview-{quote.request_id}",
            source_chain=list(dict.fromkeys([*completed.source_chain, *quote.source_chain])),
            calendar_snapshot_id=quote.calendar_snapshot_id,
            calendar_content_sha256=quote.calendar_content_sha256,
        )


__all__ = [
    "REALTIME_ANALYSIS_PREVIEW_SCHEMA_VERSION",
    "RealtimeAnalysisPreviewComputation",
    "RealtimeAnalysisPreviewService",
]
