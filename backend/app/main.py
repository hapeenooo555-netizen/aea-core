"""Main application module for AEA Core (BASELINE).

This baseline version mounts the routers that exist at baseline time
(atlas, connectors, health, missions, workers). The approvals router is
intentionally not imported here; it is added by P1-1.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import database
from app.routers import affiliate_jobs, approvals, atlas, chatplus, connectors, employee, hape_marketing, meta_webhooks, health, missions, workers

# Import supabase_client for test compatibility
supabase_client = database.supabase_client

# Initialize FastAPI application
app = FastAPI(
    title="AEA Core",
    description="Autonomous Employee Agent Core Platform",
    version="1.0.0",
)


# Allow the published AEA Affiliate Hub to call the API from a browser.
# Operators can override/extend this list through AEA_CORS_ORIGINS.
_default_cors_origins = [
    "https://aea-affiliate-hub.lovable.app",
    "https://id-preview--15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovable.app",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
_cors_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv("AEA_CORS_ORIGINS", ",".join(_default_cors_origins)).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "apikey", "x-client-info"],
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
app.include_router(connectors.oauth_router, tags=["connectors"])
app.include_router(approvals.router, tags=["approvals"])
app.include_router(employee.router, tags=["employee"])
app.include_router(affiliate_jobs.router, tags=["affiliate"])
app.include_router(chatplus.router, tags=["chatplus"])
app.include_router(hape_marketing.router, tags=["hape-brothers-marketing"])
app.include_router(meta_webhooks.router, tags=["meta-webhooks"])
