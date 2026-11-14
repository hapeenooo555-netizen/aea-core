"""Server-only, per-user Meta credential vault and OAuth state primitives."""
from __future__ import annotations
import hashlib, json, os, secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from cryptography.fernet import Fernet, InvalidToken

PURPOSES = {"facebook_login", "whatsapp_embedded_signup"}

class MetaCredentialError(RuntimeError):
    """Safe Meta credential storage error."""

def _purpose(purpose: str) -> str:
    if purpose not in PURPOSES:
        raise MetaCredentialError("Meta credential purpose is unsupported.")
    return purpose

def _fernet() -> Fernet:
    key=os.getenv("META_CREDENTIAL_ENCRYPTION_KEY","").strip()
    if not key: raise MetaCredentialError("Meta credential encryption is not configured.")
    try: return Fernet(key.encode("ascii"))
    except (ValueError,UnicodeEncodeError) as exc: raise MetaCredentialError("Meta credential encryption key is invalid.") from exc

def encrypt_access_token(access_token: str) -> str:
    if not isinstance(access_token,str) or not access_token.strip(): raise MetaCredentialError("Meta returned an empty access token.")
    return _fernet().encrypt(access_token.encode()).decode("ascii")

def decrypt_access_token(ciphertext: str) -> str:
    if not isinstance(ciphertext,str) or not ciphertext: raise MetaCredentialError("Stored Meta credential is unavailable.")
    try: return _fernet().decrypt(ciphertext.encode("ascii")).decode()
    except (InvalidToken,ValueError,UnicodeEncodeError) as exc: raise MetaCredentialError("Stored Meta credential cannot be decrypted.") from exc

def new_oauth_state() -> str: return secrets.token_urlsafe(32)

def hash_oauth_state(state: str) -> str:
    if not isinstance(state,str) or len(state)<32 or len(state)>256: raise MetaCredentialError("Meta OAuth state is invalid.")
    return hashlib.sha256(state.encode()).hexdigest()

def oauth_state_expiry(now: datetime|None=None) -> str:
    issued=now or datetime.now(timezone.utc)
    if issued.tzinfo is None: raise ValueError("OAuth state clock must be timezone-aware.")
    return (issued+timedelta(minutes=10)).isoformat()

def _service_client():
    from app import database
    client=database.get_supabase_oauth_callback_client()
    if client is None: raise MetaCredentialError("Secure Meta storage is not configured.")
    return client

def create_oauth_state(owner_id: str,purpose: str="facebook_login") -> str:
    if not owner_id: raise MetaCredentialError("Authenticated owner is required.")
    _purpose(purpose); state=new_oauth_state()
    try:
        _service_client().table("meta_oauth_states").insert({"state_hash":hash_oauth_state(state),"owner_id":owner_id,"purpose":purpose,"expires_at":oauth_state_expiry()}).execute()
    except Exception as exc: raise MetaCredentialError("Could not securely create Meta OAuth state.") from exc
    return state

def consume_oauth_state(state: str,purpose: str|None=None,expected_owner_id: str|None=None) -> dict[str,str]:
    state_hash=hash_oauth_state(state); now=datetime.now(timezone.utc).isoformat()
    try:
        query=_service_client().table("meta_oauth_states").update({"consumed_at":now}).eq("state_hash",state_hash).is_("consumed_at","null").gt("expires_at",now)
        if purpose: query=query.eq("purpose",purpose)
        if expected_owner_id: query=query.eq("owner_id",expected_owner_id)
        rows=getattr(query.select("owner_id,purpose,expires_at").execute(),"data",None) or []
    except Exception as exc: raise MetaCredentialError("Could not validate Meta OAuth state.") from exc
    if len(rows)!=1: raise MetaCredentialError("Meta OAuth state is invalid, expired, or already used.")
    return {"owner_id":rows[0]["owner_id"],"purpose":rows[0]["purpose"]}

def store_connection(owner_id: str,access_token: str,*,purpose: str,token_expires_at: str|None=None,granted_scopes: list[str]|None=None,facebook_pages=None,instagram_accounts=None,whatsapp_accounts=None) -> None:
    if not owner_id: raise MetaCredentialError("Authenticated owner is required.")
    purpose=_purpose(purpose)
    assets={"facebook_pages":facebook_pages or [],"instagram_accounts":instagram_accounts or [],"whatsapp_accounts":whatsapp_accounts or []}
    try:
        _service_client().table("meta_user_connections").upsert({
            "owner_id":owner_id,"purpose":purpose,"encrypted_access_token":encrypt_access_token(access_token),
            "token_expires_at":token_expires_at,"granted_scopes":sorted(set(granted_scopes or [])),
            "assets":json.loads(json.dumps(assets)),"updated_at":datetime.now(timezone.utc).isoformat()
        },on_conflict="owner_id,purpose").execute()
    except MetaCredentialError: raise
    except Exception as exc: raise MetaCredentialError("Could not securely save the Meta connection.") from exc

def load_access_token(owner_id: str,purpose: str) -> str:
    purpose=_purpose(purpose)
    try:
        rows=getattr(_service_client().table("meta_user_connections").select("encrypted_access_token").eq("owner_id",owner_id).eq("purpose",purpose).limit(1).execute(),"data",None) or []
    except Exception as exc: raise MetaCredentialError("Could not load the Meta connection.") from exc
    if len(rows)!=1: raise MetaCredentialError("Meta account is not connected for this platform.")
    return decrypt_access_token(rows[0].get("encrypted_access_token",""))

def connection_status(owner_id: str) -> dict[str,Any]:
    try:
        rows=getattr(_service_client().table("meta_user_connections").select("purpose,granted_scopes,assets,token_expires_at,updated_at").eq("owner_id",owner_id).execute(),"data",None) or []
    except Exception as exc: raise MetaCredentialError("Could not check the Meta connection.") from exc
    merged={"facebook_pages":[],"instagram_accounts":[],"whatsapp_accounts":[],"business_portfolios":[]}
    scopes=set(); purposes=[]; expiries=[]; updated=[]
    for row in rows:
        purposes.append(row.get("purpose"))
        scopes.update(row.get("granted_scopes") or [])
        assets=row.get("assets") or {}
        for key in merged:
            for item in assets.get(key,[]):
                if item not in merged[key]: merged[key].append(item)
        if row.get("token_expires_at"): expiries.append(row["token_expires_at"])
        if row.get("updated_at"): updated.append(row["updated_at"])
    return {"connected":bool(rows),"connections":sorted(p for p in purposes if p),"granted_scopes":sorted(scopes),"assets":merged,"token_expires_at":min(expiries) if expiries else None,"updated_at":max(updated) if updated else None}

def disconnect_owner(owner_id: str) -> bool:
    try: _service_client().table("meta_user_connections").delete().eq("owner_id",owner_id).execute(); return True
    except Exception as exc: raise MetaCredentialError("Could not disconnect the Meta account.") from exc

def update_connection_assets(owner_id: str,assets: dict[str,Any],purpose: str) -> None:
    purpose=_purpose(purpose); allowed={"facebook_pages","instagram_accounts","whatsapp_accounts","business_portfolios"}
    try:
        _service_client().table("meta_user_connections").update({"assets":json.loads(json.dumps({k:assets.get(k,[]) for k in allowed})),"updated_at":datetime.now(timezone.utc).isoformat()}).eq("owner_id",owner_id).eq("purpose",purpose).execute()
    except Exception as exc: raise MetaCredentialError("Could not save discovered Meta assets.") from exc
