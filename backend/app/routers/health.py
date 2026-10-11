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

    # Do not report healthy if the configured database query is failing.
    if not connected:
        payload["status"] = "degraded"

    return payload
