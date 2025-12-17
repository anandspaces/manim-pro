import logging
from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse
from src.schemas import (
    AnimationRequest, JobStatusResponse,
    CacheCheckRequest, CacheCheckResponse
)
from src.helper_service import (
    create_animation_job, render_animation, get_job, 
    get_all_jobs, retry_job, find_video_file,
)
from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL, MEDIA_ROOT, 
    SCRIPTS_DIR, JOBS_DIR, ALLOWED_EXTENSIONS, MEDIA_TYPES
)
from src.redis_client import redis_client

logger = logging.getLogger("manim_ai_server")
router = APIRouter()

# --- Animation Generation Endpoints ---

@router.post("/generate_animation")
async def generate_animation(req: AnimationRequest, background_tasks: BackgroundTasks, request: Request):
    """
    Generate animation from topic using Gemini AI.
    Checks database for existing animation first (caching).
    Returns job_id for status tracking.
    """
    from src.database import animation_db
    
    try:
        # Step 0: Check if animation already exists in database
        logger.info(f"\n=== Animation generation request ===")
        logger.info(f"Topic: {req.topic}, Level: {req.level}")
        logger.info(f"Subject ID: {req.subject_id}, Chapter ID: {req.chapter_id}, Topic ID: {req.topic_id}")
        logger.info("Step 0: Checking for existing animation in database...")
        
        existing = animation_db.check_existing_animation(
            level=req.level,
            subject_id=req.subject_id,
            chapter_id=req.chapter_id,
            topic_id=req.topic_id
        )
        
        if existing:
            job_id = existing['job_id']
            logger.info(f"✓ Found existing animation! Returning cached job_id: {job_id}")
            
            # Get job details from Redis
            job = get_job(job_id)
            if not job:
                # If not in Redis, reconstruct from database
                logger.info(f"Job {job_id} not in Redis, reconstructing from database...")
                job = {
                    "job_id": job_id,
                    "status": existing["status"],
                    "message": "Animation retrieved from cache",
                    "video_name": existing.get("video_name"),
                    "topic": existing.get("topic_name"),
                    "created_at": existing["created_at"],
                    "updated_at": existing["updated_at"]
                }
            
            # Get video URL for cached animation
            video_url = None
            video_name = job.get("video_name")
            
            if video_name:
                # Find video file
                file_path = find_video_file(video_name)
                if file_path:
                    try:
                        # Security check
                        file_resolved = file_path.resolve()
                        file_resolved.relative_to(MEDIA_ROOT.resolve())
                        
                        # Get relative path from MEDIA_ROOT
                        relative_path = file_path.relative_to(MEDIA_ROOT)
                        
                        # Construct URL
                        base_url = f"{request.url.scheme}://{request.url.netloc}"
                        video_url = f"{base_url}/media/{relative_path}"
                        
                        logger.info(f"✓ Generated video URL for cached animation: {video_url}")
                    except ValueError:
                        logger.error(f"Security check failed for video file: {video_name}")
                    except Exception as e:
                        logger.error(f"Error generating video URL: {e}")
                else:
                    logger.warning(f"Video file not found for cached animation: {video_name}")
            
            return {
                "job_id": job_id,
                "status": job["status"],
                "message": "Animation retrieved from cache",
                "cached": True,
                "video_name": video_name,
                "video_url": video_url,
                "script": job.get("script"),
                "topic": job.get("topic")
            }
        
        logger.info("No existing animation found. Proceeding with generation...")
        
        # Check if API key is configured
        if not GEMINI_API_KEY:
            raise HTTPException(
                status_code=500, 
                detail="GEMINI_API_KEY not configured. Please set it in .env file"
            )
        
        # Check Redis connection
        if not redis_client.ping():
            raise HTTPException(
                status_code=503,
                detail="Redis connection unavailable"
            )
        
        # Create job and generate script
        try:
            result = create_animation_job(
                topic=req.topic,
                topic_id=req.topic_id,
                subject=req.subject,
                subject_id=req.subject_id,
                chapter=req.chapter,
                chapter_id=req.chapter_id,
                level=req.level
            )
            
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
                "cached": False,
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
        # Try to get from database if not in Redis
        from src.database import animation_db
        db_animation = animation_db.get_animation_by_job_id(job_id)
        
        if db_animation:
            # Reconstruct job response from database
            return JobStatusResponse(
                job_id=db_animation["job_id"],
                status=db_animation["status"],
                message=f"Animation {db_animation['status']}",
                script=None,  # Script not stored in DB
                video_name=db_animation.get("video_name"),
                error=None,
                created_at=db_animation["created_at"],
                updated_at=db_animation["updated_at"]
            )
        
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        message=job["message"],
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
    Get video URL by job ID.
    Returns the URL to access the video file.
    """
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"Request from {client_host} - get_video job_id={job_id}")
    
    try:
        # Get job
        job = get_job(job_id)
        if not job:
            # Try database
            from src.database import animation_db
            db_animation = animation_db.get_animation_by_job_id(job_id)
            if db_animation and db_animation["status"] == "completed":
                video_name = db_animation.get("video_name")
            else:
                raise HTTPException(status_code=404, detail="Job not found")
        else:
            # Check if job is completed
            if job["status"] != "completed":
                raise HTTPException(
                    status_code=400, 
                    detail=f"Video not ready. Job status: {job['status']}"
                )
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

        # Get relative path from MEDIA_ROOT
        relative_path = file_path.relative_to(MEDIA_ROOT)
        
        # Construct URL
        # Get the base URL from the request
        base_url = f"{request.url.scheme}://{request.url.netloc}"
        video_url = f"{base_url}/media/{relative_path}"
        
        logger.info(f"✓ Generated video URL for job {job_id}: {video_url}")
        
        return {
            "job_id": job_id,
            "video_name": video_name,
            "video_url": video_url,
            "status": "completed"
        }
        
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
            # Try database
            from src.database import animation_db
            db_animation = animation_db.get_animation_by_job_id(job_id)
            if db_animation and db_animation["status"] == "completed":
                video_name = db_animation.get("video_name")
            else:
                raise HTTPException(status_code=404, detail="Job not found")
        else:
            # Check if job is completed
            if job["status"] != "completed":
                raise HTTPException(
                    status_code=400, 
                    detail=f"Video not ready. Job status: {job['status']}"
                )
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
    """List all jobs from Redis"""
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

# --- Database-specific Endpoints ---

@router.get("/check_animation")
async def check_animation(level: int, subjectId: int, chapterId: int, topicId: int):
    """
    Check if animation exists in database for given parameters.
    Returns existing job_id if found.
    """
    from src.database import animation_db
    
    try:
        logger.info(f"Checking for animation: level={level}, subjectId={subjectId}, chapterId={chapterId}, topicId={topicId}")
        
        existing = animation_db.check_existing_animation(
            level=level,
            subject_id=subjectId,
            chapter_id=chapterId,
            topic_id=topicId
        )
        
        if existing:
            return {
                "exists": True,
                "job_id": existing["job_id"],
                "video_name": existing.get("video_name"),
                "status": existing["status"],
                "created_at": existing["created_at"],
                "topic": existing.get("topic_name")
            }
        else:
            return {
                "exists": False,
                "job_id": None
            }
            
    except Exception as e:
        logger.error(f"Error checking animation: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/database_stats")
async def database_stats():
    """Get database statistics"""
    from src.database import animation_db
    
    try:
        stats = animation_db.get_stats()
        return stats
    except Exception as e:
        logger.error(f"Error getting database stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list_cached_animations")
async def list_cached_animations(limit: int = 50):
    """List all cached animations from database"""
    from src.database import animation_db
    
    try:
        animations = animation_db.get_all_animations(limit=limit)
        return {
            "count": len(animations),
            "animations": animations
        }
    except Exception as e:
        logger.error(f"Error listing cached animations: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/check_cache")
async def check_animation_cache(req: CacheCheckRequest):
    """
    Check if animation exists in cache.
    
    Returns:
        - cached: True if animation exists and is completed
        - job_id: Job ID if cached
        - video_name: Video filename if available
        - status: Animation status
        - message: Descriptive message
    """
    from src.database import animation_db
    
    try:
        logger.info(
            f"Cache check request - Level: {req.level}, "
            f"Subject ID: {req.subject_id}, Chapter ID: {req.chapter_id}, "
            f"Topic ID: {req.topic_id}"
        )
        
        # Check if animation exists in database
        existing = animation_db.check_existing_animation(
            level=req.level,
            subject_id=req.subject_id,
            chapter_id=req.chapter_id,
            topic_id=req.topic_id
        )
        
        if existing:
            logger.info(
                f"✓ Cache HIT - Job ID: {existing['job_id']}, "
                f"Status: {existing['status']}"
            )
            
            return CacheCheckResponse(
                cached=True,
                job_id=existing["job_id"],
                video_name=existing.get("video_name"),
                status=existing["status"],
                created_at=existing["created_at"],
                message=f"Animation found in cache for '{req.topic}'"
            )
        else:
            logger.info(
                f"✗ Cache MISS - No animation found for Level {req.level}, "
                f"Subject {req.subject_id}, Chapter {req.chapter_id}, "
                f"Topic {req.topic_id}"
            )
            
            return CacheCheckResponse(
                cached=False,
                message=f"No cached animation found for '{req.topic}'. New generation required."
            )
            
    except Exception as e:
        logger.error(f"Error checking animation cache: {e}")
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to check cache: {str(e)}"
        )

# --- Health Check ---

@router.get("/health")
async def health():
    """Health check endpoint"""
    redis_stats = redis_client.get_stats()
    
    return {
        "status": "ok",
        "media_root": str(MEDIA_ROOT),
        "media_root_exists": MEDIA_ROOT.exists(),
        "scripts_dir": str(SCRIPTS_DIR),
        "jobs_dir": str(JOBS_DIR),
        "active_jobs": len(get_all_jobs()),
        "gemini_api_key_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL,
        "redis": redis_stats
    }