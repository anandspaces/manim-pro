import logging
import subprocess
import json
import uuid
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
import google.generativeai as genai

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL, SCRIPTS_DIR, 
    JOBS_DIR, MEDIA_ROOT, RENDER_TIMEOUT, RENDER_QUALITY
)

logger = logging.getLogger("manim_ai_server")

# In-memory job store (use Redis/DB in production)
jobs_store: Dict[str, dict] = {}

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
    words = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in topic).split()
    class_name = ''.join(word.capitalize() for word in words)
    
    if not class_name:
        class_name = "Animation"
    elif class_name[0].isdigit():
        class_name = "Anim" + class_name
        
    return class_name + "Scene"

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

# --- Core Service Functions ---

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
        save_job_to_disk(job_id)
        
        # Run manim command
        cmd = [
            "manim",
            RENDER_QUALITY,
            str(script_path),
            class_name
        ]
        
        logger.info(f"[{job_id}] Running command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT
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

def create_animation_job(topic: str, description: Optional[str]) -> dict:
    """Create a new animation job and generate script."""
    job_id = str(uuid.uuid4())
    
    logger.info(f"[{job_id}] New animation request - Topic: {topic}")
    
    # Initialize job
    jobs_store[job_id] = {
        "job_id": job_id,
        "status": "generating_script",
        "message": "Generating animation script with AI...",
        "topic": topic,
        "description": description,
        "script": None,
        "video_name": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
    save_job_to_disk(job_id)
    
    # Generate script with Gemini
    script = generate_script_with_gemini(topic, description)
    class_name = sanitize_class_name(topic)
    
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
    
    return {
        "job_id": job_id,
        "script": script,
        "script_path": script_path,
        "class_name": class_name
    }

def get_job(job_id: str) -> Optional[dict]:
    """Get job by ID."""
    return jobs_store.get(job_id)

def get_all_jobs() -> list:
    """Get all jobs."""
    return list(jobs_store.values())

def retry_job(job_id: str) -> dict:
    """Retry a failed job."""
    job = jobs_store.get(job_id)
    if not job:
        raise ValueError("Job not found")
    
    if job["status"] not in ["failed"]:
        raise ValueError(f"Cannot retry job with status: {job['status']}")
    
    logger.info(f"[{job_id}] Retrying failed job - Topic: {job['topic']}")
    
    # Reset job status
    jobs_store[job_id]["status"] = "generating_script"
    jobs_store[job_id]["message"] = "Regenerating animation script with AI..."
    jobs_store[job_id]["error"] = None
    jobs_store[job_id]["updated_at"] = datetime.now().isoformat()
    save_job_to_disk(job_id)
    
    # Generate new script
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
    
    return {
        "job_id": job_id,
        "script": script,
        "script_path": script_path,
        "class_name": class_name
    }