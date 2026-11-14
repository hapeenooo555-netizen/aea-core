"""Permission-aware Meta Graph asset discovery for each connected owner flow."""
from __future__ import annotations
from typing import Any
import httpx
from app.services import meta_user_connection as vault
from app.services.meta_oauth import graph_version

class MetaAssetDiscoveryError(RuntimeError):
    pass

async def _get_json(client: httpx.AsyncClient,url: str,token: str) -> dict[str,Any]:
    try:
        response=await client.get(url,headers={"Authorization":f"Bearer {token}"})
        response.raise_for_status(); data=response.json()
        if not isinstance(data,dict): raise MetaAssetDiscoveryError("Meta returned an invalid asset response.")
        return data
    except MetaAssetDiscoveryError: raise
    except (httpx.HTTPError,ValueError,TypeError) as exc: raise MetaAssetDiscoveryError("Meta asset discovery failed. Check granted permissions and retry.") from exc

async def _edge(client: httpx.AsyncClient,url: str,token: str,max_pages: int=20) -> list[dict[str,Any]]:
    rows=[]
    for _ in range(max_pages):
        data=await _get_json(client,url,token); batch=data.get("data",[])
        if isinstance(batch,list): rows.extend(x for x in batch if isinstance(x,dict))
        paging=data.get("paging") or {}; next_url=paging.get("next") if isinstance(paging,dict) else None
        if not next_url: return rows
        if not isinstance(next_url,str) or not next_url.startswith("https://graph.facebook.com/"): raise MetaAssetDiscoveryError("Meta returned an unsafe pagination URL.")
        url=next_url
    raise MetaAssetDiscoveryError("Meta asset list exceeded the pagination safety limit.")

async def discover_assets(owner_id: str,purpose: str) -> dict[str,Any]:
    if purpose not in {"facebook_login","whatsapp_embedded_signup"}: raise MetaAssetDiscoveryError("Unsupported Meta asset discovery purpose.")
    token=vault.load_access_token(owner_id,purpose)
    base=f"https://graph.facebook.com/{graph_version()}"
    pages=[]; instagram=[]; businesses=[]; whatsapp=[]
    async with httpx.AsyncClient(timeout=25.0) as client:
        me=await _get_json(client,f"{base}/me?fields=id,name",token)
        if purpose=="facebook_login":
            page_rows=await _edge(client,f"{base}/me/accounts?fields=id,name,category,tasks,instagram_business_account{{id,username}}",token)
            for page in page_rows:
                page_id=page.get("id")
                if not page_id: continue
                pages.append({"id":str(page_id),"name":page.get("name"),"category":page.get("category"),"tasks":page.get("tasks") or []})
                ig=page.get("instagram_business_account")
                if isinstance(ig,dict) and ig.get("id"): instagram.append({"id":str(ig["id"]),"username":ig.get("username"),"page_id":str(page_id),"page_name":page.get("name")})
        business_rows=await _edge(client,f"{base}/me/businesses?fields=id,name",token)
        businesses=[{"id":str(b["id"]),"name":b.get("name")} for b in business_rows if b.get("id")]
        if purpose=="whatsapp_embedded_signup":
            # Embedded Signup reports the created/selected WABA and phone in the
            # SDK completion message; Graph lookup confirms accessible account data.
            for business in businesses:
                for edge_name in ("owned_whatsapp_business_accounts","client_whatsapp_business_accounts"):
                    wabas=await _edge(client,f"{base}/{business['id']}/{edge_name}?fields=id,name",token)
                    for waba in wabas:
                        waba_id=waba.get("id")
                        if not waba_id: continue
                        phones=await _edge(client,f"{base}/{waba_id}/phone_numbers?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status",token)
                        whatsapp.append({"waba_id":str(waba_id),"name":waba.get("name"),"business_id":business["id"],"relationship":edge_name,"phone_numbers":[{"id":str(p["id"]),"display_phone_number":p.get("display_phone_number"),"verified_name":p.get("verified_name"),"quality_rating":p.get("quality_rating"),"code_verification_status":p.get("code_verification_status")} for p in phones if p.get("id")]})
    assets={"facebook_pages":pages,"instagram_accounts":instagram,"whatsapp_accounts":whatsapp,"business_portfolios":businesses}
    vault.update_connection_assets(owner_id,assets,purpose)
    return {"meta_user":{"id":me.get("id"),"name":me.get("name")},"assets":assets,"discovered":True,"purpose":purpose}
