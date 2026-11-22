from app.services.agent_runtime_contract import RuntimeRequest
from app.services.agent_runtime import AEAAgentRuntime


class FakeOrchestration:
    def __init__(self, mission=None, outcome=None):
        self.mission = mission or {"id": "mission-1", "status": "pending", "result": {}}
        self.outcome = outcome or {}
        self.created = None

    def create_mission(self, objective, **kwargs):
        self.created = (objective, kwargs)
        return {"success": True, "mission": self.mission}

    def run_mission(self, mission_id):
        return self.outcome

    def get_mission(self, mission_id):
        return self.mission


def test_runtime_adapter_delegates_mission_creation_and_preserves_context():
    orchestration = FakeOrchestration()
    runtime = AEAAgentRuntime("owner-1", orchestration=orchestration)
    request = RuntimeRequest(
        owner_id="owner-1",
        objective="Prepare a content draft",
        request_id="req-123",
        locale="sw-TZ",
        timezone="Africa/Dar_es_Salaam",
        success_criteria=("save draft",),
    )
    result = runtime.submit(request)
    assert result["success"] is True
    assert result["status"] == "ACCEPTED"
    assert orchestration.created[0] == request.objective
    assert orchestration.created[1]["metadata"]["locale"] == "sw-TZ"
    assert orchestration.created[1]["idempotency_key"] == "req-123"


def test_runtime_adapter_rejects_cross_owner_request():
    runtime = AEAAgentRuntime("owner-1", orchestration=FakeOrchestration())
    result = runtime.submit(RuntimeRequest(owner_id="owner-2", objective="Do work"))
    assert result["success"] is False
    assert result["status"] == "REJECTED"


def test_runtime_adapter_maps_waiting_approval_state_from_employee_report():
    mission = {
        "id": "mission-2",
        "status": "waiting_approval",
        "result": {
            "success": False,
            "status": "WAIT_FOR_APPROVAL",
            "report": {
                "execution_id": "execution-2",
                "resume_information": {"approval_request_id": "approval-1", "execution_id": "execution-2"},
            },
        },
    }
    runtime = AEAAgentRuntime("owner-1", orchestration=FakeOrchestration(mission=mission))
    result = runtime.submit(RuntimeRequest(owner_id="owner-1", objective="Publish externally"))
    assert result["success"] is True
    assert result["status"] == "WAITING_APPROVAL"
    assert result["pending_approvals"] == ("approval-1",)
    assert result["execution_id"] == "execution-2"


def test_runtime_adapter_preserves_explicit_pending_approval_references():
    mission = {
        "id": "mission-3",
        "status": "waiting_approval",
        "result": {},
        "pending_approvals": ["approval-1", "approval-2"],
    }
    runtime = AEAAgentRuntime("owner-1", orchestration=FakeOrchestration(mission=mission))
    result = runtime.submit(RuntimeRequest(owner_id="owner-1", objective="Publish externally"))
    assert result["success"] is True
    assert result["pending_approvals"] == ("approval-1", "approval-2")


def test_runtime_adapter_rejects_mission_without_id():
    runtime = AEAAgentRuntime("owner-1", orchestration=FakeOrchestration(mission={"status": "pending"}))
    result = runtime.submit(RuntimeRequest(owner_id="owner-1", objective="Do work"))
    assert result["success"] is False
    assert "no mission identifier" in result["errors"][0]
