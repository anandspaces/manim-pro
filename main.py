# src/video_server.py
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pathlib import Path
import logging
import uvicorn
import traceback

# --- Config ---
MEDIA_ROOT = Path(__file__).resolve().parent.parent / "media"   # ~/yoyoPro/ManimPro/media
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
LOG_LEVEL = "INFO"

# --- Logging ---
logger = logging.getLogger("video_server")
logger.setLevel(LOG_LEVEL)
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
)
logger.addHandler(handler)

# --- FastAPI app ---
app = FastAPI(title="ManimPro Video Server", version="0.1")

class VideoRequest(BaseModel):
    name: str  # e.g. "MinimalSurfaceScene.mp4"

def safe_filename(name: str) -> str:
    """
    Basic safety: forbid path separators and require a filename only.
    """
    if "/" in name or "\\" in name:
        raise ValueError("Invalid filename (contains path separators).")
    return name

def find_video_file(filename: str) -> Path | None:
    """Search MEDIA_ROOT recursively for a file matching filename.
    Returns first match Path or None."""
    for p in MEDIA_ROOT.rglob(filename):
        if p.is_file():
            return p
    return None

@app.post("/get_video")
async def get_video(req: VideoRequest, request: Request):
    logger.info(f"Request from {request.client.host if request.client else 'unknown'} - get_video name={req.name}")
    try:
        # sanitize name
        name = safe_filename(req.name.strip())
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            logger.warning(f"Rejected request for disallowed extension: {name}")
            raise HTTPException(status_code=400, detail="Unsupported file extension")

        # find file under media/
        file_path = find_video_file(name)
        if not file_path:
            logger.info(f"File not found: {name}")
            raise HTTPException(status_code=404, detail="File not found")

        # Double-check the resolved parent is inside MEDIA_ROOT (safety)
        try:
            file_resolved = file_path.resolve()
            if MEDIA_ROOT.resolve() not in file_resolved.parents and file_resolved != MEDIA_ROOT.resolve():
                logger.error(f"Security: file outside media root: {file_resolved}")
                raise HTTPException(status_code=403, detail="Access denied")
        except Exception as e:
            logger.exception("Error resolving file path")
            raise HTTPException(status_code=500, detail="Server error")

        logger.info(f"Serving file: {file_path}")
        # Stream the file back (FileResponse handles range headers, content-type)
        return FileResponse(path=str(file_path), filename=file_path.name, media_type="video/mp4")
    except HTTPException as he:
        # re-raise (FastAPI will handle)
        raise he
    except ValueError as ve:
        logger.warning(f"Bad request: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error("Unhandled error in /get_video:\n" + traceback.format_exc())
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

@app.get("/health")
async def health():
    return {"status": "ok"}
