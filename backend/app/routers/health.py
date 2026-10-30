from fastapi import APIRouter

from app.database import check_supabase_connection, is_supabase_configured

router = APIRouter()


@router.get("/health")
def health_check() -> dict:
    # Import lazily to avoid the existing service import cycle during module
    # collection (agent_orchestrator <-> employee_vertical_slice).
    from app.services.agent_orchestrator import AgentOrchestrator

    payload = dict(AgentOrchestrator().health_check())
    payload.setdefault("status", "healthy")
    payload["service"] = "aea-core-backend"

    configured = is_supabase_configured()
    connected = check_supabase_connection() if configured else False
    payload["supabase_configured"] = bool(configured)
    payload["supabase_connected"] = bool(connected)
    payload["supabase_query_ok"] = bool(connected)

    # Expose safe model diagnostics without ever returning credentials.
    from app.services.llm_provider import get_llm_provider
    provider = get_llm_provider()
    provider_name = provider.__class__.__name__.replace("LLMProvider", "").lower()
    payload["llm"] = {
        "provider": provider_name,
        "model": getattr(provider, "model", "mock" if provider_name == "mock" else "unknown"),
        "configured": provider_name == "mock" or bool(getattr(provider, "api_key", "")),
        "ready": provider_name == "mock" or (
            provider_name == "groq" and bool(getattr(provider, "api_key", ""))
        ),
        "is_mock": provider_name == "mock",
    }

    # Do not report healthy if the configured database query is failing.
    if not connected:
        payload["status"] = "degraded"

    return payload
