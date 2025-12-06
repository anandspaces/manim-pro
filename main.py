# main.py (in project root: ~/yoyoPro/ManimPro/main.py)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from typing import Optional
import logging
import uvicorn
import traceback

# --- Config ---
# Since this file is at ~/yoyoPro/ManimPro/main.py
# MEDIA_ROOT should be: ~/yoyoPro/ManimPro/media
MEDIA_ROOT = Path(__file__).resolve().parent / "media"  # Changed from parent.parent
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm"
}
LOG_LEVEL = "INFO"

# --- Logging ---
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger("video_server")

# --- FastAPI app ---
app = FastAPI(title="ManimPro Video Server", version="0.1")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict this in production
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

class VideoRequest(BaseModel):
    name: str  # e.g. "MinimalSurfaceScene.mp4"

def safe_filename(name: str) -> str:
    """Basic safety: forbid path separators and require a filename only."""
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid filename (contains path separators).")
    return name

def find_video_file(filename: str) -> Optional[Path]:
    """
    Search MEDIA_ROOT recursively for a file matching filename (case-insensitive).
    Returns first match Path or None.
    """
    filename_lower = filename.lower()
    
    logger.info(f"=== Starting search for '{filename}' ===")
    logger.info(f"MEDIA_ROOT: {MEDIA_ROOT}")
    logger.info(f"MEDIA_ROOT exists: {MEDIA_ROOT.exists()}")
    logger.info(f"MEDIA_ROOT is_dir: {MEDIA_ROOT.is_dir()}")
    
    if not MEDIA_ROOT.exists():
        logger.error("MEDIA_ROOT does not exist!")
        return None
    
    # Show what we're searching through
    all_mp4 = list(MEDIA_ROOT.rglob("*.mp4"))
    logger.info(f"Total .mp4 files found: {len(all_mp4)}")
    
    if all_mp4:
        logger.info("Available video files:")
        for p in all_mp4[:10]:  # Show first 10
            logger.info(f"  - {p.name} ({p.relative_to(MEDIA_ROOT)})")
    
    # Case-insensitive search
    logger.info(f"Searching for filename (case-insensitive): {filename_lower}")
    for p in MEDIA_ROOT.rglob("*"):
        if p.is_file() and p.name.lower() == filename_lower:
            logger.info(f"✓ MATCH FOUND: {p}")
            return p
    
    logger.warning(f"✗ No match found for '{filename}'")
    return None

@app.post("/get_video")
async def get_video(req: VideoRequest, request: Request):
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"\n{'='*60}")
    logger.info(f"Request from {client_host} - get_video name={req.name}")
    logger.info(f"{'='*60}")
    
    try:
        # Sanitize name
        name = safe_filename(req.name.strip())
        
        # Auto-add .mp4 extension if not provided
        suffix = Path(name).suffix.lower()
        if not suffix:
            name = f"{name}.mp4"
            suffix = ".mp4"
            logger.info(f"No extension provided, using: {name}")
        
        if suffix not in ALLOWED_EXTENSIONS:
            logger.warning(f"Rejected request for disallowed extension: {name}")
            raise HTTPException(status_code=400, detail="Unsupported file extension")

        # Find file under media/
        file_path = find_video_file(name)
        if not file_path:
            logger.info(f"File not found: {name}")
            raise HTTPException(status_code=404, detail="File not found")

        # Security: verify the resolved path is inside MEDIA_ROOT
        try:
            file_resolved = file_path.resolve()
            file_resolved.relative_to(MEDIA_ROOT.resolve())
        except ValueError:
            logger.error(f"Security: file outside media root: {file_resolved}")
            raise HTTPException(status_code=403, detail="Access denied")
        except Exception as e:
            logger.exception("Error resolving file path")
            raise HTTPException(status_code=500, detail="Server error")

        # Determine correct media type
        media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
        
        logger.info(f"✓ Serving file: {file_path.relative_to(MEDIA_ROOT)}")
        logger.info(f"  File size: {file_path.stat().st_size / (1024*1024):.2f} MB")
        logger.info(f"  Media type: {media_type}")
        
        # Stream the file back
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=media_type
        )
        
    except HTTPException:
        raise
    except ValueError as ve:
        logger.warning(f"Bad request: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error("Unhandled error in /get_video:\n" + traceback.format_exc())
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"}
        )

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "media_root": str(MEDIA_ROOT),
        "media_root_exists": MEDIA_ROOT.exists(),
        "media_root_is_dir": MEDIA_ROOT.is_dir()
    }

@app.get("/list_videos")
async def list_videos():
    """List all available video files"""
    try:
        videos = []
        for ext in ALLOWED_EXTENSIONS:
            for p in MEDIA_ROOT.rglob(f"*{ext}"):
                if p.is_file():
                    videos.append({
                        "name": p.name,
                        "path": str(p.relative_to(MEDIA_ROOT)),
                        "size_mb": round(p.stat().st_size / (1024 * 1024), 2)
                    })
        
        return {
            "count": len(videos),
            "media_root": str(MEDIA_ROOT),
            "videos": sorted(videos, key=lambda x: x["name"])
        }
    except Exception as e:
        logger.exception("Error listing videos")
        return JSONResponse(
            status_code=500,
            content={"detail": str(e)}
        )

@app.on_event("startup")
async def startup_event():
    """Verify configuration on startup"""
    logger.info("="*70)
    logger.info("VIDEO SERVER STARTUP")
    logger.info("="*70)
    logger.info(f"MEDIA_ROOT: {MEDIA_ROOT}")
    logger.info(f"MEDIA_ROOT exists: {MEDIA_ROOT.exists()}")
    logger.info(f"MEDIA_ROOT is directory: {MEDIA_ROOT.is_dir()}")
    
    if not MEDIA_ROOT.exists():
        logger.error("⚠️  MEDIA_ROOT does not exist!")
        logger.error("Please create the media directory or check configuration")
    else:
        # Count videos
        video_count = sum(1 for ext in ALLOWED_EXTENSIONS 
                         for _ in MEDIA_ROOT.rglob(f"*{ext}"))
        logger.info(f"Found {video_count} video files")
        
        if video_count == 0:
            logger.warning("⚠️  No video files found in MEDIA_ROOT")
        else:
            # Show some example files
            logger.info("Sample video files:")
            shown = 0
            for ext in ALLOWED_EXTENSIONS:
                for p in MEDIA_ROOT.rglob(f"*{ext}"):
                    if shown < 5:
                        logger.info(f"  - {p.name}")
                        shown += 1
                    else:
                        break
                if shown >= 5:
                    break
    
    logger.info("="*70)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")