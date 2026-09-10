from __future__ import annotations

from fastapi import Request

from custom_model.application.data_gateway import DataGateway

from custom_model.application.fundamental_service import FundamentalService

from custom_model.application.instruments import InstrumentSearchService

from custom_model.application.market_view import MarketBarsViewService

from custom_model.application.quote_view import QuoteViewService

from custom_model.application.quote_refresh_service import QuoteRefreshService

from custom_model.application.research import ResearchAnalysisUseCase

from custom_model.application.realtime_analysis_preview import RealtimeAnalysisPreviewService

from custom_model.application.research_view import ResearchViewService

from custom_model.application.watchlist_service import WatchlistService

from .technical_structures import TechnicalStructureEndpointService

from .realtime_analysis import RealtimeAnalysisEndpointService

class ApiConfigurationError(RuntimeError):
    """Raised when an adapter has no application service wired into it."""


def get_watchlist_service(request: Request) -> WatchlistService:
    service = getattr(request.app.state, "watchlist_service", None)
    if service is None:
        raise ApiConfigurationError("watchlist service is not configured")
    return service


def get_research_use_case(request: Request) -> ResearchAnalysisUseCase:
    use_case = getattr(request.app.state, "research_use_case", None)
    if use_case is None:
        raise ApiConfigurationError("analysis service is not configured")
    return use_case


def get_data_gateway(request: Request) -> DataGateway:
    gateway = getattr(request.app.state, "data_gateway", None)
    if gateway is None:
        raise ApiConfigurationError("market data service is not configured")
    return gateway


def get_instrument_search_service(request: Request) -> InstrumentSearchService:
    service = getattr(request.app.state, "instrument_search_service", None)
    if service is None:
        raise ApiConfigurationError("instrument search service is not configured")
    return service


def get_market_bars_view_service(request: Request) -> MarketBarsViewService:
    """Build the typed view service over the app's one shared data gateway."""

    return MarketBarsViewService(get_data_gateway(request))


def get_quote_view_service(request: Request) -> QuoteViewService:
    service = getattr(request.app.state, "quote_view_service", None)
    if service is None:
        raise ApiConfigurationError("quote service is not configured")
    return service


def get_quote_refresh_service(request: Request) -> QuoteRefreshService:
    service = getattr(request.app.state, "quote_refresh_service", None)
    if service is None:
        raise ApiConfigurationError("quote refresh service is not configured")
    return service


def get_research_view_service(request: Request) -> ResearchViewService:
    """Compose the bounded research view without issuing a second data query."""

    return ResearchViewService(
        get_research_use_case(request),
        get_market_bars_view_service(request),
    )


def get_realtime_analysis_preview_service(
    request: Request,
) -> RealtimeAnalysisEndpointService:
    return RealtimeAnalysisEndpointService(
        RealtimeAnalysisPreviewService(get_research_use_case(request)),
        get_market_bars_view_service(request),
        get_technical_structure_service(request),
    )


def get_technical_structure_service(
    request: Request,
) -> TechnicalStructureEndpointService:
    service = getattr(request.app.state, "technical_structure_service", None)
    if service is None:
        raise ApiConfigurationError("technical structure service is not configured")
    return service


def get_fundamental_service(request: Request) -> FundamentalService:
    service = getattr(request.app.state, "fundamental_service", None)
    if service is None:
        raise ApiConfigurationError("fundamental service is not configured")
    return service


