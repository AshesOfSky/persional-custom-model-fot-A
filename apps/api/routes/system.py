from datetime import datetime, timezone

from fastapi import APIRouter, Request

from ..contracts import HealthResponse, ProviderHealthInfo, WorkerHealthInfo

router = APIRouter(prefix="/v1/system", tags=["system"])


def _collect_provider_health(request: Request) -> list[ProviderHealthInfo]:
    """Snapshot each wired provider's health without ever failing the endpoint."""

    infos: list[ProviderHealthInfo] = []
    for provider in getattr(request.app.state, "providers", ()) or ():
        name = getattr(provider, "name", "unknown")
        production_ready = bool(getattr(provider, "production_ready", False))
        try:
            health = provider.health()
        except Exception as exc:  # health reporting must stay available
            infos.append(
                ProviderHealthInfo(
                    provider=name,
                    healthy=False,
                    production_ready=production_ready,
                    checked_at=datetime.now(timezone.utc),
                    message=type(exc).__name__,
                )
            )
            continue
        infos.append(
            ProviderHealthInfo(
                provider=health.provider,
                healthy=health.healthy,
                production_ready=health.production_ready,
                checked_at=health.checked_at,
                message=health.message,
            )
        )
    return infos


@router.get(
    "/health",
    operation_id="getSystemHealth",
    response_model=HealthResponse,
)
def get_health(request: Request) -> HealthResponse:
    state = request.app.state
    composition_error = getattr(state, "composition_error", None)
    analysis_ready = (
        getattr(state, "research_use_case", None) is not None
        and composition_error is None
    )
    providers = _collect_provider_health(request)
    providers_healthy = all(info.healthy for info in providers)
    probe = getattr(state, "worker_probe", None)
    worker = (
        probe()
        if callable(probe)
        else WorkerHealthInfo(status="offline", detail="worker health probe not wired")
    )
    ready = analysis_ready and providers_healthy
    return HealthResponse(
        status="ok" if ready else "degraded",
        analysis_ready=analysis_ready,
        providers=providers,
        worker=worker,
        composition_error=composition_error,
    )


