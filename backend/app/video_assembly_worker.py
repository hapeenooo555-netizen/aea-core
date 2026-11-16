"""Durable AEA final-video assembly worker.

Run as a separate long-lived process:
    python -m app.video_assembly_worker

The worker uses only a server-side Supabase service-role client. Never expose
SUPABASE_SERVICE_ROLE_KEY to the Hub or browser.
"""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

from app.database import get_supabase_video_worker_client
from app.routers.video_generation import _assemble_project_video

logger = logging.getLogger("aea.video_worker")


def process_one_job(client: Any, worker_id: str) -> bool:
    """Claim and process at most one durable video assembly job."""
    claim = client.rpc("claim_video_assembly_job", {"p_worker_id": worker_id}).execute()
    rows = getattr(claim, "data", None) or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return False

    job = rows[0]
    job_id = str(job["id"])
    project_id = str(job["project_id"])
    owner_id = str(job["owner_id"])
    succeeded = _assemble_project_video(project_id, owner_id, client)

    finished = client.rpc(
        "finish_video_assembly_job",
        {
            "p_job_id": job_id,
            "p_worker_id": worker_id,
            "p_success": bool(succeeded),
            "p_error": None if succeeded else "Final MP4 assembly failed; inspect project assembly_error.",
        },
    ).execute()
    result = getattr(finished, "data", None)
    if result is not True:
        raise RuntimeError("Could not persist the durable video assembly job outcome.")

    # Keep project status consistent with retry state after an exhausted job.
    if not succeeded:
        current = client.table("video_assembly_jobs").select(
            "status,last_error"
        ).eq("id", job_id).limit(1).execute()
        job_rows = getattr(current, "data", None) or []
        if job_rows and job_rows[0].get("status") == "failed":
            client.table("video_projects").update({
                "status": "failed",
                "assembly_error": job_rows[0].get("last_error") or "Final MP4 assembly failed after retries.",
            }).eq("id", project_id).eq("owner_id", owner_id).execute()
        logger.warning("Video assembly job %s failed (retry handled by queue).", job_id)
    else:
        logger.info("Video assembly job %s completed.", job_id)
    return True


def run_worker() -> None:
    """Poll the durable queue until the process is stopped."""
    client = get_supabase_video_worker_client()
    if client is None:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY for the isolated video worker.")
    worker_id = os.getenv("AEA_VIDEO_WORKER_ID", socket.gethostname())
    poll_seconds = max(2, int(os.getenv("AEA_VIDEO_WORKER_POLL_SECONDS", "10")))
    logger.info("AEA video assembly worker %s started.", worker_id)
    while True:
        try:
            processed = process_one_job(client, worker_id)
            if not processed:
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            logger.info("AEA video assembly worker stopping.")
            break
        except Exception:
            logger.exception("Video worker loop failed; retrying after delay.")
            time.sleep(poll_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    run_worker()
