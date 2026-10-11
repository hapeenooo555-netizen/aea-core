from fastapi import APIRouter

from app.database import check_supabase_connection, is_supabase_configured
from app.services.agent_orchestrator import AgentOrchestrator

router = APIRouter()
orchestrator = AgentOrchestrator()


@router.get("/health")
def health_check() -> dict:
    payload = dict(orchestrator.health_check())
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
