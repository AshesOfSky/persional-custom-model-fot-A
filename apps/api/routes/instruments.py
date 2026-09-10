from fastapi import APIRouter, Depends, Query

from custom_model.application.instruments import InstrumentSearchService

from ..contracts import InstrumentSearchItem, InstrumentSearchResponse
from ..dependencies import get_instrument_search_service


router = APIRouter(prefix="/v1/instruments", tags=["instruments"])


@router.get(
    "/search",
    operation_id="searchInstruments",
    response_model=InstrumentSearchResponse,
)
def search_instruments(
    q: str = Query(min_length=1, max_length=64),
    limit: int = Query(default=10, ge=1, le=20),
    service: InstrumentSearchService = Depends(get_instrument_search_service),
) -> InstrumentSearchResponse:
    query = q.strip()
    matches = service.search(query, limit=limit)
    return InstrumentSearchResponse(
        query=query,
        items=[
            InstrumentSearchItem(
                instrument=match.instrument,
                display_name=match.display_name,
                legacy_code=match.legacy_code,
                source=match.source,
                match_score=match.match_score,
            )
            for match in matches
        ],
    )
