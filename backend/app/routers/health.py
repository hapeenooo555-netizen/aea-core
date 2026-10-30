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

    # Report provider configuration without claiming that inference has been
    # tested. A credential being present is not proof that the upstream model
    # is reachable or that the selected model is valid.
    from app.services.llm_provider import get_llm_provider

    provider = get_llm_provider()
    provider_name = provider.__class__.__name__.replace("LLMProvider", "").lower()
    is_mock = provider_name == "mock"
    credential_present = bool(getattr(provider, "api_key", "")) if provider_name == "groq" else False
    payload["llm"] = {
        "provider": provider_name,
        "model": getattr(provider, "model", "mock" if is_mock else "unknown"),
        "configured": provider_name == "groq" and credential_present,
        "credential_present": credential_present,
        "ready": False,
        "readiness_check": "not_performed",
        "is_mock": is_mock,
    }

    # Mock is suitable for tests/local development only; it is not production
    # inference readiness. Keep API liveness separate from model readiness.
    if is_mock or (provider_name == "groq" and not credential_present) or not connected:
        payload["status"] = "degraded"

    return payload
