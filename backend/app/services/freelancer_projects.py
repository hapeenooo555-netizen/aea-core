"""Owner-scoped persistence for Freelancer Employee project tracking."""
from __future__ import annotations

from typing import Any

TABLE = "freelancer_projects"
PROJECT_STATUSES = {
    "lead", "proposal_draft", "proposal_sent", "in_progress",
    "submitted", "revision", "completed", "cancelled",
}


class FreelancerProjectError(ValueError):
    """A project request failed validation or persistence."""


def _data(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


def create_project(client: Any, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if client is None:
        raise FreelancerProjectError("Project database is not configured")
    if not owner_id:
        raise FreelancerProjectError("owner_id is required")
    title = str(payload.get("title") or "").strip()
    description = str(payload.get("job_description") or "").strip()
    if not title or len(title) > 200:
        raise FreelancerProjectError("title is required and must be at most 200 characters")
    if not description or len(description) > 30000:
        raise FreelancerProjectError("job_description is required and must be at most 30000 characters")
    status = payload.get("status", "lead")
    if status not in PROJECT_STATUSES:
        raise FreelancerProjectError("Invalid project status")
    agreed = payload.get("agreed_amount")
    received = payload.get("amount_received", 0)
    if agreed is not None and float(agreed) < 0:
        raise FreelancerProjectError("agreed_amount cannot be negative")
    if received is None or float(received) < 0:
        raise FreelancerProjectError("amount_received cannot be negative")
    if agreed is not None and float(received) > float(agreed):
        raise FreelancerProjectError("amount_received cannot exceed agreed_amount")
    currency = str(payload.get("currency") or "USD").upper()
    if len(currency) != 3 or not currency.isalpha():
        raise FreelancerProjectError("currency must be a three-letter code")
    row = {
        "owner_id": owner_id,
        "title": title,
        "job_description": description,
        "source_url": payload.get("source_url"),
        "status": status,
        "proposal_text": payload.get("proposal_text"),
        "agreed_amount": agreed,
        "currency": currency,
        "amount_received": received,
        "notes": payload.get("notes"),
    }
    try:
        result = client.table(TABLE).insert(row).execute()
    except Exception as exc:
        raise FreelancerProjectError("Project could not be saved; verify the Supabase migration is applied") from exc
    rows = _data(result)
    if not rows:
        raise FreelancerProjectError("Project save was not confirmed by the database")
    return rows[0]


def list_projects(client: Any, *, status: str | None = None) -> list[dict[str, Any]]:
    if client is None:
        raise FreelancerProjectError("Project database is not configured")
    query = client.table(TABLE).select("*")
    if status:
        if status not in PROJECT_STATUSES:
            raise FreelancerProjectError("Invalid project status")
        query = query.eq("status", status)
    try:
        result = query.order("updated_at", desc=True).execute()
    except Exception as exc:
        raise FreelancerProjectError("Projects could not be loaded; verify the Supabase migration is applied") from exc
    return _data(result)


def get_project(client: Any, project_id: str) -> dict[str, Any] | None:
    if client is None:
        raise FreelancerProjectError("Project database is not configured")
    try:
        rows = _data(client.table(TABLE).select("*").eq("id", project_id).limit(1).execute())
    except Exception as exc:
        raise FreelancerProjectError("Project could not be loaded") from exc
    return rows[0] if rows else None


def update_project(client: Any, project_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
    if client is None:
        raise FreelancerProjectError("Project database is not configured")
    allowed = {"title", "job_description", "source_url", "status", "proposal_text", "agreed_amount", "currency", "amount_received", "notes"}
    if not changes or set(changes) - allowed:
        raise FreelancerProjectError("No valid project fields supplied")
    if "status" in changes and changes["status"] not in PROJECT_STATUSES:
        raise FreelancerProjectError("Invalid project status")
    if "title" in changes and (not str(changes["title"]).strip() or len(str(changes["title"])) > 200):
        raise FreelancerProjectError("title is required and must be at most 200 characters")
    if "job_description" in changes and (not str(changes["job_description"]).strip() or len(str(changes["job_description"])) > 30000):
        raise FreelancerProjectError("job_description is required and must be at most 30000 characters")
    if "agreed_amount" in changes and changes["agreed_amount"] is not None and float(changes["agreed_amount"]) < 0:
        raise FreelancerProjectError("agreed_amount cannot be negative")
    if "amount_received" in changes and (changes["amount_received"] is None or float(changes["amount_received"]) < 0):
        raise FreelancerProjectError("amount_received cannot be negative")
    if "currency" in changes:
        currency = str(changes["currency"]).upper()
        if len(currency) != 3 or not currency.isalpha():
            raise FreelancerProjectError("currency must be a three-letter code")
        changes = {**changes, "currency": currency}
    try:
        result = client.table(TABLE).update(changes).eq("id", project_id).execute()
    except Exception as exc:
        raise FreelancerProjectError("Project update failed") from exc
    rows = _data(result)
    return rows[0] if rows else None
