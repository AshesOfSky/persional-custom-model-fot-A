from fastapi import APIRouter, Depends

from custom_model.application.research import ResearchAnalysisUseCase
from custom_model.application.research_view import ResearchViewService
from custom_model.domain.models import AnalysisSnapshot

from ..contracts import (
    AnalysisRequest,
    ErrorResponse,
    RealtimeAnalysisPreviewRequest,
    RealtimeAnalysisPreviewResponse,
    ResearchAnalysisResponse,
    TechnicalStructureRequest,
    TechnicalStructureResponse,
)
from ..dependencies import (
    get_research_use_case,
    get_research_view_service,
    get_realtime_analysis_preview_service,
    get_technical_structure_service,
)
from ..technical_structures import TechnicalStructureEndpointService
from ..realtime_analysis import RealtimeAnalysisEndpointService


router = APIRouter(prefix="/v1/analysis", tags=["analysis"])


@router.post(
    "/snapshots",
    operation_id="createAnalysisSnapshot",
    response_model=AnalysisSnapshot,
    responses={
        422: {"model": ErrorResponse, "description": "Request or analysis input rejected"},
        503: {"model": ErrorResponse, "description": "Data or service unavailable"},
    },
)
def create_analysis_snapshot(
    payload: AnalysisRequest,
    use_case: ResearchAnalysisUseCase = Depends(get_research_use_case),
) -> AnalysisSnapshot:
    result = use_case.analyze(
        payload.query,
        info={"name": payload.display_name} if payload.display_name else None,
        benchmark_query=payload.benchmark_query,
        has_position=payload.has_position,
    )
    return result.snapshot


@router.post(
    "/research",
    operation_id="createResearchAnalysis",
    response_model=ResearchAnalysisResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Request or analysis input rejected"},
        503: {"model": ErrorResponse, "description": "Data or service unavailable"},
    },
)
def create_research_analysis(
    payload: AnalysisRequest,
    service: ResearchViewService = Depends(get_research_view_service),
) -> ResearchAnalysisResponse:
    result = service.analyze(
        payload.query,
        info={"name": payload.display_name} if payload.display_name else None,
        benchmark_query=payload.benchmark_query,
        has_position=payload.has_position,
        provisional_quote=payload.provisional_quote,
    )
    return ResearchAnalysisResponse.model_validate(result, from_attributes=True)


@router.post(
    "/realtime-preview",
    operation_id="createRealtimeAnalysisPreview",
    response_model=RealtimeAnalysisPreviewResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Preview input rejected"},
        503: {"model": ErrorResponse, "description": "Preview data unavailable"},
    },
)
def create_realtime_analysis_preview(
    payload: RealtimeAnalysisPreviewRequest,
    service: RealtimeAnalysisEndpointService = Depends(
        get_realtime_analysis_preview_service
    ),
) -> RealtimeAnalysisPreviewResponse:
    result = service.analyze(
        payload.query,
        payload.provisional_quote,
        info={"name": payload.display_name} if payload.display_name else None,
        has_position=payload.has_position,
    )
    computation = result.computation
    completed = computation.completed
    if completed.data.last_bar_at is None:
        raise ValueError("completed daily baseline has no last bar")
    preview = ResearchAnalysisResponse.model_validate(
        result.preview_view,
        from_attributes=True,
    )
    structures = result.structures.view
    return RealtimeAnalysisPreviewResponse.model_validate(
        {
            "schema_version": computation.schema_version,
            "advisory_only": computation.advisory_only,
            "formal_use_eligible": computation.formal_use_eligible,
            "session_label": computation.session_label,
            "quote": computation.quote,
            "completed_baseline": {
                "request_id": completed.data.request_id,
                "snapshot_id": completed.snapshot.snapshot_id,
                "instrument": completed.data.instrument,
                "timeframe": completed.data.timeframe,
                "as_of": completed.data.as_of,
                "completed_through": completed.data.last_bar_at,
                "evidence_score": completed.snapshot.evidence_score,
                "score_semantics": "evidence_score_not_probability",
                "provider": completed.data.provider,
                "mode": completed.data.mode,
                "quality": completed.data.quality,
                "adjustment": completed.data.adjustment,
            },
            "preview": preview,
            "structure_data_ref": structures.data_ref,
            "structure_data_fingerprint": result.structures.data_fingerprint,
            "structure_engine": structures.engine,
            "structures": structures.sections,
            "structure_status": structures.section_status,
            "warnings": list(
                dict.fromkeys(
                    [
                        *preview.warnings,
                        *structures.warnings,
                        "盘中预估使用未收盘日K，结果会随行情变化",
                    ]
                )
            ),
        },
        from_attributes=True,
    )


@router.post(
    "/structures",
    operation_id="createTechnicalStructureAnalysis",
    response_model=TechnicalStructureResponse,
    responses={
        422: {"model": ErrorResponse, "description": "Request or analysis input rejected"},
        503: {"model": ErrorResponse, "description": "Structure service unavailable"},
    },
)
def create_technical_structure_analysis(
    payload: TechnicalStructureRequest,
    service: TechnicalStructureEndpointService = Depends(
        get_technical_structure_service
    ),
) -> TechnicalStructureResponse:
    result = service.analyze(
        payload.query,
        info={"name": payload.display_name} if payload.display_name else None,
        benchmark_query=payload.benchmark_query,
        has_position=payload.has_position,
        prior_state=payload.prior_state,
    )
    view = result.view
    return TechnicalStructureResponse.model_validate(
        {
            "schema_version": view.schema_version,
            "data_fingerprint": result.data_fingerprint,
            "data_ref": view.data_ref,
            "snapshot": view.snapshot,
            "engine": view.engine,
            "engine_note": view.engine_note,
            "sections": view.sections,
            "section_status": view.section_status,
            "current_state": result.current_state,
            "continuity": result.continuity,
            "warnings": view.warnings,
        },
        from_attributes=True,
    )
