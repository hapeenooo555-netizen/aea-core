# Render image-generation runtime dependency

AEA Core's Graphic Design generation pipeline runs local OCR through
`pytesseract` in `app/services/graphic_visual_inspector.py`. The Python
package is only a wrapper; the native Tesseract executable must also exist in
the Render service runtime. Without it, image inspection raises
`TesseractNotFoundError` and Graphic Design generation returns HTTP 502.

## Render service configuration

For the existing `aea-core` native Python web service:

- Keep the repository root directory as `backend/`.
- Change the Build Command to:

```bash
bash render-build.sh
```

- Keep the existing Start Command:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The build script installs Debian's `tesseract-ocr` package, verifies the
binary is available, then installs the existing Python requirements and
Uvicorn. It does not change API keys, Supabase settings, model selection,
budgets, or user data.

After the service redeploys, verify `tesseract --version` in the build log
and run one Graphic Design generation request. The image should only be marked
completed after OCR/visual inspection and private Supabase Storage upload
succeed.
