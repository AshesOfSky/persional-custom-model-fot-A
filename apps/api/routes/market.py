from fastapi import APIRouter, Depends

from custom_model.application.market_view import MarketBarsViewService
from custom_model.application.quote_view import QuoteViewService
from custom_model.application.quote_refresh_service import QuoteRefreshService
from custom_model.domain.models import DataQuery, QuoteQuery

from ..contracts import (
    MarketBarsView,
    QuoteRefreshRequest,
    QuoteRefreshResponse,
    QuoteViewResponse,
)
from ..dependencies import (
    get_market_bars_view_service,
    get_quote_refresh_service,
    get_quote_view_service,
)


router = APIRouter(prefix="/v1/market", tags=["market"])


@router.post(
    "/quote",
    operation_id="queryMarketQuote",
    response_model=QuoteViewResponse,
)
def query_market_quote(
    query: QuoteQuery,
    service: QuoteViewService = Depends(get_quote_view_service),
) -> QuoteViewResponse:
    result = service.query(query)
    return QuoteViewResponse.model_validate(result, from_attributes=True)


@router.post(
    "/quote/refresh",
    operation_id="refreshMarketQuote",
    response_model=QuoteRefreshResponse,
)
def refresh_market_quote(
    request: QuoteRefreshRequest,
    service: QuoteRefreshService = Depends(get_quote_refresh_service),
) -> QuoteRefreshResponse:
    result = service.refresh(request.instrument)
    return QuoteRefreshResponse(
        quote=result.quote,
        provider_unchanged=result.provider_unchanged,
        cooldown_seconds=result.cooldown_seconds,
    )


@router.post(
    "/bars/query",
    operation_id="queryMarketBars",
    response_model=MarketBarsView,
)
def query_market_bars(
    query: DataQuery,
    service: MarketBarsViewService = Depends(get_market_bars_view_service),
) -> MarketBarsView:
    result = service.query(query)
    return MarketBarsView.model_validate(result, from_attributes=True)
