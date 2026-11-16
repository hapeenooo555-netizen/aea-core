"""Owner-scoped Phase 1 APIs for Freelancer clients, services and dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/freelancer", tags=["freelancer-foundation"])


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    timezone: str = Field(default="Africa/Dar_es_Salaam", max_length=80)
    notes: str | None = Field(default=None, max_length=5000)
    model_config = ConfigDict(extra="forbid")


class ClientUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    timezone: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=5000)
    model_config = ConfigDict(extra="forbid")


class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    pricing_model: str = Field(default="custom", pattern="^(fixed|hourly|custom)$")
    starting_price: float | None = Field(default=None, ge=0)
    currency: str = Field(default="TZS", pattern="^[A-Za-z]{3}$")
    is_active: bool = True
    model_config = ConfigDict(extra="forbid")


class ServiceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    category: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=5000)
    pricing_model: str | None = Field(default=None, pattern="^(fixed|hourly|custom)$")
    starting_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, pattern="^[A-Za-z]{3}$")
    is_active: bool | None = None
    model_config = ConfigDict(extra="forbid")


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


def _db_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail="Freelancer data service is unavailable; confirm the Phase 1 Supabase migration is applied.")


def _list_owned(client: Any, table: str, owner_id: str, active_only: bool = False) -> list[dict[str, Any]]:
    query = client.table(table).select("*").eq("owner_id", owner_id)
    if active_only and table == "freelancer_services":
        query = query.eq("is_active", True)
    return _rows(query.order("updated_at", desc=True).execute())


def _create_owned(client: Any, table: str, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    row = dict(payload)
    row["owner_id"] = owner_id
    if "currency" in row and isinstance(row["currency"], str):
        row["currency"] = row["currency"].upper()
    result = client.table(table).insert(row).execute()
    rows = _rows(result)
    if not rows:
        raise HTTPException(status_code=503, detail="Database did not confirm the record creation.")
    return rows[0]


def _update_owned(client: Any, table: str, record_id: str, owner_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    changes = {key: value for key, value in payload.items() if value is not None}
    if "currency" in changes and isinstance(changes["currency"], str):
        changes["currency"] = changes["currency"].upper()
    if not changes:
        raise HTTPException(status_code=400, detail="At least one field must be supplied.")
    rows = _rows(client.table(table).update(changes).eq("id", record_id).eq("owner_id", owner_id).execute())
    if not rows:
        raise HTTPException(status_code=404, detail="Record not found.")
    return rows[0]


@router.get("/clients")
def list_clients(owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "clients": _list_owned(client, "freelancer_clients", owner_id)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.post("/clients", status_code=status.HTTP_201_CREATED)
def create_client(body: ClientCreate, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "client": _create_owned(client, "freelancer_clients", owner_id, body.model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.patch("/clients/{client_id}")
def update_client(client_id: str, body: ClientUpdate, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "client": _update_owned(client, "freelancer_clients", client_id, owner_id, body.model_dump(exclude_unset=True))}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.get("/services")
def list_services(active_only: bool = Query(default=False), owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "services": _list_owned(client, "freelancer_services", owner_id, active_only)}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.post("/services", status_code=status.HTTP_201_CREATED)
def create_service(body: ServiceCreate, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "service": _create_owned(client, "freelancer_services", owner_id, body.model_dump())}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.patch("/services/{service_id}")
def update_service(service_id: str, body: ServiceUpdate, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        return {"success": True, "service": _update_owned(client, "freelancer_services", service_id, owner_id, body.model_dump(exclude_unset=True))}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_error(exc)


@router.get("/dashboard/summary")
def freelancer_dashboard_summary(owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    try:
        projects = _rows(client.table("freelancer_projects").select("id,status,agreed_amount,amount_received,currency").eq("owner_id", owner_id).execute())
        clients = _rows(client.table("freelancer_clients").select("id").eq("owner_id", owner_id).execute())
        services = _rows(client.table("freelancer_services").select("id").eq("owner_id", owner_id).eq("is_active", True).execute())
        counts: dict[str, int] = {}
        received_by_currency: dict[str, float] = {}
        agreed_by_currency: dict[str, float] = {}
        for project in projects:
            state = str(project.get("status") or "unknown")
            counts[state] = counts.get(state, 0) + 1
            currency = str(project.get("currency") or "USD")
            received_by_currency[currency] = received_by_currency.get(currency, 0.0) + float(project.get("amount_received") or 0)
            if project.get("agreed_amount") is not None:
                agreed_by_currency[currency] = agreed_by_currency.get(currency, 0.0) + float(project["agreed_amount"])
        return {"success": True, "summary": {
            "clients_count": len(clients), "active_services_count": len(services),
            "projects_count": len(projects), "projects_by_status": counts,
            "agreed_amount_by_currency": agreed_by_currency,
            "received_amount_by_currency": received_by_currency,
        }}
    except Exception as exc:
        raise _db_error(exc)
