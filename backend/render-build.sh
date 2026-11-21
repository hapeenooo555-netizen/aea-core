#!/usr/bin/env bash
# Render native Python build: install the local OCR engine required by
# graphic_visual_inspector, then install the API dependencies.
set -euo pipefail

if ! command -v tesseract >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tesseract-ocr
  rm -rf /var/lib/apt/lists/*
fi

tesseract --version
python -m pip install -r requirements.txt
python -m pip install "uvicorn[standard]>=0.29,<1.0"
