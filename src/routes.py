import logging
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse
from pathlib import Path

from src.schemas import (
    AnimationRequest, JobStatusResponse, 
    VideoListItem, JobListItem
)
from src.service import (
    create_animation_job, render_animation, get_job, 
    get_all_jobs, retry_job, find_video_file,
    safe_filename, load_jobs_from_disk
)
from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL, MEDIA_ROOT, 
    SCRIPTS_DIR, JOBS_DIR, ALLOWED_EXTENSIONS, MEDIA_TYPES
)

logger = logging.getLogger("manim_ai_server")
router = APIRouter()

# --- Animation Generation Endpoints ---

@router.post("/generate_animation")
async def generate_animation(req: AnimationRequest, background_tasks: BackgroundTasks):
    """
    Generate animation from topic using Gemini AI.
    Returns job_id for status tracking.
    """
    try:
        # Check if API key is configured
        if not GEMINI_API_KEY:
            raise HTTPException(
                status_code=500, 
                detail="GEMINI_API_KEY not configured. Please set it in .env file"
            )
        
        # Create job and generate script
        try:
            result = create_animation_job(req.topic, req.description)
            
            # Start background rendering
            background_tasks.add_task(
                render_animation, 
                result["job_id"], 
                result["script_path"], 
                result["class_name"]
            )
            
            return {
                "job_id": result["job_id"],
                "status": "pending",
                "message": "Animation generation started",
                "script": result["script"]
            }
            
        except Exception as e:
            logger.error(f"Script generation failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /generate_animation")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/job_status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get status of animation generation job.
    Returns current status, script, and video name if completed.
    """
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        message=job["message"],
        script=job.get("script"),
        video_name=job.get("video_name"),
        error=job.get("error"),
        created_at=job["created_at"],
        updated_at=job["updated_at"]
    )

@router.post("/retry_job/{job_id}")
async def retry_job_endpoint(job_id: str, background_tasks: BackgroundTasks):
    """
    Retry a failed job by regenerating the script and rendering again.
    """
    try:
        result = retry_job(job_id)
        
        # Start background rendering
        background_tasks.add_task(
            render_animation, 
            result["job_id"], 
            result["script_path"], 
            result["class_name"]
        )
        
        return {
            "job_id": result["job_id"],
            "status": "pending",
            "message": "Job retry started with new script",
            "script": result["script"]
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Retry failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Video Endpoints ---

@router.get("/get_video/{job_id}")
async def get_video_by_job(job_id: str, request: Request):
    """
    Get video file by job ID.
    This retrieves the completed animation for a specific job.
    """
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"Request from {client_host} - get_video job_id={job_id}")
    
    try:
        # Get job
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Check if job is completed
        if job["status"] != "completed":
            raise HTTPException(
                status_code=400, 
                detail=f"Video not ready. Job status: {job['status']}"
            )
        
        # Get video name from job
        video_name = job.get("video_name")
        if not video_name:
            raise HTTPException(
                status_code=404, 
                detail="Video file not associated with this job"
            )
        
        # Find video file
        file_path = find_video_file(video_name)
        if not file_path:
            raise HTTPException(
                status_code=404, 
                detail=f"Video file not found: {video_name}"
            )

        # Security check
        try:
            file_resolved = file_path.resolve()
            file_resolved.relative_to(MEDIA_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        suffix = file_path.suffix.lower()
        media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
        
        logger.info(f"✓ Serving file for job {job_id}: {file_path.relative_to(MEDIA_ROOT)}")
        
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=media_type
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /get_video")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/download_video/{job_id}")
async def download_video(job_id: str, request: Request):
    """
    Download video file by job ID with Content-Disposition header.
    Forces browser to download instead of playing inline.
    """
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"Request from {client_host} - download_video job_id={job_id}")
    
    try:
        # Get job
        job = get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        
        # Check if job is completed
        if job["status"] != "completed":
            raise HTTPException(
                status_code=400, 
                detail=f"Video not ready. Job status: {job['status']}"
            )
        
        # Get video name from job
        video_name = job.get("video_name")
        if not video_name:
            raise HTTPException(
                status_code=404, 
                detail="Video file not associated with this job"
            )
        
        # Find video file
        file_path = find_video_file(video_name)
        if not file_path:
            raise HTTPException(
                status_code=404, 
                detail=f"Video file not found: {video_name}"
            )

        # Security check
        try:
            file_resolved = file_path.resolve()
            file_resolved.relative_to(MEDIA_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        suffix = file_path.suffix.lower()
        media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
        
        logger.info(f"✓ Downloading file for job {job_id}: {file_path.relative_to(MEDIA_ROOT)}")
        
        return FileResponse(
            path=str(file_path),
            filename=file_path.name,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={file_path.name}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /download_video")
        raise HTTPException(status_code=500, detail=str(e))

# --- List Endpoints ---

@router.get("/list_videos")
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
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list_jobs")
async def list_jobs():
    """List all jobs"""
    jobs = get_all_jobs()
    return {
        "count": len(jobs),
        "jobs": [
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "topic": job.get("topic"),
                "created_at": job["created_at"],
                "video_name": job.get("video_name")
            }
            for job in jobs
        ]
    }

# --- Health Check ---

@router.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "media_root": str(MEDIA_ROOT),
        "media_root_exists": MEDIA_ROOT.exists(),
        "scripts_dir": str(SCRIPTS_DIR),
        "jobs_dir": str(JOBS_DIR),
        "active_jobs": len(get_all_jobs()),
        "gemini_api_key_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL
    }

# --- Startup Event ---

@router.on_event("startup")
async def startup_event():
    """Load jobs from disk on startup"""
    load_jobs_from_disk()