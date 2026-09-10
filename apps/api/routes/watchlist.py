from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query

from custom_model.application.watchlist_service import (
    CreateWatchlistEntrySpec,
    PatchWatchlistEntrySpec,
    ReorderWatchlistEntriesSpec,
    WatchlistService,
)
from custom_model.domain.watchlist import WatchlistEntryRecord

from ..contracts import (
    ErrorResponse,
    WatchlistEntriesReorderRequest,
    WatchlistEntryCreateRequest,
    WatchlistEntryListResponse,
    WatchlistEntryPatchRequest,
    WatchlistEntryResponse,
    WatchlistEntryView,
)
from ..dependencies import get_watchlist_service


router = APIRouter(prefix="/v1/watchlist", tags=["watchlist"])

_ERROR_RESPONSES = {
    404: {"model": ErrorResponse, "description": "Watchlist entry not found"},
    409: {"model": ErrorResponse, "description": "Watchlist conflict"},
    422: {"model": ErrorResponse, "description": "Watchlist input rejected"},
    503: {"model": ErrorResponse, "description": "Watchlist service unavailable"},
}


def _view(record: WatchlistEntryRecord) -> WatchlistEntryView:
    entry = record.entry
    return WatchlistEntryView(
        entry_id=entry.entry_id,
        instrument=entry.instrument,
        role=entry.role,
        display_name=entry.display_name,
        note=entry.note,
        pinned=entry.pinned,
        sort_order=entry.sort_order,
        created_at=record.created_at,
        updated_at=record.updated_at,
        revision=record.revision,
        deleted_at=record.deleted_at,
    )


def _response(
    record: WatchlistEntryRecord,
    action: Literal["created", "read", "updated", "deleted"],
) -> WatchlistEntryResponse:
    return WatchlistEntryResponse(action=action, entry=_view(record))


@router.post(
    "/entries",
    operation_id="createWatchlistEntry",
    status_code=201,
    response_model=WatchlistEntryResponse,
    responses=_ERROR_RESPONSES,
)
def create_watchlist_entry(
    payload: WatchlistEntryCreateRequest,
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryResponse:
    record = service.create(CreateWatchlistEntrySpec(**payload.model_dump()))
    return _response(record, "created")


@router.get(
    "/entries",
    operation_id="listWatchlistEntries",
    response_model=WatchlistEntryListResponse,
    responses={503: _ERROR_RESPONSES[503]},
)
def list_watchlist_entries(
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryListResponse:
    records = service.list()
    return WatchlistEntryListResponse(
        total=len(records),
        items=[_view(record) for record in records],
    )


@router.put(
    "/entries/order",
    operation_id="reorderWatchlistEntries",
    response_model=WatchlistEntryListResponse,
    responses=_ERROR_RESPONSES,
)
def reorder_watchlist_entries(
    payload: WatchlistEntriesReorderRequest,
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryListResponse:
    service.reorder(ReorderWatchlistEntriesSpec(**payload.model_dump()))
    records = service.list()
    return WatchlistEntryListResponse(
        total=len(records),
        items=[_view(record) for record in records],
    )


@router.get(
    "/entries/{entry_id}",
    operation_id="getWatchlistEntry",
    response_model=WatchlistEntryResponse,
    responses=_ERROR_RESPONSES,
)
def get_watchlist_entry(
    entry_id: Annotated[str, Path(min_length=1, max_length=64)],
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryResponse:
    return _response(service.get(entry_id), "read")


@router.patch(
    "/entries/{entry_id}",
    operation_id="patchWatchlistEntry",
    response_model=WatchlistEntryResponse,
    responses=_ERROR_RESPONSES,
)
def patch_watchlist_entry(
    entry_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: WatchlistEntryPatchRequest,
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryResponse:
    spec = PatchWatchlistEntrySpec(**payload.model_dump(exclude_unset=True))
    return _response(service.patch(entry_id, spec), "updated")


@router.delete(
    "/entries/{entry_id}",
    operation_id="deleteWatchlistEntry",
    response_model=WatchlistEntryResponse,
    responses=_ERROR_RESPONSES,
)
def delete_watchlist_entry(
    entry_id: Annotated[str, Path(min_length=1, max_length=64)],
    expected_revision: Annotated[int, Query(ge=1)],
    service: WatchlistService = Depends(get_watchlist_service),
) -> WatchlistEntryResponse:
    return _response(
        service.delete(entry_id, expected_revision=expected_revision),
        "deleted",
    )
