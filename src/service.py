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
8. Duration should be 6-10 seconds
9. Use appropriate colors and styling
10. Include necessary imports (numpy, etc.)

CRITICAL DURATION RULES:
- Keep animations SHORT and CONCISE
- Use self.wait() sparingly (max 1-2 seconds total)
- Aim for 6-10 seconds total runtime
- Count your wait times to ensure you don't exceed 10 seconds

CRITICAL SYNTAX RULES:
- NEVER use RunAnimation() - it's deprecated
- Pass animations directly to self.play(): self.play(Create(obj), Write(text))
- For 3D scenes, use self.move_camera() or self.begin_ambient_camera_rotation()
- Always test that your code would run without errors
- Use only documented Manim methods and classes
- Keep animations SIMPLE to ensure fast rendering

CRITICAL IMPORT AND RATE FUNCTION RULES:
- ONLY import from manim: from manim import *
- NEVER import from manim.utils.rate_functions
- AVAILABLE rate functions: smooth, linear, rush_into, rush_from, slow_into, there_and_back
- USE 'smooth' instead of ease_in_out_sine, ease_in_out, or any other easing function
- USE 'rush_into' instead of ease_in
- USE 'rush_from' instead of ease_out

PERFORMANCE OPTIMIZATION:
- Avoid complex 3D scenes with many objects
- Limit particle systems to < 20 objects
- Use simple shapes when possible
- Avoid nested loops creating many objects
- Keep total object count under 30
- Userun_time parameter to speed up animations

Example of CORRECT 10-second animation:
```python
from manim import *

class MyScene(Scene):
    def construct(self):
        # Title - 2 seconds
        title = Text("Topic")
        self.play(Write(title), run_time=1.5)
        self.wait(0.5)
        
        # Main content - 5 seconds
        self.play(title.animate.scale(0.5).to_edge(UP), run_time=1)
        circle = Circle(color=BLUE)
        self.play(Create(circle), run_time=2)
        self.play(circle.animate.set_fill(BLUE, opacity=0.5), run_time=2)
        
        # Outro - 2.5 seconds
        self.play(FadeOut(title), FadeOut(circle), run_time=2)
        self.wait(0.5)
        # Total: 1.5 + 0.5 + 1 + 2 + 2 + 2 + 0.5 = 9.5 seconds ✓
```

Example of INCORRECT (TOO LONG):
```python
# WRONG - This would take 25+ seconds
self.wait(3)  # Too long
self.play(Create(obj), run_time=5)  # Too slow
for i in range(100):  # Too many objects
    self.play(Create(Circle()))  # Way too long
```

IMPORTANT: 
- Return ONLY the Python code, no explanations
- Do not include markdown code blocks (no ```python```)
- Start directly with imports
- Make it production-ready and FAST
- Maximum 10 seconds total duration
- Keep it simple for quick rendering

Example structure:
from manim import *
import numpy as np

class {class_name}(Scene):  # or ThreeDScene
    def construct(self):
        # Quick intro (1-2s)
        title = Text("{topic}")
        self.play(Write(title), run_time=1)
        self.wait(0.5)
        
        # Main content (5-6s)
        self.play(title.animate.to_edge(UP), run_time=1)
        # Add 2-3 quick animations here
        
        # Quick outro (1-2s)
        self.play(*[FadeOut(mob) for mob in self.mobjects], run_time=1.5)
        self.wait(0.5)
"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Clean up markdown code blocks if present
        if script.startswith("```python"):
            script = script.replace("```python", "").replace("```", "").strip()
        elif script.startswith("```"):
            script = script.replace("```", "").strip()
        
        # Auto-fix common errors
        script = auto_fix_script(script)
        
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
            timeout=RENDER_TIMEOUT,
            cwd=str(SCRIPTS_DIR.parent)  # Run from app directory
        )
        
        # Log output for debugging
        if result.stdout:
            logger.info(f"[{job_id}] Manim stdout: {result.stdout[-500:]}")  # Last 500 chars
        if result.stderr:
            logger.warning(f"[{job_id}] Manim stderr: {result.stderr[-500:]}")
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "Unknown rendering error"
            logger.error(f"[{job_id}] Render failed (exit {result.returncode}): {error_msg}")
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
            logger.info(f"[{job_id}] Searching in MEDIA_ROOT: {MEDIA_ROOT}")
            # List what files exist
            existing_files = list(MEDIA_ROOT.rglob("*.mp4"))
            logger.info(f"[{job_id}] Found {len(existing_files)} MP4 files: {[f.name for f in existing_files[:10]]}")
            
            jobs_store[job_id]["status"] = "failed"
            jobs_store[job_id]["message"] = "Video file not found after rendering"
            jobs_store[job_id]["error"] = f"Expected video: {video_name}, found {len(existing_files)} other videos"
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
        logger.error(f"[{job_id}] Render timeout after {RENDER_TIMEOUT} seconds")
        jobs_store[job_id]["status"] = "failed"
        jobs_store[job_id]["message"] = "Rendering timeout - animation too complex"
        jobs_store[job_id]["error"] = f"Render exceeded {RENDER_TIMEOUT}s timeout. The generated script may be too complex or have infinite loops."
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