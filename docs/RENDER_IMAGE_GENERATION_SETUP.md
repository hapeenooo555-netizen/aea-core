# Render image-generation runtime dependency

AEA Core's Graphic Design pipeline uses local OCR through `pytesseract` in
`app/services/graphic_visual_inspector.py`. The Python package is a wrapper;
the native Tesseract executable must also be present. The native Python Render
build attempt failed because its build environment rejected writes to
`/var/lib/apt/lists` (read-only filesystem).

## Render configuration

Use the Docker runtime for the existing `aea-core` web service so OS-level
packages are installed inside the image at build time.

In Render Dashboard > `aea-core` > Settings > Build > Source > Edit:

- Runtime: **Docker**
- Dockerfile Path: `backend/Dockerfile`
- Docker Context: `backend` (if the field is shown)
- Keep repository: `hapeenooo555-netizen/aea-core`
- Keep branch: `main`

The Dockerfile installs Debian's `tesseract-ocr` package, installs the
existing Python requirements and Uvicorn, and starts the existing FastAPI
application using Render's `PORT` environment variable.

Do not change environment variables, credentials, Supabase configuration,
Fal.ai settings, budgets, or the service URL. The runtime change should be
made through Render's Source editor; do not replace the service with a new
service unless Render explicitly requires it.

## Verification

1. Confirm the Docker image build succeeds and the service becomes Live.
2. Check runtime logs for successful FastAPI startup.
3. Verify the OCR binary in a controlled runtime check with
   `tesseract --version`.
4. Run one authenticated Graphic Design generation request.
5. Confirm visual inspection passes and the generated image is stored in
   private Supabase Storage before the job is marked completed.

The previous native-runtime `render-build.sh` workaround is removed because
APT package installation is not supported in that build environment.
