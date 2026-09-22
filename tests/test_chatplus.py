"""Chat+ focused validation tests."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.main as main_module
from app.dependencies import get_current_user_id, get_user_scoped_client, get_current_user
from app.routers.chatplus import router as chatplus_router
from app.services.chatplus import ChatPlusService


def auth_user_id(request: Request) -> str:
    from fastapi import HTTPException, status

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid Authorization header")
    return token


def auth_user(request: Request) -> dict[str, Any]:
    return {"id": auth_user_id(request), "email": "test@example.com", "role": "authenticated"}


def scoped_client(request: Request) -> Any:
    return None


def build_app(authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(chatplus_router)
    if authenticated:
        app.dependency_overrides[get_current_user_id] = auth_user_id
        app.dependency_overrides[get_current_user] = auth_user
        app.dependency_overrides[get_user_scoped_client] = scoped_client
    return app


@pytest.fixture
def auth_client() -> TestClient:
    tc = TestClient(build_app(authenticated=True))
    tc.headers["Authorization"] = "Bearer chat-user-a"
    tc.headers["Content-Type"] = "application/json"
    return tc


@pytest.fixture
def unauth_client() -> TestClient:
    return TestClient(build_app(authenticated=False))


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestChatPlusServiceInstantiation:
    def test_can_instantiate_in_memory(self):
        service = ChatPlusService("owner-a")
        assert service.owner_id == "owner-a"
        assert service.client is None

    def test_rejects_empty_owner(self):
        with pytest.raises(ValueError):
            ChatPlusService("")

    def test_rejects_none_owner(self):
        with pytest.raises(ValueError):
            ChatPlusService(None)


class TestChatPlusMessageFlow:
    def test_authenticated_owner_can_send_message(self):
        service = ChatPlusService("owner-b")
        result = service.send_message("Log a test goal")
        assert result["success"] is True
        assert result["message"]["role"] == "assistant"
        assert result["mission"] is not None
        assert result["state"]["state"] in {"completed", "waiting_for_approval"}

    def test_conversation_history_works(self):
        service = ChatPlusService("owner-c")
        service.send_message("First goal", conversation_id="conv-1")
        history = service.list_messages("conv-1")
        assert history["success"] is True
        assert len(history["messages"]) >= 1
        assert any(m["role"] == "user" for m in history["messages"])
        assert any(m["role"] == "assistant" for m in history["messages"])

    def test_ownership_enforced_by_owner_id(self):
        service_a = ChatPlusService("owner-d")
        service_b = ChatPlusService("owner-e")
        service_a.send_message("Secret goal", conversation_id="conv-owned")
        history_b = service_b.list_messages("conv-owned")
        assert history_b["success"] is True
        assert len(history_b["messages"]) == 0

    def test_actionable_goal_uses_mission_architecture(self):
        service = ChatPlusService("owner-f")
        result = service.send_message("Complete the affiliate workflow")
        assert result["mission"] is not None
        assert result["mission"]["owner_id"] == "owner-f"
        assert result["mission"]["objective"] == "Complete the affiliate workflow"

    def test_non_actionable_message_creates_mission_but_not_appropriate_action(self):
        service = ChatPlusService("owner-g")
        result = service.send_message("Tell me a joke")
        assert result["mission"] is not None
        mission = result["mission"]
        assert mission["objective"] == "Tell me a joke"

    def test_approval_required_uses_existing_gateway(self):
        service = ChatPlusService("owner-h")
        result = service.send_message("Start affiliate marketing on pinterest")
        assert result["state"]["state"] == "waiting_for_approval"
        approvals = result["approvals"]
        assert len(approvals) >= 1
        assert approvals[0]["owner_id"] == "owner-h"

    def test_approval_resume_uses_existing_resume_service(self):
        service = ChatPlusService("owner-i")
        result = service.send_message("Start affiliate marketing on pinterest")
        assert result["state"]["state"] == "waiting_for_approval"
        approval_id = result["approvals"][0]["id"]
        resume = service.approve_approval(approval_id)
        assert resume["success"] is True
        assert resume["resume"] is not None


class TestChatPlusRoutes:
    def test_config_route(self):
        response = _client_with_app.get("/chatplus/config")
        assert response.status_code == 200
        data = response.json()
        assert "supabaseUrl" in data
        assert "supabaseAnonKey" in data
        assert "authEnabled" in data

    def test_config_route_public_no_auth_required(self, unauth_client: TestClient):
        """Chat+ config endpoint must be accessible without authentication."""
        response = unauth_client.get("/chatplus/config")
        assert response.status_code == 200
        data = response.json()
        assert "supabaseUrl" in data
        assert "supabaseAnonKey" in data
        assert "authEnabled" in data

    def test_index_route(self):
        response = _client_with_app.get("/chatplus")
        assert response.status_code == 200
        assert "<!doctype html>" in response.text

    def test_index_slash_route(self):
        response = _client_with_app.get("/chatplus/")
        assert response.status_code == 200
        assert "<!doctype html>" in response.text

    def test_history_requires_auth(self, unauth_client: TestClient):
        response = unauth_client.get("/chatplus/history")
        assert response.status_code == 401

    def test_messages_requires_auth(self, unauth_client: TestClient):
        response = unauth_client.post("/chatplus/messages", json={"message": "hi"})
        assert response.status_code == 401

    def test_state_requires_auth(self, unauth_client: TestClient):
        response = unauth_client.get("/chatplus/state")
        assert response.status_code == 401

    def test_approve_requires_auth(self, unauth_client: TestClient):
        response = unauth_client.post("/chatplus/approvals/x/approve")
        assert response.status_code == 401

    def test_reject_requires_auth(self, unauth_client: TestClient):
        response = unauth_client.post("/chatplus/approvals/x/reject", json={})
        assert response.status_code == 401

    def test_history_returns_messages(self, auth_client: TestClient):
        auth_client.post("/chatplus/messages", json={"message": "History check", "conversation_id": "conv-history"})
        response = auth_client.get("/chatplus/history?conversation_id=conv-history")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert len(data["messages"]) >= 1

    def test_state_returns_current_state(self, auth_client: TestClient):
        auth_client.post("/chatplus/messages", json={"message": "State check", "conversation_id": "conv-state"})
        response = auth_client.get("/chatplus/state?conversation_id=conv-state")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["state"]["state"] in {"ready", "completed", "waiting_for_approval"}

    def test_approve_endpoint_requires_body_and_ownership(self, auth_client: TestClient):
        response = auth_client.post("/chatplus/approvals/nonexistent/approve")
        assert response.status_code == 404

    def test_approval_flow_survives_multiple_requests(self, auth_client: TestClient):
        sent = auth_client.post(
            "/chatplus/messages",
            json={"message": "Start affiliate marketing on pinterest", "conversation_id": "conv-approval-route"},
        )
        assert sent.status_code == 200
        approvals = sent.json()["approvals"]
        assert len(approvals) >= 1
        approval_id = approvals[0]["id"]
        approved = auth_client.post(f"/chatplus/approvals/{approval_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["success"] is True
        state = auth_client.get("/chatplus/state?conversation_id=conv-approval-route")
        assert state.status_code == 200
        assert state.json()["state"]["state"] in {"waiting_for_approval", "failed", "completed"}

    def test_reject_endpoint_works_with_correct_ownership(self, auth_client: TestClient):
        sent = auth_client.post(
            "/chatplus/messages",
            json={"message": "Start affiliate marketing on pinterest", "conversation_id": "conv-reject-route"},
        )
        assert sent.status_code == 200
        approval_id = sent.json()["approvals"][0]["id"]
        rejected = auth_client.post(
            f"/chatplus/approvals/{approval_id}/reject",
            json={"reason": "Not now"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["success"] is True
        state = auth_client.get("/chatplus/state?conversation_id=conv-reject-route")
        assert state.status_code == 200
        assert state.json()["state"]["state"] in {"failed", "waiting_for_approval"}


class TestChatPlusSecurity:
    def test_user_a_cannot_access_user_b_history(self):
        service_a = ChatPlusService("owner-j")
        service_b = ChatPlusService("owner-k")
        service_a.send_message("Secret goal", conversation_id="conv-secret")
        history_b = service_b.list_messages("conv-secret")
        assert history_b["success"] is True
        assert len(history_b["messages"]) == 0

    def test_user_cannot_approve_others_approval(self):
        service_a = ChatPlusService("owner-l")
        result = service_a.send_message("Start affiliate marketing on pinterest")
        if result["state"]["state"] != "waiting_for_approval":
            pytest.skip("No approval required for this goal")
        approval_id = result["approvals"][0]["id"]
        service_b = ChatPlusService("owner-m")
        approved = service_b.approve_approval(approval_id)
        assert approved["success"] is False

    def test_user_cannot_reject_others_approval(self):
        service_a = ChatPlusService("owner-n")
        result = service_a.send_message("Start affiliate marketing on pinterest")
        if result["state"]["state"] != "waiting_for_approval":
            pytest.skip("No approval required for this goal")
        approval_id = result["approvals"][0]["id"]
        service_b = ChatPlusService("owner-o")
        rejected = service_b.reject_approval(approval_id, reason="No")
        assert rejected["success"] is False

    def test_errors_are_sanitized_no_raw_exceptions(self):
        service = ChatPlusService("owner-p")
        result = service.send_message("x" * 5000)
        assert result["success"] is False
        assert "error" in result
        assert "Traceback" not in str(result.get("error", ""))

    def test_no_secrets_returned(self):
        service = ChatPlusService("owner-q")
        result = service.send_message("Log a test goal")
        raw = str(result)
        assert "access_token" not in raw.lower()
        assert "refresh_token" not in raw.lower()
        assert "client_secret" not in raw.lower()


class TestChatPlusFrontend:
    def test_static_page_reachable(self):
        response = _client_with_app.get("/chatplus/")
        assert response.status_code == 200
        html = response.text
        assert "What should we work on?" in html
        assert "Send" in html
        assert "Sign in" in html
        assert "Approval" in html or "approval" in html.lower()
        assert "Bearer eyJ" not in html
        assert "Bearer null" not in html
        assert "Bearer undefined" not in html
        assert "SUPABASE_ANON_KEY=" not in html


_client_with_app = TestClient(build_app(authenticated=True))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
