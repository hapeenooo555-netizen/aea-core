from __future__ import annotations
import pytest
from app.services import meta_asset_discovery as discovery

class FakeResponse:
    def __init__(self,data): self._data=data
    def raise_for_status(self): pass
    def json(self): return self._data

class FakeClient:
    def __init__(self, mapping): self.mapping=mapping; self.urls=[]
    async def get(self,url,headers=None):
        self.urls.append(url)
        for key,value in self.mapping.items():
            if key in url: return FakeResponse(value)
        raise AssertionError(f"Unexpected Graph URL: {url}")

@pytest.mark.asyncio
async def test_asset_discovery_maps_pages_instagram_and_whatsapp(monkeypatch):
    mapping={
      "/me?fields=id,name":{"id":"meta-user","name":"Enock"},
      "/me/accounts?fields=":{"data":[{"id":"page-1","name":"NextGen Page","category":"Business","tasks":["CREATE_CONTENT"],"instagram_business_account":{"id":"ig-1","username":"nextgen"}}]},
      "/me/businesses?fields=":{"data":[{"id":"biz-1","name":"NextGen Portfolio"}]},
      "/biz-1/owned_whatsapp_business_accounts?fields=":{"data":[{"id":"waba-1","name":"Production WABA"}]},
      "/waba-1/phone_numbers?fields=":{"data":[{"id":"phone-1","display_phone_number":"+255700000000","verified_name":"NextGen","quality_rating":"GREEN","code_verification_status":"VERIFIED"}]},
      "/biz-1/client_whatsapp_business_accounts?fields=":{"data":[]},
    }
    fake=FakeClient(mapping)
    class ClientFactory:
      def __init__(self,*a,**kw): pass
      async def __aenter__(self): return fake
      async def __aexit__(self,*a): pass
    monkeypatch.setattr(discovery.httpx,"AsyncClient",ClientFactory)
    monkeypatch.setattr(discovery.vault,"load_access_token",lambda owner,purpose:"secret-token")
    saved={}
    monkeypatch.setattr(discovery.vault,"update_connection_assets",lambda owner,assets,purpose:saved.update(owner=owner,assets=assets,purpose=purpose))
    result=await discovery.discover_assets("owner-1","facebook_login")
    assert result["assets"]["facebook_pages"][0]["id"]=="page-1"
    assert result["assets"]["instagram_accounts"][0]["id"]=="ig-1"
    assert saved["owner"]=="owner-1"
    assert saved["purpose"]=="facebook_login"

@pytest.mark.asyncio
async def test_asset_discovery_rejects_external_pagination_url(monkeypatch):
    class Fake:
      async def get(self,url,headers=None):
        return FakeResponse({"data":[],"paging":{"next":"https://evil.example/steal"}})
    with pytest.raises(discovery.MetaAssetDiscoveryError,match="unsafe pagination"):
      await discovery._edge(Fake(),"https://graph.facebook.com/v23.0/me/accounts","token")

@pytest.mark.asyncio
async def test_whatsapp_discovery_uses_whatsapp_token_and_persists_only_whatsapp_assets(monkeypatch):
    mapping={
      "/me?fields=id,name":{"id":"meta-user","name":"Enock"},
      "/me/businesses?fields=":{"data":[{"id":"biz-1","name":"NextGen Portfolio"}]},
      "/biz-1/owned_whatsapp_business_accounts?fields=":{"data":[{"id":"waba-1","name":"Production WABA"}]},
      "/waba-1/phone_numbers?fields=":{"data":[{"id":"phone-1","display_phone_number":"+255700000000","verified_name":"NextGen","quality_rating":"GREEN","code_verification_status":"VERIFIED"}]},
      "/biz-1/client_whatsapp_business_accounts?fields=":{"data":[]},
    }
    fake=FakeClient(mapping)
    class ClientFactory:
      def __init__(self,*a,**kw): pass
      async def __aenter__(self): return fake
      async def __aexit__(self,*a): pass
    monkeypatch.setattr(discovery.httpx,"AsyncClient",ClientFactory)
    loaded={}; saved={}
    monkeypatch.setattr(discovery.vault,"load_access_token",lambda owner,purpose:loaded.update(owner=owner,purpose=purpose) or "wa-token")
    monkeypatch.setattr(discovery.vault,"update_connection_assets",lambda owner,assets,purpose:saved.update(owner=owner,assets=assets,purpose=purpose))
    result=await discovery.discover_assets("owner-1","whatsapp_embedded_signup")
    assert loaded=={"owner":"owner-1","purpose":"whatsapp_embedded_signup"}
    assert result["assets"]["whatsapp_accounts"][0]["phone_numbers"][0]["id"]=="phone-1"
    assert result["assets"]["facebook_pages"]==[]
    assert saved["purpose"]=="whatsapp_embedded_signup"
