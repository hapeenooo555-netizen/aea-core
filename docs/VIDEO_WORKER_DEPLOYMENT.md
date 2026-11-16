# AEA durable video assembly worker

Final MP4 rendering runs in a separate long-lived worker process. It is not
executed inside the FastAPI request process.

## Required worker environment

- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY` (server-side worker only; never expose to Hub)
- Optional `AEA_VIDEO_WORKER_ID` (defaults to hostname)
- Optional `AEA_VIDEO_WORKER_POLL_SECONDS` (defaults to 10; minimum 2)

The API service still requires the normal user-scoped Supabase public key.
Runway and ElevenLabs keys remain on the AEA Core API service.

## Database prerequisite

Apply Supabase migrations in order, including:

- `20261115190000_video_generation_jobs.sql`
- `20261116080000_durable_video_assembly_queue.sql`

The latter creates the owner-scoped queue, an atomic `FOR UPDATE SKIP LOCKED`
claim function, lease recovery, and bounded retry/backoff. The queue's claim
and finish RPCs are executable only by `service_role`.

## Worker service

Create a separate Render Background Worker using the same Core repository and
the backend working directory. Start command:

```bash
python -m app.video_assembly_worker
```

Install dependencies from `backend/requirements.txt` and set the worker
environment above. Do not run the worker as a public web service. Keep
`SUPABASE_SERVICE_ROLE_KEY` only in this worker's private environment.

The API returns `queued` after it persists the job. The worker claims one job
at a time, downloads the scene clips and narration, renders the MP4, stores it
in the private `aea-video-assets` bucket, and records completion. Failed jobs
are retried up to three attempts with backoff; expired 15-minute leases can be
reclaimed after a worker restart.

## Operational verification

Automated tests verify queue handling and worker outcomes. Before enabling
production video creation, apply the migrations, deploy the separate worker,
confirm it can claim a test job, and run one low-cost Runway + ElevenLabs
end-to-end generation. No provider API key or production migration is
configured by this source change itself.
