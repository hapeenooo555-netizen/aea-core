"""Owner-scoped Meta publishing through the encrypted OAuth credential vault."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.services import meta_user_connection as vault
from .base import BaseConnector, ConnectorCapabilities


@dataclass
class MetaConfig:
    graph_base_url: str = "https://graph.facebook.com"
    api_version: str = "v27.0"

    @property
    def base_url(self) -> str:
        return f"{self.graph_base_url.rstrip('/')}/{self.api_version}"


class MetaMarketingConnector(BaseConnector):
    """Facebook, Instagram and WhatsApp execution boundary.

    Publishing never reads a shared META_ACCESS_TOKEN. It resolves the
    authenticated owner's encrypted OAuth credential and the matching asset
    recorded by Meta asset discovery.
    """

    def __init__(
        self,
        *,
        transport: Callable[..., dict[str, Any]] | None = None,
        config: MetaConfig | None = None,
    ) -> None:
        super().__init__()
        self.config = config or MetaConfig(
            graph_base_url=os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com"),
            api_version=os.getenv("META_GRAPH_API_VERSION", "v27.0"),
        )
        self._transport = transport

    @property
    def platform(self) -> str:
        return "meta"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            platform="meta",
            account_status=True,
            connect_account=False,
            disconnect_account=False,
            publish_content=True,
            human_intervention_capable=True,
        )

    def start_onboarding(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {
            "status": "awaiting_human",
            "workflow_id": f"meta-connect-{worker_id}",
            "platform": self.platform,
            "requires_human_intervention": True,
            "checkpoint_type": "oauth_authorization_required",
            "instructions": "Connect Facebook/Instagram or WhatsApp from AEA Hub Integrations.",
        }

    def resume_onboarding(self, workflow_id: str, human_input: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "awaiting_human_intervention",
            "workflow_id": workflow_id,
            "platform": self.platform,
            "requires_human_intervention": True,
            "checkpoint_type": "oauth_authorization_required",
            "instructions": "Complete Meta OAuth from AEA Hub Integrations; do not paste access tokens into this workflow.",
        }

    def health_check(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "status": "owner_scoped_oauth_required",
            "connection_type": "per_user_encrypted_vault",
            "user_oauth_supported": True,
            "details": {
                "facebook": "requires_facebook_login_and_page_asset",
                "instagram": "requires_linked_instagram_professional_account",
                "whatsapp": "requires_embedded_signup_and_phone_asset",
            },
        }

    def get_account_status(self) -> dict[str, Any]:
        health = self.health_check()
        return {
            "status": "not_started",
            "configuration_status": health["status"],
            "connection_type": health["connection_type"],
            "user_oauth_supported": True,
            "details": health["details"],
        }

    def connect_account(self, worker_id: str, auth_data: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "use_meta_oauth_routes",
            "error": "Connect Meta through the owner-authenticated AEA Hub OAuth flow; raw tokens are not accepted here.",
        }

    def disconnect_account(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "use_meta_oauth_routes",
            "error": "Disconnect Meta through the owner-authenticated Meta OAuth route.",
        }

    @staticmethod
    def _asset_id(value: Any) -> str | None:
        if isinstance(value, dict) and value.get("id") is not None:
            return str(value["id"])
        return None

    def _page_access_token(self, client: httpx.Client, user_token: str, page_id: str) -> str:
        response = client.get(
            f"{self.config.base_url}/me/accounts",
            headers={"Authorization": f"Bearer {user_token}"},
            params={"fields": "id,access_token", "limit": 100},
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        for page in rows if isinstance(rows, list) else []:
            if isinstance(page, dict) and str(page.get("id")) == page_id:
                token = page.get("access_token")
                if isinstance(token, str) and token:
                    return token
        raise RuntimeError("Meta did not return a Page access token for the selected owned Page. Check Page tasks and granted permissions.")

    def publish_content(
        self,
        worker_id: str,
        content: dict[str, Any],
        *,
        idempotency_key: str | None = None,
        owner_id: str | None = None,
    ) -> dict[str, Any]:
        channel = str(content.get("channel") or "").strip().lower()
        if channel not in {"facebook", "instagram", "whatsapp"}:
            return {"success": False, "status": "invalid_request", "error": "channel must be facebook, instagram, or whatsapp"}
        if not owner_id:
            return {"success": False, "status": "owner_required", "error": "Authenticated owner is required; shared Meta credentials are not supported."}
        purpose = "whatsapp_embedded_signup" if channel == "whatsapp" else "facebook_login"
        if self._transport is not None:
            return self._transport(
                channel=channel,
                worker_id=worker_id,
                owner_id=owner_id,
                content=dict(content),
                base_url=self.config.base_url,
                idempotency_key=idempotency_key,
                credential_purpose=purpose,
            )
        try:
            token = vault.load_access_token(owner_id, purpose)
            connection = vault.connection_status(owner_id)
            assets = connection.get("assets") if isinstance(connection, dict) else {}
            assets = assets if isinstance(assets, dict) else {}
            with httpx.Client(timeout=25.0) as client:
                if channel == "whatsapp":
                    return self._publish_whatsapp(client, token, assets, content)
                return self._publish_page_channel(client, token, assets, channel, content)
        except vault.MetaCredentialError as exc:
            return {
                "success": False,
                "status": "not_connected",
                "requires_human_intervention": True,
                "checkpoint_type": "oauth_authorization_required",
                "error": str(exc),
            }
        except httpx.HTTPStatusError as exc:
            return {
                "success": False,
                "status": "remote_error",
                "http_status": exc.response.status_code,
                "error": exc.response.text[:500],
            }
        except httpx.HTTPError:
            return {"success": False, "status": "transport_error", "error": "Meta API request failed. Retry after checking the connection."}
        except (RuntimeError, ValueError, TypeError) as exc:
            return {"success": False, "status": "asset_or_request_error", "error": str(exc)}

    def _publish_page_channel(
        self,
        client: httpx.Client,
        user_token: str,
        assets: dict[str, Any],
        channel: str,
        content: dict[str, Any],
    ) -> dict[str, Any]:
        pages = assets.get("facebook_pages") if isinstance(assets.get("facebook_pages"), list) else []
        instagram = assets.get("instagram_accounts") if isinstance(assets.get("instagram_accounts"), list) else []
        if channel == "facebook":
            requested_page = str(content.get("page_id") or "")
            page = next((p for p in pages if isinstance(p, dict) and self._asset_id(p) == requested_page), None) if requested_page else next((p for p in pages if isinstance(p, dict) and self._asset_id(p)), None)
            page_id = self._asset_id(page)
            message = content.get("message")
            if not page_id:
                raise RuntimeError("No discovered Facebook Page is connected to this AEA account.")
            if not isinstance(message, str) or not message.strip():
                raise ValueError("Facebook publishing requires message text.")
            page_token = self._page_access_token(client, user_token, page_id)
            image_url = content.get("image_url") or content.get("media_url")
            if image_url is not None and (not isinstance(image_url, str) or not image_url.startswith("https://")):
                raise ValueError("Facebook image publishing requires an HTTPS image_url.")
            if image_url:
                response = client.post(
                    f"{self.config.base_url}/{page_id}/photos",
                    headers={"Authorization": f"Bearer {page_token}"},
                    data={"url": image_url, "caption": message},
                )
            else:
                response = client.post(
                    f"{self.config.base_url}/{page_id}/feed",
                    headers={"Authorization": f"Bearer {page_token}"},
                    data={"message": message},
                )
            response.raise_for_status()
            result = response.json()
            post_id = result.get("post_id") or result.get("id")
            if not post_id:
                raise RuntimeError("Meta accepted the request without returning a verifiable Facebook post ID.")
            return {
                "success": True,
                "status": "published",
                "channel": channel,
                "page_id": page_id,
                "post_id": str(post_id),
                "provider_reference": str(post_id),
                "remote": result,
            }

        requested_ig = str(content.get("instagram_account_id") or "")
        account = next((item for item in instagram if isinstance(item, dict) and self._asset_id(item) == requested_ig), None) if requested_ig else next((item for item in instagram if isinstance(item, dict) and self._asset_id(item)), None)
        instagram_id = self._asset_id(account)
        page_id = str(account.get("page_id") or "") if isinstance(account, dict) else ""
        image_url = content.get("image_url") or content.get("media_url")
        if not instagram_id or not page_id:
            raise RuntimeError("No linked Instagram Professional account and Facebook Page were discovered.")
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise ValueError("Instagram publishing requires a publicly reachable HTTPS image_url or media_url.")
        page_token = self._page_access_token(client, user_token, page_id)
        create = client.post(
            f"{self.config.base_url}/{instagram_id}/media",
            headers={"Authorization": f"Bearer {page_token}"},
            data={"image_url": image_url, "caption": str(content.get("message") or "")},
        )
        create.raise_for_status()
        creation_id = create.json().get("id")
        if not creation_id:
            raise RuntimeError("Meta did not return an Instagram media container ID.")
        publish = client.post(
            f"{self.config.base_url}/{instagram_id}/media_publish",
            headers={"Authorization": f"Bearer {page_token}"},
            data={"creation_id": creation_id},
        )
        publish.raise_for_status()
        published_result = publish.json()
        post_id = published_result.get("id")
        if not post_id:
            raise RuntimeError("Meta published the Instagram media without returning a verifiable media ID.")
        return {
            "success": True,
            "status": "published",
            "channel": channel,
            "instagram_account_id": instagram_id,
            "post_id": str(post_id),
            "provider_reference": str(post_id),
            "remote": published_result,
        }

    def _publish_whatsapp(
        self,
        client: httpx.Client,
        token: str,
        assets: dict[str, Any],
        content: dict[str, Any],
    ) -> dict[str, Any]:
        accounts = assets.get("whatsapp_accounts") if isinstance(assets.get("whatsapp_accounts"), list) else []
        phone_numbers = []
        for account in accounts:
            if isinstance(account, dict) and isinstance(account.get("phone_numbers"), list):
                phone_numbers.extend(p for p in account["phone_numbers"] if isinstance(p, dict))
        requested_phone = str(content.get("phone_number_id") or "")
        phone = next((p for p in phone_numbers if self._asset_id(p) == requested_phone), None) if requested_phone else next((p for p in phone_numbers if self._asset_id(p)), None)
        phone_id = self._asset_id(phone)
        recipient = content.get("recipient") or content.get("to")
        message = content.get("message")
        if not phone_id:
            raise RuntimeError("No WhatsApp phone number was discovered through Embedded Signup.")
        if not isinstance(recipient, str) or not recipient.strip() or not isinstance(message, str) or not message.strip():
            raise ValueError("WhatsApp requires recipient and message text.")
        response = client.post(
            f"{self.config.base_url}/{phone_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "to": recipient,
                "type": "text",
                "text": {"body": message},
            },
        )
        response.raise_for_status()
        return {"success": True, "status": "sent", "channel": "whatsapp", "phone_number_id": phone_id, "remote": response.json()}

    def get_analytics(self, worker_id: str) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "adapter_ready"}
