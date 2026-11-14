"""Authenticated AEA Hub Meta OAuth API."""
from __future__ import annotations
import os
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel,Field
from app.dependencies import get_current_user_id
from app.services import meta_user_connection as vault
from app.services.meta_oauth import MetaOAuthError,complete_flow,start_flow
from app.services.meta_asset_discovery import MetaAssetDiscoveryError,discover_assets
router=APIRouter(prefix="/meta/oauth",tags=["meta-oauth"])
class StartRequest(BaseModel):
    purpose:Literal["facebook_login","whatsapp_embedded_signup"]
class CompleteRequest(BaseModel):
    purpose:Literal["facebook_login","whatsapp_embedded_signup"]
    state:str=Field(min_length=32,max_length=256)
    code:str=Field(min_length=1,max_length=4096)

@router.get("/config")
async def config(owner_id:str=Depends(get_current_user_id)):
    return {"facebook_login_configured":bool(os.getenv("META_APP_ID") and os.getenv("META_FACEBOOK_LOGIN_CONFIG_ID") and os.getenv("META_OAUTH_REDIRECT_URI")),"whatsapp_embedded_signup_configured":bool(os.getenv("META_APP_ID") and os.getenv("META_WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID") and os.getenv("META_OAUTH_REDIRECT_URI")),"connected":vault.connection_status(owner_id).get("connected",False)}

@router.post("/start")
async def start(request:StartRequest,owner_id:str=Depends(get_current_user_id)):
    try:return {"success":True,**start_flow(owner_id,request.purpose)}
    except (MetaOAuthError,vault.MetaCredentialError) as exc:raise HTTPException(status_code=503,detail=str(exc)) from exc

@router.post("/complete")
async def complete(request:CompleteRequest,owner_id:str=Depends(get_current_user_id)):
    try:return {"success":True,**await complete_flow(request.state,request.code,request.purpose,owner_id)}
    except (MetaOAuthError,vault.MetaCredentialError) as exc:raise HTTPException(status_code=400,detail=str(exc)) from exc

@router.get("/facebook/callback")
async def facebook_callback(code:str|None=None,state:str|None=None,error:str|None=None):
    hub=os.getenv("AEA_HUB_URL","https://aea-affiliate-hub-web.onrender.com").rstrip("/")
    if error or not code or not state:return RedirectResponse(f"{hub}/settings/integrations?meta=authorization_error",status_code=303)
    try:
        result=await complete_flow(state,code,"facebook_login")
        return RedirectResponse(f"{hub}/settings/integrations?meta=authorized",status_code=303)
    except (MetaOAuthError,vault.MetaCredentialError):
        return RedirectResponse(f"{hub}/settings/integrations?meta=connection_error",status_code=303)

@router.get("/status")
async def status_route(owner_id:str=Depends(get_current_user_id)):
    try:return {"success":True,**vault.connection_status(owner_id)}
    except vault.MetaCredentialError as exc:raise HTTPException(status_code=503,detail=str(exc)) from exc

@router.delete("/disconnect")
async def disconnect(owner_id:str=Depends(get_current_user_id)):
    try:vault.disconnect_owner(owner_id);return {"success":True,"connected":False,"scope":"local_credentials_only"}
    except vault.MetaCredentialError as exc:raise HTTPException(status_code=503,detail=str(exc)) from exc


@router.post("/assets/refresh")
async def refresh_assets(owner_id:str=Depends(get_current_user_id)):
    try:
        result=await discover_assets(owner_id)
        return {"success":True,**result}
    except (MetaAssetDiscoveryError,vault.MetaCredentialError) as exc:
        raise HTTPException(status_code=502,detail=str(exc)) from exc
