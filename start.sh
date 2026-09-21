#!/bin/bash
# Start script for AEA Core backend.
#
# Usage:
#   Local dev:  bash start.sh
#   Hosting:    bash start.sh  (platform sets $PORT)
#
# Binds to 0.0.0.0 with PORT env (default 8000 for local dev).
# Does NOT read or print any secrets.
set -euo pipefail

cd "$(dirname "$0")/backend"

PORT="${PORT:-8000}"

exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT"
