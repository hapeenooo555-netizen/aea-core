from __future__ import annotations

from types import SimpleNamespace

from app import video_assembly_worker as worker


class FakeQuery:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.filters = {}

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _n):
        return self

    def update(self, values):
        self.client.updated.append((self.table_name, self.filters, values))
        return self

    def execute(self):
        rows = [r for r in self.client.tables.get(self.table_name, [])
                if all(r.get(k) == v for k, v in self.filters.items())]
        return SimpleNamespace(data=rows)


class FakeRPC:
    def __init__(self, client, name, params):
        self.client, self.name, self.params = client, name, params

    def execute(self):
        if self.name == "claim_video_assembly_job":
            return SimpleNamespace(data=self.client.claimed)
        if self.name == "finish_video_assembly_job":
            self.client.finished.append(self.params)
            return SimpleNamespace(data=True)
        raise AssertionError(self.name)


class FakeClient:
    def __init__(self, claimed=None, job_rows=None):
        self.claimed = claimed or []
        self.tables = {"video_assembly_jobs": job_rows or []}
        self.updated = []
        self.finished = []

    def rpc(self, name, params):
        return FakeRPC(self, name, params)

    def table(self, name):
        return FakeQuery(self, name)


def test_worker_returns_false_when_queue_is_empty():
    assert worker.process_one_job(FakeClient(), "worker-1") is False


def test_worker_claims_and_finishes_successful_assembly(monkeypatch):
    job = {"id": "job-1", "project_id": "project-1", "owner_id": "owner-1"}
    client = FakeClient(claimed=[job])
    seen = []
    monkeypatch.setattr(worker, "_assemble_project_video",
                        lambda project, owner, db: seen.append((project, owner, db)) or True)

    assert worker.process_one_job(client, "worker-1") is True
    assert seen == [("project-1", "owner-1", client)]
    assert client.finished == [{
        "p_job_id": "job-1", "p_worker_id": "worker-1",
        "p_success": True, "p_error": None,
    }]


def test_worker_records_failure_and_leaves_retry_to_queue(monkeypatch):
    job = {"id": "job-2", "project_id": "project-2", "owner_id": "owner-2"}
    client = FakeClient(claimed=[job], job_rows=[{"id": "job-2", "status": "queued"}])
    monkeypatch.setattr(worker, "_assemble_project_video", lambda *_args: False)

    assert worker.process_one_job(client, "worker-2") is True
    assert client.finished[0]["p_success"] is False
    assert client.tables["video_assembly_jobs"][0]["status"] == "queued"
