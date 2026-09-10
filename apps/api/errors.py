from __future__ import annotations

from fastapi import FastAPI, Request

from fastapi.exceptions import RequestValidationError

from fastapi.responses import JSONResponse

from custom_model.application.analysis_service import DecisionInputError

from custom_model.application.data_gateway import DataUnavailableError

from custom_model.application.fundamental_service import FundamentalDataUnavailableError, FundamentalNotApplicableError

from custom_model.application.technical_structure_service import TechnicalStructureBuildError

from custom_model.application.quote_view import QuoteUnavailableError

from custom_model.application.quote_refresh_service import QuoteRefreshCooldownError, QuoteRefreshInputError, QuoteRefreshRateLimitedError, QuoteRefreshUnavailableError

from custom_model.application.watchlist_service import WatchlistConflictError, WatchlistInputError, WatchlistNotFoundError, WatchlistStorageUnavailableError

from .contracts import ErrorDetail, ErrorResponse, ValidationIssue

from .dependencies import ApiConfigurationError

def _response(status_code: int, detail: ErrorDetail) -> JSONResponse:
    payload = ErrorResponse(error=detail).model_dump(mode="json")
    return JSONResponse(status_code=status_code, content=payload)


async def request_validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    issues = [
        ValidationIssue(
            location=list(item.get("loc", ())),
            message=str(item.get("msg", "invalid request")),
            issue_type=str(item.get("type", "validation_error")),
        )
        for item in exc.errors()
    ]
    return _response(
        422,
        ErrorDetail(
            code="request_validation_failed",
            message="request validation failed",
            validation=issues,
        ),
    )


async def decision_input_handler(_request: Request, exc: DecisionInputError) -> JSONResponse:
    return _response(
        422,
        ErrorDetail(code="analysis_input_rejected", message=str(exc)),
    )


async def watchlist_input_handler(
    _request: Request, _exc: WatchlistInputError
) -> JSONResponse:
    return _response(
        422,
        ErrorDetail(
            code="watchlist_input_rejected",
            message="watchlist input was rejected",
        ),
    )


async def watchlist_not_found_handler(
    _request: Request, _exc: WatchlistNotFoundError
) -> JSONResponse:
    return _response(
        404,
        ErrorDetail(
            code="watchlist_entry_not_found",
            message="watchlist entry was not found",
        ),
    )


async def watchlist_conflict_handler(
    _request: Request, _exc: WatchlistConflictError
) -> JSONResponse:
    return _response(
        409,
        ErrorDetail(
            code="watchlist_conflict",
            message="watchlist configuration conflicts with current state",
        ),
    )


async def watchlist_storage_unavailable_handler(
    _request: Request, _exc: WatchlistStorageUnavailableError
) -> JSONResponse:
    return _response(
        503,
        ErrorDetail(
            code="watchlist_storage_unavailable",
            message="watchlist storage is unavailable",
            retryable=True,
        ),
    )


async def data_unavailable_handler(
    _request: Request, exc: DataUnavailableError
) -> JSONResponse:
    return _response(
        503,
        ErrorDetail(code="data_unavailable", message=str(exc), retryable=True),
    )


async def configuration_handler(
    _request: Request, exc: ApiConfigurationError
) -> JSONResponse:
    return _response(
        503,
        ErrorDetail(code="service_not_configured", message=str(exc)),
    )


async def technical_structure_build_handler(
    _request: Request, _exc: TechnicalStructureBuildError
) -> JSONResponse:
    # Builder exceptions can contain filesystem paths, provider payloads or
    # implementation details.  Keep the public failure stable and opaque.
    return _response(
        503,
        ErrorDetail(
            code="technical_structure_unavailable",
            message="technical structure service is unavailable",
            retryable=True,
        ),
    )


async def fundamental_not_applicable_handler(
    _request: Request, _exc: FundamentalNotApplicableError
) -> JSONResponse:
    return _response(
        422,
        ErrorDetail(
            code="fundamentals_not_applicable",
            message="issuer fundamentals are not applicable to this instrument",
        ),
    )


async def fundamental_data_unavailable_handler(
    _request: Request, _exc: FundamentalDataUnavailableError
) -> JSONResponse:
    # Provider attempts may contain internal categories or local paths.  The
    # service records them for diagnostics, but the public API stays opaque.
    return _response(
        503,
        ErrorDetail(
            code="fundamental_data_unavailable",
            message="fundamental data is unavailable",
            retryable=True,
        ),
    )


async def quote_unavailable_handler(
    _request: Request, _exc: QuoteUnavailableError
) -> JSONResponse:
    return _response(
        503,
        ErrorDetail(
            code="quote_unavailable",
            message="native market quote is unavailable",
            retryable=True,
        ),
    )


async def quote_refresh_input_handler(
    _request: Request, exc: QuoteRefreshInputError
) -> JSONResponse:
    return _response(
        422,
        ErrorDetail(code="quote_refresh_input_rejected", message=str(exc)),
    )


async def quote_refresh_cooldown_handler(
    _request: Request, exc: QuoteRefreshCooldownError
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code="quote_refresh_cooldown",
            message="manual quote refresh is cooling down",
            retryable=True,
        )
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=429,
        content=payload,
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


async def quote_refresh_rate_limited_handler(
    _request: Request, exc: QuoteRefreshRateLimitedError
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code="quote_refresh_upstream_rate_limited",
            message="quote refresh is temporarily rate limited",
            retryable=True,
        )
    ).model_dump(mode="json")
    return JSONResponse(
        status_code=503,
        content=payload,
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


async def quote_refresh_unavailable_handler(
    _request: Request, _exc: QuoteRefreshUnavailableError
) -> JSONResponse:
    return _response(
        503,
        ErrorDetail(
            code="quote_refresh_unavailable",
            message="native quote refresh is unavailable",
            retryable=True,
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, request_validation_handler)
    app.add_exception_handler(DecisionInputError, decision_input_handler)
    app.add_exception_handler(WatchlistInputError, watchlist_input_handler)
    app.add_exception_handler(WatchlistNotFoundError, watchlist_not_found_handler)
    app.add_exception_handler(WatchlistConflictError, watchlist_conflict_handler)
    app.add_exception_handler(WatchlistStorageUnavailableError, watchlist_storage_unavailable_handler)
    app.add_exception_handler(DataUnavailableError, data_unavailable_handler)
    app.add_exception_handler(ApiConfigurationError, configuration_handler)
    app.add_exception_handler(TechnicalStructureBuildError, technical_structure_build_handler)
    app.add_exception_handler(FundamentalNotApplicableError, fundamental_not_applicable_handler)
    app.add_exception_handler(FundamentalDataUnavailableError, fundamental_data_unavailable_handler)
    app.add_exception_handler(QuoteUnavailableError, quote_unavailable_handler)
    app.add_exception_handler(QuoteRefreshInputError, quote_refresh_input_handler)
    app.add_exception_handler(QuoteRefreshCooldownError, quote_refresh_cooldown_handler)
    app.add_exception_handler(QuoteRefreshRateLimitedError, quote_refresh_rate_limited_handler)
    app.add_exception_handler(QuoteRefreshUnavailableError, quote_refresh_unavailable_handler)


