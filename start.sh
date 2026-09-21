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

# Ensure dependencies are installed (idempotent — pip skips if already satisfied).
# Provides a runtime fallback when the hosting buildpack does not auto-install
# dependencies from requirements.txt at the repo root.
python3 -m pip install --quiet --no-cache-dir -r requirements.txt 2>&1 || {
    echo "Warning: pip install failed, attempting uvicorn fallback..."
}

PORT="${PORT:-8000}"

exec python3 -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$PORT"
