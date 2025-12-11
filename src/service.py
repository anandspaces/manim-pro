import logging
import subprocess
import json
import uuid
import re
import time
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from src.database import animation_db
import google.generativeai as genai

from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL, SCRIPTS_DIR, 
    JOBS_DIR, MEDIA_ROOT, RENDER_TIMEOUT, RENDER_QUALITY
)
from src.redis_client import redis_client

logger = logging.getLogger("manim_ai_server")

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

def save_job_to_disk(job_id: str, job_data: dict):
    """Persist job data to disk as backup."""
    try:
        job_file = JOBS_DIR / f"{job_id}.json"
        with open(job_file, 'w') as f:
            json.dump(job_data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save job {job_id} to disk: {e}")

def load_jobs_from_disk():
    """Load all jobs from disk on startup (migration from old system)."""
    try:
        count = 0
        for job_file in JOBS_DIR.glob("*.json"):
            try:
                with open(job_file, 'r') as f:
                    job_data = json.load(f)
                    job_id = job_file.stem
                    
                    # Check if job already exists in Redis
                    if not redis_client.get_job(job_id):
                        redis_client.save_job(job_id, job_data)
                        count += 1
            except Exception as e:
                logger.error(f"Failed to load job from {job_file}: {e}")
        
        if count > 0:
            logger.info(f"Migrated {count} jobs from disk to Redis")
        
        # Cleanup expired jobs
        redis_client.cleanup_expired_jobs()
        
    except Exception as e:
        logger.error(f"Failed to load jobs from disk: {e}")

def validate_script_safety(script: str) -> tuple[bool, str]:
    """
    Validate script for performance-killing patterns.
    Returns: (is_safe, error_message)
    """
    issues = []
    
    # Check for too many particles
    particle_match = re.search(r'num_particles\s*=\s*(\d+)', script)
    if particle_match:
        num = int(particle_match.group(1))
        if num > 15:
            issues.append(f"Too many particles ({num}). Max allowed: 15")
    
    # Check for physics updaters (very slow)
    if 'add_updater' in script:
        issues.append("Physics updaters (add_updater) are too slow for rendering")
    
    # Check for nested loops creating objects
    if re.search(r'for.*for.*\.(add|play)', script, re.DOTALL):
        issues.append("Nested loops creating objects will be too slow")
    
    # Count total objects being created
    create_count = len(re.findall(r'\b(Circle|Square|Dot|Sphere|Cube|Text|Line|Arrow)\(', script))
    if create_count > 25:
        issues.append(f"Too many objects ({create_count}). Max recommended: 25")
    
    # Check for 3D scenes with many objects
    if 'ThreeDScene' in script and create_count > 15:
        issues.append("3D scenes with >15 objects are too slow")
    
    # Check total duration
    total_wait = 0
    for wait in re.findall(r'self\.wait\(([^)]+)\)', script):
        try:
            total_wait += float(eval(wait))
        except:
            pass
    
    total_runtime = 0
    for runtime in re.findall(r'run_time\s*=\s*([^,)]+)', script):
        try:
            total_runtime += float(eval(runtime))
        except:
            pass
    
    estimated_duration = total_wait + total_runtime
    if estimated_duration > 12:
        issues.append(f"Estimated duration {estimated_duration:.1f}s exceeds 10s limit")
    
    if issues:
        return False, " | ".join(issues)
    return True, "Script validated successfully"

def auto_fix_script(script: str) -> str:
    """Automatically fix common errors in generated scripts."""
    
    # Fix rate functions
    rate_func_fixes = {
        'ease_in_out_sine': 'smooth',
        'ease_in_sine': 'slow_into',
        'ease_out_sine': 'rush_from',
        'ease_in_out': 'smooth',
        'ease_in': 'rush_into',
        'ease_out': 'rush_from',
        'ease_in_out_quad': 'smooth',
        'ease_in_out_cubic': 'smooth',
    }
    
    for old, new in rate_func_fixes.items():
        if old in script:
            logger.info(f"Auto-fixing: Replacing '{old}' with '{new}'")
            script = script.replace(old, new)
    
    return script

# --- Core Service Functions ---

def generate_script_with_gemini(topic: str) -> str:
    """Generate Manim animation script using Gemini API."""
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in environment variables")
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        class_name = sanitize_class_name(topic)
        
        prompt = f"""You are an expert Manim animation developer. Generate a SIMPLE, FAST-RENDERING Manim script.

Topic: {topic}

CRITICAL PERFORMANCE RULES (MUST FOLLOW):
1. **NO physics simulations** (no add_updater, no particle systems)
2. **NO 3D scenes** (use Scene, NOT ThreeDScene)
3. **Maximum 10 objects total** in the entire animation
4. **Simple shapes only** (Circle, Square, Text, Line, Arrow - NO Sphere/Cube)
5. **Total duration: 8-10 seconds MAXIMUM**
6. **Use Scene class, NOT ThreeDScene**

DURATION BREAKDOWN (EXACTLY 10 SECONDS):
- Title/Intro: 2 seconds
- Main content: 5 seconds  
- Outro: 2 seconds
- Buffer: 1 second
= **10 SECONDS TOTAL**

ALLOWED:
- Simple 2D shapes (Circle, Square, Rectangle, Text)
- Transformations (Transform, ReplacementTransform)
- Movement (shift, move_to, animate.scale)
- Basic animations (Create, Write, FadeIn, FadeOut)
- Color changes and rotations
- Max 10 objects

FORBIDDEN (TOO SLOW):
- ThreeDScene, Sphere, Cube (3D is slow!)
- add_updater, physics simulations
- Particle systems, random particles
- More than 10 objects
- Nested loops creating objects
- Complex calculations per frame
- Ambient camera rotation

Example CORRECT script (10 seconds):
```python
from manim import *

class {class_name}(Scene):
    def construct(self):
        # Title (2s)
        title = Text("{topic}", font_size=48)
        self.play(Write(title), run_time=1.5)
        self.wait(0.5)
        
        # Main (5s)
        self.play(title.animate.scale(0.6).to_edge(UP), run_time=1)
        
        circle = Circle(radius=1, color=BLUE)
        square = Square(side_length=2, color=RED)
        
        self.play(Create(circle), run_time=1.5)
        self.play(Transform(circle, square), run_time=2)
        self.wait(0.5)
        
        # Outro (2s)
        self.play(
            FadeOut(title),
            FadeOut(circle),
            run_time=1.5
        )
        self.wait(0.5)
```

CRITICAL SYNTAX RULES:
- Class name: {class_name}
- Use Scene (NOT ThreeDScene)
- ONLY import: from manim import *
- Rate functions: smooth, linear, rush_into, rush_from (NO ease_in_out_sine!)
- Return ONLY Python code (no markdown, no explanations)
- Keep it SIMPLE for fast rendering

Generate a SIMPLE 2D animation that renders in under 2 minutes:
"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Clean up markdown code blocks
        if script.startswith("```python"):
            script = script.replace("```python", "").replace("```", "").strip()
        elif script.startswith("```"):
            script = script.replace("```", "").strip()
        
        # Auto-fix common errors
        script = auto_fix_script(script)
        
        # Validate script
        # is_safe, validation_msg = validate_script_safety(script)
        # logger.info(f"Script validation: {validation_msg}")
        
        # if not is_safe:
        #     logger.warning(f"Script validation failed: {validation_msg}")
        #     logger.warning("Attempting to generate simpler script...")
        #     raise ValueError(f"Generated script failed validation: {validation_msg}")
        
        return script
        
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        raise Exception(f"Failed to generate script: {str(e)}")

def render_animation(job_id: str, script_path: Path, class_name: str):
    """Render Manim animation in background."""
    
    try:
        logger.info(f"[{job_id}] Starting render for {class_name}")
        
        # Update job status in Redis
        job = redis_client.get_job(job_id)
        if job:
            now = datetime.now()
            job["status"] = "rendering"
            job["message"] = "Rendering animation..."
            job["updated_at"] = now.isoformat()
            job["timestamp_numeric"] = time.time()  # Update timestamp
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            
            # Update database
            animation_db.update_animation_status(job_id, "rendering")
        
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
            timeout=RENDER_TIMEOUT,
            cwd=str(SCRIPTS_DIR.parent)
        )
        
        # Log output for debugging
        if result.stdout:
            logger.info(f"[{job_id}] Manim stdout: {result.stdout[-500:]}")
        if result.stderr:
            logger.warning(f"[{job_id}] Manim stderr: {result.stderr[-500:]}")
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown rendering error"
            logger.error(f"[{job_id}] Render failed (exit {result.returncode})")
            
            if job:
                job["status"] = "failed"
                job["message"] = "Rendering failed"
                job["error"] = error_msg
                job["updated_at"] = datetime.now().isoformat()
                job["timestamp_numeric"] = time.time()
                redis_client.save_job(job_id, job)
                save_job_to_disk(job_id, job)
                
                # Update database
                animation_db.update_animation_status(job_id, "failed")
            return
        
        # Find generated video
        video_name = f"{class_name}.mp4"
        video_path = find_video_file(video_name)
        
        if not video_path:
            logger.error(f"[{job_id}] Video file not found: {video_name}")
            existing_files = list(MEDIA_ROOT.rglob("*.mp4"))
            logger.info(f"[{job_id}] Found {len(existing_files)} MP4 files")
            
            if job:
                job["status"] = "failed"
                job["message"] = "Video file not found after rendering"
                job["error"] = f"Expected: {video_name}"
                job["updated_at"] = datetime.now().isoformat()
                job["timestamp_numeric"] = time.time()
                redis_client.save_job(job_id, job)
                save_job_to_disk(job_id, job)
                
                # Update database
                animation_db.update_animation_status(job_id, "failed")
            return
        
        # Success!
        logger.info(f"[{job_id}] Render completed: {video_name}")
        
        if job:
            job["status"] = "completed"
            job["message"] = "Animation completed successfully"
            job["video_name"] = video_name
            job["updated_at"] = datetime.now().isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            
            # Update database
            animation_db.update_animation_status(job_id, "completed", video_name)
        
    except subprocess.TimeoutExpired:
        logger.error(f"[{job_id}] Render timeout after {RENDER_TIMEOUT}s")
        
        job = redis_client.get_job(job_id)
        if job:
            job["status"] = "failed"
            job["message"] = "Rendering timeout - script too complex"
            job["error"] = f"Exceeded {RENDER_TIMEOUT}s timeout. Use simpler animations."
            job["updated_at"] = datetime.now().isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            
            # Update database
            animation_db.update_animation_status(job_id, "failed")
        
    except Exception as e:
        logger.exception(f"[{job_id}] Unexpected render error")
        
        job = redis_client.get_job(job_id)
        if job:
            job["status"] = "failed"
            job["message"] = "Rendering failed"
            job["error"] = str(e)
            job["updated_at"] = datetime.now().isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            
            # Update database
            animation_db.update_animation_status(job_id, "failed")

def create_animation_job(
    topic: str,
    topic_id: int,
    subject: str,
    subject_id: int,
    chapter: str,
    chapter_id: int,
    level: int,
) -> dict:
    """Create a new animation job and generate script."""
    
    job_id = str(uuid.uuid4())
    
    logger.info(f"[{job_id}] New animation request - Topic: {topic}, Level: {level}")
    
    # Get current timestamp
    now = datetime.now()
    timestamp_numeric = time.time()
    
    # Initialize job
    job_data = {
        "job_id": job_id,
        "status": "generating_script",
        "message": "Generating animation script with AI...",
        "topic": topic,
        "topic_id": topic_id,
        "subject": subject,
        "subject_id": subject_id,
        "chapter": chapter,
        "chapter_id": chapter_id,
        "level": level,
        "script": None,
        "video_name": None,
        "error": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "timestamp_numeric": timestamp_numeric  # For Redis sorted set
    }
    
    # Save to Redis
    redis_client.save_job(job_id, job_data)
    save_job_to_disk(job_id, job_data)
    
    # Save to SQLite database
    animation_db.save_animation(
        level=level,
        subject_id=subject_id,
        subject_name=subject,
        chapter_id=chapter_id,
        chapter_name=chapter,
        topic_id=topic_id,
        topic_name=topic,
        job_id=job_id,
        status="generating_script"
    )
    
    # Generate script with Gemini
    script = generate_script_with_gemini(topic)
    class_name = sanitize_class_name(topic)
    
    # Save script
    script_filename = f"{job_id}_{class_name}.py"
    script_path = SCRIPTS_DIR / script_filename
    
    with open(script_path, 'w') as f:
        f.write(script)
    
    logger.info(f"[{job_id}] Script generated: {script_filename}")
    
    # Update job with script
    job_data["script"] = script
    job_data["script_path"] = str(script_path)
    job_data["class_name"] = class_name
    job_data["updated_at"] = datetime.now().isoformat()
    job_data["timestamp_numeric"] = time.time()
    
    redis_client.save_job(job_id, job_data)
    save_job_to_disk(job_id, job_data)
    
    return {
        "job_id": job_id,
        "script": script,
        "script_path": script_path,
        "class_name": class_name
    }

def get_job(job_id: str) -> Optional[dict]:
    """Get job by ID from Redis."""
    return redis_client.get_job(job_id)

def get_all_jobs() -> list:
    """Get all jobs from Redis."""
    return redis_client.get_all_jobs()

def retry_job(job_id: str) -> dict:
    """Retry a failed job."""
    job = redis_client.get_job(job_id)
    if not job:
        raise ValueError("Job not found")
    
    if job["status"] not in ["failed"]:
        raise ValueError(f"Cannot retry job with status: {job['status']}")
    
    logger.info(f"[{job_id}] Retrying failed job - Topic: {job['topic']}")
    
    # Reset job status
    now = datetime.now()
    job["status"] = "generating_script"
    job["message"] = "Regenerating simpler animation script..."
    job["error"] = None
    job["updated_at"] = now.isoformat()
    job["timestamp_numeric"] = time.time()
    
    redis_client.save_job(job_id, job)
    save_job_to_disk(job_id, job)
    
    # Generate new script
    script = generate_script_with_gemini(job["topic"])
    class_name = sanitize_class_name(job["topic"])
    
    # Save new script
    script_filename = f"{job_id}_{class_name}.py"
    script_path = SCRIPTS_DIR / script_filename
    
    with open(script_path, 'w') as f:
        f.write(script)
    
    logger.info(f"[{job_id}] New script generated: {script_filename}")
    
    # Update job with new script
    job["script"] = script
    job["script_path"] = str(script_path)
    job["class_name"] = class_name
    job["updated_at"] = datetime.now().isoformat()
    job["timestamp_numeric"] = time.time()
    
    redis_client.save_job(job_id, job)
    save_job_to_disk(job_id, job)
    
    return {
        "job_id": job_id,
        "script": script,
        "script_path": script_path,
        "class_name": class_name
    }