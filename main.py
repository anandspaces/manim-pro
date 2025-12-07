from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from typing import Optional, Dict
import logging
import uvicorn
import traceback
import subprocess
import uuid
import json
import os
from datetime import datetime
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# --- Config ---
MEDIA_ROOT = Path(__file__).resolve().parent / "media"
SCRIPTS_DIR = Path(__file__).resolve().parent / "scripts"
JOBS_DIR = Path(__file__).resolve().parent / "jobs"
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm"
}
LOG_LEVEL = "INFO"

# Get API configuration from environment variables
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-pro")  # Default to gemini-pro

# Create directories if they don't exist
SCRIPTS_DIR.mkdir(exist_ok=True)
JOBS_DIR.mkdir(exist_ok=True)

# --- Logging ---
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s"
)
logger = logging.getLogger("manim_ai_server")

# --- FastAPI app ---
app = FastAPI(title="ManimPro AI Animation Server", version="1.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# --- In-memory job store (use Redis/DB in production) ---
jobs_store: Dict[str, dict] = {}

# --- Pydantic Models ---
class AnimationRequest(BaseModel):
    topic: str
    description: Optional[str] = None

class VideoRequest(BaseModel):
    name: str

class JobStatusResponse(BaseModel):
    job_id: str
    status: str  # "pending", "generating_script", "rendering", "completed", "failed"
    message: str
    script: Optional[str] = None
    video_name: Optional[str] = None
    error: Optional[str] = None
    created_at: str
    updated_at: str

# --- Helper Functions ---
def safe_filename(name: str) -> str:
    """Basic safety: forbid path separators and require a filename only."""
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid filename (contains path separators).")
    return name

def find_video_file(filename: str) -> Optional[Path]:
    """Search MEDIA_ROOT recursively for a file matching filename (case-insensitive)."""
    filename_lower = filename.lower()
    
    if not MEDIA_ROOT.exists():
        return None
    
    for p in MEDIA_ROOT.rglob("*"):
        if p.is_file() and p.name.lower() == filename_lower:
            return p
    
    return None

def sanitize_class_name(topic: str) -> str:
    """Convert topic to valid Python class name."""
    # Remove special characters and convert to PascalCase
    words = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in topic).split()
    class_name = ''.join(word.capitalize() for word in words)
    
    if not class_name:
        class_name = "Animation"
    elif class_name[0].isdigit():
        class_name = "Anim" + class_name
        
    return class_name + "Scene"

def generate_script_with_gemini(topic: str, description: Optional[str]) -> str:
    """Generate Manim animation script using Gemini API."""
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in environment variables")
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        class_name = sanitize_class_name(topic)
        
        prompt = f"""You are an expert Manim animation developer. Generate a complete, working Manim script for the following:

Topic: {topic}
{f'Description: {description}' if description else ''}

Requirements:
1. Use the class name: {class_name}
2. The script must be complete and executable
3. Use modern Manim syntax (from manim import *)
4. Include interesting visual effects and animations
5. Make it educational and visually appealing
6. Use 3D scenes if appropriate (ThreeDScene)
7. Add smooth transitions and camera movements
8. Duration should be 8-12 seconds
9. Use appropriate colors and styling
10. Include necessary imports (numpy, etc.)

CRITICAL SYNTAX RULES:
- NEVER use RunAnimation() - it's deprecated
- Pass animations directly to self.play(): self.play(Create(obj), Write(text))
- For 3D scenes, use self.move_camera() or self.begin_ambient_camera_rotation()
- Always test that your code would run without errors
- Use only documented Manim methods and classes

IMPORTANT: 
- Return ONLY the Python code, no explanations
- Do not include markdown code blocks (no ```python```)
- Start directly with imports
- Make it production-ready
- Test syntax mentally before generating

Example structure:
from manim import *
import numpy as np

class {class_name}(Scene):  # or ThreeDScene
    def construct(self):
        # Create objects
        circle = Circle()
        text = Text("Hello")
        
        # Animate them (NO RunAnimation!)
        self.play(Create(circle), Write(text))
        self.wait(1)
"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Clean up markdown code blocks if present
        if script.startswith("```python"):
            script = script.replace("```python", "").replace("```", "").strip()
        elif script.startswith("```"):
            script = script.replace("```", "").strip()
        
        return script
        
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        raise Exception(f"Failed to generate script: {str(e)}")

def render_animation(job_id: str, script_path: Path, class_name: str):
    """Render Manim animation in background."""
    try:
        logger.info(f"[{job_id}] Starting render for {class_name}")
        
        # Update job status
        jobs_store[job_id]["status"] = "rendering"
        jobs_store[job_id]["message"] = "Rendering animation..."
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        
        # Save job to disk
        save_job_to_disk(job_id)
        
        # Run manim command
        cmd = [
            "manim",
            "-pql",  # preview quality low (faster)
            str(script_path),
            class_name
        ]
        
        logger.info(f"[{job_id}] Running command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown rendering error"
            logger.error(f"[{job_id}] Render failed: {error_msg}")
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["message"] = "Rendering failed"
            jobs_store[job_id]["error"] = error_msg
            jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
            save_job_to_disk(job_id)
            return
        
        # Find generated video
        video_name = f"{class_name}.mp4"
        video_path = find_video_file(video_name)
        
        if not video_path:
            logger.error(f"[{job_id}] Video file not found: {video_name}")
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["message"] = "Video file not found after rendering"
            jobs_store[job_id]["error"] = f"Expected video: {video_name}"
            jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
            save_job_to_disk(job_id)
            return
        
        # Success!
        logger.info(f"[{job_id}] Render completed successfully: {video_name}")
        jobs_store[job_id]["status"] = "completed"
        jobs_store[job_id]["message"] = "Animation completed successfully"
        jobs_store[job_id]["video_name"] = video_name
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)
        
    except subprocess.TimeoutExpired:
        logger.error(f"[{job_id}] Render timeout")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["message"] = "Rendering timeout"
        jobs_store[job_id]["error"] = "Render took too long (>5 minutes)"
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)
        
    except Exception as e:
        logger.exception(f"[{job_id}] Unexpected error during render")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["message"] = "Rendering failed"
        jobs_store[job_id]["error"] = str(e)
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)

def save_job_to_disk(job_id: str):
    """Persist job data to disk."""
    try:
        job_file = JOBS_DIR / f"{job_id}.json"
        with open(job_file, 'w') as f:
            json.dump(jobs_store[job_id], f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save job {job_id}: {e}")

def load_jobs_from_disk():
    """Load all jobs from disk on startup."""
    try:
        for job_file in JOBS_DIR.glob("*.json"):
            with open(job_file, 'r') as f:
                job_data = json.load(f)
                job_id = job_file.stem
                jobs_store[job_id] = job_data
        logger.info(f"Loaded {len(jobs_store)} jobs from disk")
    except Exception as e:
        logger.error(f"Failed to load jobs: {e}")

# --- API Endpoints ---

@app.post("/generate_animation")
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
        
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        logger.info(f"[{job_id}] New animation request - Topic: {req.topic}")
        
        # Initialize job
        jobs_store[job_id] = {
            "job_id": job_id,
            "status": "generating_script",
            "message": "Generating animation script with AI...",
            "topic": req.topic,
            "description": req.description,
            "script": None,
            "video_name": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        save_job_to_disk(job_id)
        
        # Generate script with Gemini
        try:
            script = generate_script_with_gemini(req.topic, req.description)
            class_name = sanitize_class_name(req.topic)
            
            # Save script
            script_filename = f"{job_id}_{class_name}.py"
            script_path = SCRIPTS_DIR / script_filename
            
            with open(script_path, 'w') as f:
                f.write(script)
            
            logger.info(f"[{job_id}] Script generated: {script_filename}")
            
            # Update job with script
            jobs_store[job_id]["script"] = script
            jobs_store[job_id]["script_path"] = str(script_path)
            jobs_store[job_id]["class_name"] = class_name
            jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
            save_job_to_disk(job_id)
            
            # Start background rendering
            background_tasks.add_task(render_animation, job_id, script_path, class_name)
            
            return {
                "job_id": job_id,
                "status": "pending",
                "message": "Animation generation started",
                "script": script
            }
            
        except Exception as e:
            logger.error(f"[{job_id}] Script generation failed: {e}")
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["message"] = "Script generation failed"
            jobs_store[job_id]["error"] = str(e)
            jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
            save_job_to_disk(job_id)
            raise HTTPException(status_code=500, detail=str(e))
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error in /generate_animation")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/job_status/{job_id}")
async def get_job_status(job_id: str):
    """
    Get status of animation generation job.
    Returns current status, script, and video name if completed.
    """
    if job_id not in jobs_store:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_store[job_id]
    
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

@app.post("/get_video")
async def get_video(req: VideoRequest, request: Request):
    """
    Get video file by name.
    """
    client_host = request.client.host if request.client else "unknown"
    logger.info(f"Request from {client_host} - get_video name={req.name}")
    
    try:
        name = safe_filename(req.name.strip())
        
        # Auto-add .mp4 extension if not provided
        suffix = Path(name).suffix.lower()
        if not suffix:
            name = f"{name}.mp4"
            suffix = ".mp4"
        
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail="Unsupported file extension")

        file_path = find_video_file(name)
        if not file_path:
            raise HTTPException(status_code=404, detail="File not found")

        # Security check
        try:
            file_resolved = file_path.resolve()
            file_resolved.relative_to(MEDIA_ROOT.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        media_type = MEDIA_TYPES.get(suffix, "application/octet-stream")
        
        logger.info(f"✓ Serving file: {file_path.relative_to(MEDIA_ROOT)}")
        
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

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "media_root": str(MEDIA_ROOT),
        "media_root_exists": MEDIA_ROOT.exists(),
        "scripts_dir": str(SCRIPTS_DIR),
        "jobs_dir": str(JOBS_DIR),
        "active_jobs": len(jobs_store),
        "gemini_api_key_configured": bool(GEMINI_API_KEY),
        "gemini_model": GEMINI_MODEL
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
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/list_jobs")
async def list_jobs():
    """List all jobs"""
    return {
        "count": len(jobs_store),
        "jobs": [
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "topic": job.get("topic"),
                "created_at": job["created_at"],
                "video_name": job.get("video_name")
            }
            for job in jobs_store.values()
        ]
    }

@app.post("/retry_job/{job_id}")
async def retry_job(job_id: str, background_tasks: BackgroundTasks):
    """
    Retry a failed job by regenerating the script and rendering again.
    """
    if job_id not in jobs_store:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs_store[job_id]
    
    if job["status"] not in ["failed"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot retry job with status: {job['status']}. Only failed jobs can be retried."
        )
    
    try:
        logger.info(f"[{job_id}] Retrying failed job - Topic: {job['topic']}")
        
        # Reset job status
        jobs_store[job_id]["status"] = "generating_script"
        jobs_store[job_id]["message"] = "Regenerating animation script with AI..."
        jobs_store[job_id]["error"] = None
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)
        
        # Generate new script with improved prompt
        script = generate_script_with_gemini(job["topic"], job.get("description"))
        class_name = sanitize_class_name(job["topic"])
        
        # Save new script
        script_filename = f"{job_id}_{class_name}.py"
        script_path = SCRIPTS_DIR / script_filename
        
        with open(script_path, 'w') as f:
            f.write(script)
        
        logger.info(f"[{job_id}] New script generated: {script_filename}")
        
        # Update job with new script
        jobs_store[job_id]["script"] = script
        jobs_store[job_id]["script_path"] = str(script_path)
        jobs_store[job_id]["class_name"] = class_name
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)
        
        # Start background rendering
        background_tasks.add_task(render_animation, job_id, script_path, class_name)
        
        return {
            "job_id": job_id,
            "status": "pending",
            "message": "Job retry started with new script",
            "script": script
        }
        
    except Exception as e:
        logger.error(f"[{job_id}] Retry failed: {e}")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["message"] = "Retry failed"
        jobs_store[job_id]["error"] = str(e)
        jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
        save_job_to_disk(job_id)
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    """Verify configuration on startup"""
    logger.info("="*70)
    logger.info("MANIM AI ANIMATION SERVER STARTUP")
    logger.info("="*70)
    logger.info(f"MEDIA_ROOT: {MEDIA_ROOT}")
    logger.info(f"SCRIPTS_DIR: {SCRIPTS_DIR}")
    logger.info(f"JOBS_DIR: {JOBS_DIR}")
    logger.info(f"GEMINI_MODEL: {GEMINI_MODEL}")
    logger.info(f"GEMINI_API_KEY: {'✓ Configured' if GEMINI_API_KEY else '✗ Not set'}")
    
    if not GEMINI_API_KEY:
        logger.warning("⚠️  GEMINI_API_KEY not configured! Set it in .env file")
    
    # Create directories
    MEDIA_ROOT.mkdir(exist_ok=True)
    SCRIPTS_DIR.mkdir(exist_ok=True)
    JOBS_DIR.mkdir(exist_ok=True)
    
    # Load existing jobs
    load_jobs_from_disk()
    
    logger.info("="*70)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")