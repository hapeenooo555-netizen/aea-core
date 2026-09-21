"""Main application module for AEA Core (BASELINE).

This baseline version mounts the routers that exist at baseline time
(atlas, connectors, health, missions, workers). The approvals router is
intentionally not imported here; it is added by P1-1.
"""

from __future__ import annotations

from fastapi import FastAPI

from app import database
from app.routers import affiliate_jobs, approvals, atlas, connectors, employee, health, missions, workers

# Import supabase_client for test compatibility
supabase_client = database.supabase_client

# Initialize FastAPI application
app = FastAPI(
    title="AEA Core",
    description="Autonomous Employee Agent Core Platform",
    version="1.0.0",
)


# Root endpoint
@app.get("/")
def root() -> dict[str, str]:
    """Return a simple health check message."""
    return {"message": "AEA Core API is running"}


# Register routers
app.include_router(health.router, tags=["health"])
app.include_router(atlas.router, tags=["atlas"])
app.include_router(missions.router, tags=["missions"])
app.include_router(workers.router, tags=["workers"])
app.include_router(connectors.router, tags=["connectors"])
app.include_router(approvals.router, tags=["approvals"])
app.include_router(employee.router, tags=["employee"])
app.include_router(affiliate_jobs.router, tags=["affiliate"])
