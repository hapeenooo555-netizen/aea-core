"""FastAPI authentication and authorization dependencies for P1-4C/P1-4D.

Provides:

    get_current_user
        Verifies the Supabase access token from the Authorization header
        and returns the normalized authenticated user dictionary.

    get_current_user_id
        Convenience dependency that extracts the canonical user id
        (auth.users.id) from the verified token.

    get_user_scoped_client
        Returns a Supabase client whose queries carry the caller's JWT
        so that PostgreSQL RLS can evaluate auth.uid() against the
        authenticated user identity.

    verify_ownership
        Dependency for verifying resource ownership. Raises 404 for
        non-owned resources to prevent information disclosure.

    verify_mission_ownership
        Verifies that a mission belongs to the current user.

    verify_worker_ownership
        Verifies that a worker belongs to the current user.

    verify_approval_ownership
        Verifies that an approval request belongs to the current user.

The unauthenticated (anon) global client in ``app.database`` is retained
for public endpoints and for token verification itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from . import database as database_module

logger = logging.getLogger(__name__)

_UNAUTHENTICATED_ERROR = "Missing or invalid Authorization header"
_INVALID_TOKEN_ERROR = "Invalid or expired access token"
_NOT_FOUND_ERROR = "Resource not found"


def _extract_bearer_token(request: Request) -> str:
    """Extract a Bearer token from the Authorization header.

    Raises:
        HTTPException: 401 if the header is missing or malformed.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _verify_token_with_supabase(token: str) -> dict[str, Any]:
    """Verify a Supabase access token and return the user payload.

    Uses the anon-key global client so that verification does not depend
    on the caller's token scope. The returned dict always contains at
    least ``id`` (the canonical ``auth.users.id`` UUID string).

    Raises:
        HTTPException: 401 if verification fails for any reason.
    """
    client = database_module.supabase_client
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase client is not configured",
        )

    try:
        user_response = client.auth.get_user(token)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Supabase auth.get_user failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    supabase_user = getattr(user_response, "user", None)
    if supabase_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = getattr(supabase_user, "id", None)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_TOKEN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": user_id,
        "email": getattr(supabase_user, "email", None),
        "role": getattr(supabase_user, "role", "authenticated"),
    }


def get_current_user(request: Request) -> dict[str, Any]:
    """FastAPI dependency that returns the authenticated Supabase user.

    The caller must supply::

        Authorization: Bearer <supabase-access-token>

    Returns a dict with at least::

        {"id": <auth.users.id UUID string>, "email": ..., "role": ...}

    Raises:
        HTTPException: 401 if the token is missing, malformed, or invalid.
    """
    token = _extract_bearer_token(request)
    return _verify_token_with_supabase(token)


def get_current_user_id(current_user: dict[str, Any] = Depends(get_current_user)) -> str:
    """FastAPI dependency that returns the canonical user id (auth.users.id)."""
    return current_user["id"]


def get_user_scoped_client(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> Any:
    """Return a Supabase client whose queries carry the caller's JWT.

    The returned client is constructed with the Supabase anon key (required by
    PostgREST for API-key validation) and then the caller's access token is
    attached via ``auth.set_session``. PostgreSQL RLS therefore sees
    ``auth.uid()`` equal to ``current_user["id"]`` for every query made with
    this client.

    The caller's raw token is never logged, returned in responses, or
    persisted. It is used only to construct the client and is then
    discarded.
    """
    token = _extract_bearer_token(request)
    url = database_module.get_supabase_url()
    anon_key = database_module.get_supabase_anon_key()
    if not (url and anon_key):
        return None
    try:
        from supabase import create_client

        client = create_client(url, anon_key)
        client.auth.set_session(
            access_token=token,
            refresh_token="",
        )
        return client
    except Exception:  # pragma: no cover - defensive
        return None


def verify_ownership(
    current_user_id: str = Depends(get_current_user_id),
    resource_owner_id: str | None = None,
) -> str:
    """Dependency that verifies resource ownership.

    Raises HTTP 404 if the resource does not belong to the current user.
    Uses 404 (not 403) to prevent information disclosure about whether
    another user's resource exists.

    Returns current_user_id when verification passes.
    """

    if resource_owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_ERROR,
        )

    if resource_owner_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_NOT_FOUND_ERROR,
        )

    return current_user_id


def verify_mission_ownership(
    mission_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Verify that a mission belongs to the current user.

    Uses mission_engine to fetch the mission and check ownership.
    Raises 404 if mission not found or not owned by current user.
    """

    from app.services.mission_engine import MissionEngine

    mission_engine = MissionEngine(client=client)
    mission = mission_engine.get_mission(mission_id, owner_id=current_user_id, client=client)
    if mission is None or mission.get("owner_id") != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mission not found",
        )
    return mission


def verify_worker_ownership(
    worker_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Verify that a worker belongs to the current user.

    Raises 404 if worker not found or not owned by current user.
    """

    from app.services.mission_engine import MissionEngine

    mission_engine = MissionEngine(client=client)
    # List workers owned by current user and check if worker_id is among them
    missions = mission_engine.get_worker_missions(current_user_id, limit=100, client=client)
    # This is a heuristic - also check if any mission assigned to this worker belongs to user
    user_missions = [m for m in missions if m.get("owner_id") == current_user_id]
    user_worker_missions = [m for m in user_missions if m.get("assigned_worker") == worker_id]

    if not user_worker_missions:
        # Also try direct lookup - worker might exist but have no missions
        # In real system, workers table should also have owner_id
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Worker not found",
        )

    return {"worker_id": worker_id, "owner_id": current_user_id}


def verify_approval_ownership(
    approval_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Verify that an approval request belongs to the current user.

    Raises 404 if approval not found or not owned by current user.
    """

    from app.services.approval_gateway import ApprovalGateway

    approval_gateway = ApprovalGateway(client=client)
    approval = approval_gateway.get_request(approval_id, client=client)
    if approval is None or approval.get("owner_id") != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Approval request not found",
        )
    return {"approval_id": approval_id, "owner_id": current_user_id}
