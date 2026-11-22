"""Hub-selected worker/mission linking and first-publish completion."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import approvals as approvals_router
from app.routers import affiliate_pinterest as cp
from app.services.approval_resume_service import ApprovalResumeService

OWNER = "owner-1"
ROWS = {
    "workers": [
        {"id": "w1", "owner_id": OWNER, "role": "affiliate_marketing", "status": "active"},
        {"id": "w-other", "owner_id": "owner-2", "role": "affiliate_marketing", "status": "active"},
    ],
    "missions": [
        {"id": "m1", "owner_id": OWNER, "assigned_worker": "w1"},
        {"id": "m-wrong-worker", "owner_id": OWNER, "assigned_worker": "w9"},
        {"id": "m-other", "owner_id": "owner-2", "worker_id": "w-other"},
    ],
    "graphic_design_jobs": [
        {"id": "img-1", "owner_id": OWNER, "status": "completed", "asset_path": f"{OWNER}/img-1/original.png", "mime_type": "image/png", "width": 1000, "height": 1500},
        {"id": "img-other", "owner_id": "owner-2", "status": "completed", "asset_path": "owner-2/img-other/original.png", "mime_type": "image/png", "width": 1000, "height": 1500},
    ],
}


class Query:
    def __init__(self, table):
        self.rows, self.filters = ROWS.get(table, []), {}

    def select(self, *_):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _):
        return self

    def update(self, values):
        self.update_values = values
        return self

    def execute(self):
        data = [r for r in self.rows if all(r.get(k) == v for k, v in self.filters.items())]
        if hasattr(self, "update_values"):
            for row in data:
                row.update(self.update_values)
        return type("R", (), {"data": data})()


class Client:
    def table(self, name):
        return Query(name)


created = {}


class FakeOrchestration:
    def __init__(self, *_a, **_k):
        pass

    def create_mission(self, **kwargs):
        created["mission"] = kwargs
        ROWS["missions"].append({"id": "approval-mission-1", "owner_id": OWNER, "assigned_worker": None})
        return {"success": True, "mission": {"id": "approval-mission-1"}}


class FakeGateway:
    def __init__(self, *_a, **_k):
        pass

    def list_requests(self, **_k):
        return []

    def create_request(self, **kwargs):
        created["approval"] = kwargs
        return {"success": True, "request": {"id": "appr-1", "mission_id": kwargs["mission_id"], "status": "pending"}}


class FakeExecutionService:
    def __init__(self, **_kwargs):
        pass

    def claim_execution(self, **kwargs):
        created["execution_claim"] = kwargs
        return {
            "success": True,
            "execution": {"execution_id": "execution-1"},
            "created": True,
        }

    def update_execution_state(self, execution_id, owner_id, **kwargs):
        created["execution_state"] = {
            "execution_id": execution_id,
            "owner_id": owner_id,
            **kwargs,
        }
        return {"success": True}


DRAFT = {"title": "T", "description": "D", "destination_url": "https://shop.example/p", "image_url": "https://img.example/p.jpg", "board_id": "b1"}


def make_client(monkeypatch):
    monkeypatch.setattr(cp, "get_access_token", lambda owner_id: {"access_token": "x"})
    monkeypatch.setattr(cp, "verify_account_and_boards", lambda _t: {"account": {"username": "u"}, "boards": [{"id": "b1", "name": "Ideas"}]})
    monkeypatch.setattr(cp, "MissionOrchestrationService", FakeOrchestration)
    monkeypatch.setattr(cp, "ApprovalGateway", FakeGateway)
    monkeypatch.setattr(cp, "MissionExecutionService", FakeExecutionService)
    app = FastAPI()
    app.include_router(cp.router)
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    app.dependency_overrides[get_user_scoped_client] = lambda: Client()
    return TestClient(app)


def test_links_owned_worker_and_mission_and_returns_ids(monkeypatch):
    created.clear()
    res = make_client(monkeypatch).post("/affiliate/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m1"})
    assert res.status_code == 201
    body = res.json()
    assert body["approval_request_id"] == "appr-1"
    assert body["mission_id"] == "approval-mission-1"
    assert body["source_mission_id"] == "m1"
    assert body["published"] is False
    assert created["approval"]["payload"]["source_mission_id"] == "m1"
    assert created["approval"]["payload"]["execution_id"] == "execution-1"
    assert created["execution_claim"]["mission_id"] == "approval-mission-1"
    assert created["execution_state"]["status"] == "WAITING_APPROVAL"
    assert created["mission"]["metadata"]["source_worker_id"] == "w1"
    assert next(row for row in ROWS["missions"] if row["id"] == "approval-mission-1")["assigned_worker"] == "w1"


def test_rejects_another_owners_worker(monkeypatch):
    res = make_client(monkeypatch).post("/affiliate/pinterest/draft-approval", json={**DRAFT, "worker_id": "w-other"})
    assert res.status_code == 404


def test_rejects_another_owners_mission(monkeypatch):
    res = make_client(monkeypatch).post("/affiliate/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m-other"})
    assert res.status_code == 404


def test_rejects_mission_of_a_different_worker(monkeypatch):
    res = make_client(monkeypatch).post("/affiliate/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m-wrong-worker"})
    assert res.status_code == 422


def test_works_without_selection(monkeypatch):
    res = make_client(monkeypatch).post("/affiliate/pinterest/draft-approval", json=DRAFT)
    assert res.status_code == 201
    assert res.json()["source_mission_id"] is None


def _transition_for(monkeypatch, outcome):
    seen = {}

    class Orch:
        def __init__(self, *_a, **_k):
            pass

        def get_mission(self, _id):
            return {"id": "m", "orchestration_idempotency_key": "k", "objective": "o"}

        def transition(self, _id, target, result=None):
            seen["target"] = target

    monkeypatch.setattr(approvals_router, "MissionOrchestrationService", Orch)
    approvals_router._update_p1_8_mission_state({"mission_id": "m"}, outcome, OWNER, None)
    return seen.get("target")


def test_first_publish_with_pin_id_completes_mission(monkeypatch):
    assert _transition_for(monkeypatch, {"status": "resumed", "result": {"pin_id": "123"}}) == "completed"


def test_resumed_without_pin_id_does_not_complete(monkeypatch):
    assert _transition_for(monkeypatch, {"status": "resumed", "result": {}}) is None


def test_resume_failed_marks_approval_mission_failed(monkeypatch):
    assert _transition_for(
        monkeypatch,
        {"status": "resume_failed", "error": "Pinterest rejected pin creation"},
    ) == "failed"



def test_publish_failure_preserves_safe_pinterest_rejection_details():
    class Connector:
        platform = "pinterest"

        def publish_content(self, *_args, **_kwargs):
            return {
                "success": False,
                "status": "publish_failed",
                "error": "Pinterest rejected pin creation (HTTP 403): app_not_authorized",
                "provider_status": 403,
                "provider_error": {"code": 2, "message": "The application is not authorized"},
            }

    service = ApprovalResumeService(
        approval_gateway=object(),
        connector_registry=object(),
    )
    outcome = service._handle_publish_content(
        approval_request_id="approval-1",
        mission_id="mission-1",
        worker_id="worker-1",
        payload={"content": {"title": "Test"}},
        connector=Connector(),
        current_user_id="owner-1",
    )
    assert outcome["status"] == "resume_failed"
    assert outcome["provider_status"] == 403
    assert outcome["provider_error"]["code"] == 2



def test_private_image_asset_preview_requires_owner_scoped_completed_asset(monkeypatch):
    client = make_client(monkeypatch)
    response = client.post(
        "/affiliate/pinterest/draft-preview",
        json={**{k: v for k, v in DRAFT.items() if k != "image_url"}, "image_asset_id": "img-1"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["preview"]["image_asset_id"] == "img-1"
    assert payload["preview"]["image_asset"]["mime_type"] == "image/png"
    assert payload["published"] is False


def test_private_image_asset_preview_rejects_another_owners_asset(monkeypatch):
    client = make_client(monkeypatch)
    response = client.post(
        "/affiliate/pinterest/draft-preview",
        json={**{k: v for k, v in DRAFT.items() if k != "image_url"}, "image_asset_id": "img-other"},
    )
    assert response.status_code == 404


def test_private_image_asset_approval_passes_only_validated_owner_path(monkeypatch):
    created.clear()
    response = make_client(monkeypatch).post(
        "/affiliate/pinterest/draft-approval",
        json={**{k: v for k, v in DRAFT.items() if k != "image_url"}, "image_asset_id": "img-1", "worker_id": "w1"},
    )
    assert response.status_code == 201
    content = created["approval"]["payload"]["content"]
    assert content["image_asset_id"] == "img-1"
    assert content["image_asset_path"] == f"{OWNER}/img-1/original.png"
    assert content["image_asset_mime_type"] == "image/png"
    assert content["image_url"] is None
