from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from custom_model.application.market_view import MarketBarsViewService
from custom_model.application.realtime_analysis_preview import (
    RealtimeAnalysisPreviewComputation,
    RealtimeAnalysisPreviewService,
)
from custom_model.application.research_view import ResearchViewMapper, ResearchViewResult
from custom_model.domain.models import DataQuery, QuoteEnvelope

from .technical_structures import (
    TechnicalStructureEndpointResult,
    TechnicalStructureEndpointService,
)


@dataclass(frozen=True)
class RealtimeAnalysisEndpointResult:
    computation: RealtimeAnalysisPreviewComputation
    preview_view: ResearchViewResult
    structures: TechnicalStructureEndpointResult


class RealtimeAnalysisEndpointService:
    """Compose one full provisional view without a second market-bars fetch."""

    def __init__(
        self,
        preview_service: RealtimeAnalysisPreviewService,
        market_view_service: MarketBarsViewService,
        technical_structure_service: TechnicalStructureEndpointService,
        mapper: ResearchViewMapper | None = None,
    ) -> None:
        self.preview_service = preview_service
        self.market_view_service = market_view_service
        self.technical_structure_service = technical_structure_service
        self.mapper = mapper or ResearchViewMapper()

    def analyze(
        self,
        query: DataQuery,
        quote: QuoteEnvelope,
        *,
        info: Mapping[str, Any] | None = None,
        has_position: bool = False,
    ) -> RealtimeAnalysisEndpointResult:
        computation = self.preview_service.analyze(
            query,
            quote,
            info=info,
            has_position=has_position,
        )
        market_view = self.market_view_service.build_view(computation.preview.data)
        preview_view = self.mapper.map(computation.preview, market_view)
        structures = self.technical_structure_service.analyze_research(
            computation.preview,
            confirmed_through=computation.completed.data.last_bar_at,
        )
        return RealtimeAnalysisEndpointResult(
            computation=computation,
            preview_view=preview_view,
            structures=structures,
        )


__all__ = [
    "RealtimeAnalysisEndpointResult",
    "RealtimeAnalysisEndpointService",
]
