from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from fastapi import APIRouter, Depends

from custom_model.application.fundamental_service import FundamentalService
from custom_model.domain.models import FundamentalField, FundamentalSnapshot

from ..contracts import (
    ErrorResponse,
    FundamentalCoverageView,
    FundamentalEnvelopeView,
    FundamentalFieldView,
    FundamentalRawEntryView,
    FundamentalRawValueView,
    FundamentalSnapshotRequest,
    FundamentalSnapshotResponse,
)
from ..dependencies import get_fundamental_service


router = APIRouter(prefix="/v1/fundamentals", tags=["fundamentals"])


def _raw_evidence_view(value: Any) -> FundamentalRawValueView:
    if isinstance(value, bool):
        return FundamentalRawValueView(kind="boolean", boolean_value=value)
    if isinstance(value, int):
        return FundamentalRawValueView(kind="integer", integer_value=value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("fundamental raw evidence must be finite")
        return FundamentalRawValueView(kind="number", number_value=value)
    if isinstance(value, str):
        return FundamentalRawValueView(kind="string", string_value=value)
    if isinstance(value, Mapping):
        return FundamentalRawValueView(
            kind="object",
            entries=[
                FundamentalRawEntryView(
                    key=key,
                    value=_raw_evidence_view(child),
                )
                for key, child in value.items()
            ],
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return FundamentalRawValueView(
            kind="array",
            items=[_raw_evidence_view(child) for child in value],
        )
    raise ValueError("unsupported fundamental raw evidence type")


def _field_view(field: FundamentalField) -> FundamentalFieldView:
    return FundamentalFieldView(
        name=field.name,
        source_field=field.source_field,
        raw_value=_raw_evidence_view(field.raw_value),
        normalized_value=field.normalized_value,
        unit=field.unit,
        basis=field.basis,
        status=field.status,
        period_end=field.period_end,
        published_at=field.published_at,
        market_time=field.market_time,
    )


def _snapshot_response(snapshot: FundamentalSnapshot) -> FundamentalSnapshotResponse:
    data = snapshot.data
    coverage = snapshot.coverage
    return FundamentalSnapshotResponse(
        snapshot_id=snapshot.snapshot_id,
        instrument=snapshot.instrument,
        purpose=snapshot.purpose,
        as_of=snapshot.as_of,
        engine_version=snapshot.engine_version,
        data=FundamentalEnvelopeView(
            instrument=data.instrument,
            purpose=data.purpose,
            provider=data.provider,
            mode=data.mode,
            quality=data.quality,
            as_of=data.as_of,
            fetched_at=data.fetched_at,
            data_time=data.data_time,
            data_time_basis=data.data_time_basis,
            timezone=data.timezone,
            currency=data.currency,
            freshness_seconds=data.freshness_seconds,
            fields=[_field_view(field) for field in data.fields],
            warnings=data.warnings,
            is_synthetic=data.is_synthetic,
            source_chain=data.source_chain,
            request_id=data.request_id,
        ),
        coverage=FundamentalCoverageView(
            required_fields=coverage.required_fields,
            verified_financial_fields=coverage.verified_financial_fields,
            missing_required_fields=coverage.missing_required_fields,
            minimum_verified_financial_fields=(
                coverage.minimum_verified_financial_fields
            ),
            passed=coverage.passed,
        ),
        use_case=snapshot.use_case,
        formal_use_eligible=snapshot.formal_use_eligible,
    )


@router.post(
    "/snapshots",
    operation_id="createFundamentalSnapshot",
    response_model=FundamentalSnapshotResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Fundamentals not applicable"},
        503: {"model": ErrorResponse, "description": "Fundamental data unavailable"},
    },
)
def create_fundamental_snapshot(
    payload: FundamentalSnapshotRequest,
    service: FundamentalService = Depends(get_fundamental_service),
) -> FundamentalSnapshotResponse:
    return _snapshot_response(service.analyze(payload.query))
