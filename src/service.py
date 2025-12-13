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

# Import TTS functions
from src.tts_service import (
    generate_narration_text,
    generate_narration_audio,
    get_available_voices
)

logger = logging.getLogger("manim_ai_server")

# --- Helper Functions (unchanged) ---

def safe_filename(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError("Invalid filename (contains path separators).")
    return name

def find_video_file(filename: str) -> Optional[Path]:
    filename_lower = filename.lower()
    if not MEDIA_ROOT.exists():
        return None
    for p in MEDIA_ROOT.rglob("*"):
        if p.is_file() and p.name.lower() == filename_lower:
            return p
    return None

def sanitize_class_name(topic: str) -> str:
    words = ''.join(c if c.isalnum() or c.isspace() else ' ' for c in topic).split()
    class_name = ''.join(word.capitalize() for word in words)
    if not class_name:
        class_name = "Animation"
    elif class_name[0].isdigit():
        class_name = "Anim" + class_name
    return class_name + "Scene"

def save_job_to_disk(job_id: str, job_data: dict):
    try:
        job_file = JOBS_DIR / f"{job_id}.json"
        with open(job_file, 'w') as f:
            json.dump(job_data, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save job {job_id} to disk: {e}")

def load_jobs_from_disk():
    try:
        count = 0
        for job_file in JOBS_DIR.glob("*.json"):
            try:
                with open(job_file, 'r') as f:
                    job_data = json.load(f)
                    job_id = job_file.stem
                    if not redis_client.get_job(job_id):
                        redis_client.save_job(job_id, job_data)
                        count += 1
            except Exception as e:
                logger.error(f"Failed to load job from {job_file}: {e}")
        if count > 0:
            logger.info(f"Migrated {count} jobs from disk to Redis")
        redis_client.cleanup_expired_jobs()
    except Exception as e:
        logger.error(f"Failed to load jobs from disk: {e}")

def auto_fix_script(script: str) -> str:
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

# --- Core Service Functions (UPDATED) ---

def generate_script_with_gemini(
    topic: str, 
    audio_file_path: Optional[str] = None,
    audio_duration: Optional[float] = None
) -> str:
    """Generate Manim animation script using Gemini API with optional audio integration."""
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in environment variables")
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        class_name = sanitize_class_name(topic)
        
        # Base prompt
        base_prompt = f"""You are an expert Manim animation developer. Generate a SIMPLE, FAST-RENDERING Manim script.

Topic: {topic}

CRITICAL PERFORMANCE RULES (MUST FOLLOW):
1. **NO physics simulations** (no add_updater, no particle systems)
2. **NO 3D scenes** (use Scene, NOT ThreeDScene)
3. **Maximum 10 objects total** in the entire animation
4. **Simple shapes only** (Circle, Square, Text, Line, Arrow - NO Sphere/Cube)
5. **Use Scene class, NOT ThreeDScene**
"""
        
        # Add audio integration section if audio is provided
        if audio_file_path and audio_duration:
            audio_section = f"""
AUDIO INTEGRATION (IMPORTANT):
- Audio narration file: {audio_file_path}
- Audio duration: {audio_duration:.2f} seconds
- FIRST LINE in construct() MUST BE: self.add_sound("{audio_file_path}")
- Total animation duration MUST MATCH audio duration: {audio_duration:.2f}s
- Sync visual transitions with narration timing
- Use self.wait() to match audio pauses
"""
            duration_target = audio_duration
            duration_breakdown = f"""
DURATION BREAKDOWN (MATCH AUDIO: {audio_duration:.1f} SECONDS):
- Title/Intro: 2 seconds
- Main content: {audio_duration - 4:.1f} seconds  
- Outro: 2 seconds
= **{audio_duration:.1f} SECONDS TOTAL (MATCH AUDIO)**
"""
        else:
            audio_section = ""
            duration_target = 10
            duration_breakdown = """
DURATION BREAKDOWN (EXACTLY 10 SECONDS):
- Title/Intro: 2 seconds
- Main content: 6 seconds  
- Outro: 2 seconds
= **10 SECONDS TOTAL**
"""
        
        prompt = base_prompt + audio_section + duration_breakdown + f"""
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

Example CORRECT script with audio:
```python
from manim import *

class {class_name}(Scene):
    def construct(self):
        # Add audio narration (if provided)
        {'self.add_sound("' + audio_file_path + '")' if audio_file_path else '# No audio'}
        
        # Title (2s)
        title = Text("{topic}", font_size=48)
        self.play(Write(title), run_time=1.5)
        self.wait(0.5)
        
        # Main content (sync with narration)
        self.play(title.animate.scale(0.6).to_edge(UP), run_time=1)
        
        circle = Circle(radius=1, color=BLUE)
        square = Square(side_length=2, color=RED)
        
        self.play(Create(circle), run_time=1.5)
        self.play(Transform(circle, square), run_time=2)
        self.wait({audio_duration - 6.5 if audio_duration else 0.5})
        
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
{'- MUST add audio: self.add_sound("' + audio_file_path + '") as FIRST line' if audio_file_path else ''}

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
        
        # Verify audio integration if provided
        if audio_file_path and "add_sound" not in script:
            logger.warning("Generated script missing audio integration, adding it...")
            # Find construct method and add audio
            lines = script.split('\n')
            for i, line in enumerate(lines):
                if 'def construct(self):' in line:
                    indent = ' ' * 8  # Standard indent
                    lines.insert(i + 1, f'{indent}# Add narration audio')
                    lines.insert(i + 2, f'{indent}self.add_sound("{audio_file_path}")')
                    lines.insert(i + 3, '')
                    break
            script = '\n'.join(lines)
        
        return script
        
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        raise Exception(f"Failed to generate script: {str(e)}")


def render_animation(job_id: str, script_path: Path, class_name: str):
    """Render Manim animation in background (unchanged logic)."""
    try:
        logger.info(f"[{job_id}] Starting render for {class_name}")
        
        job = redis_client.get_job(job_id)
        if job:
            now = datetime.now()
            job["status"] = "rendering"
            job["message"] = "Rendering animation with audio..."
            job["updated_at"] = now.isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            animation_db.update_animation_status(job_id, "rendering")
        
        cmd = ["manim", RENDER_QUALITY, str(script_path), class_name]
        logger.info(f"[{job_id}] Running command: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=RENDER_TIMEOUT, cwd=str(SCRIPTS_DIR.parent)
        )
        
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
                animation_db.update_animation_status(job_id, "failed")
            return
        
        video_name = f"{class_name}.mp4"
        video_path = find_video_file(video_name)
        
        if not video_path:
            logger.error(f"[{job_id}] Video file not found: {video_name}")
            if job:
                job["status"] = "failed"
                job["message"] = "Video file not found after rendering"
                job["error"] = f"Expected: {video_name}"
                job["updated_at"] = datetime.now().isoformat()
                job["timestamp_numeric"] = time.time()
                redis_client.save_job(job_id, job)
                save_job_to_disk(job_id, job)
                animation_db.update_animation_status(job_id, "failed")
            return
        
        logger.info(f"[{job_id}] ✓ Render completed: {video_name}")
        
        if job:
            job["status"] = "completed"
            job["message"] = "Animation completed successfully with audio"
            job["video_name"] = video_name
            job["updated_at"] = datetime.now().isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
            animation_db.update_animation_status(
                job_id, "completed", video_name,
                job.get("audio_filename"), job.get("audio_duration")
            )
        
    except subprocess.TimeoutExpired:
        logger.error(f"[{job_id}] Render timeout after {RENDER_TIMEOUT}s")
        job = redis_client.get_job(job_id)
        if job:
            job["status"] = "failed"
            job["message"] = "Rendering timeout"
            job["error"] = f"Exceeded {RENDER_TIMEOUT}s timeout"
            job["updated_at"] = datetime.now().isoformat()
            job["timestamp_numeric"] = time.time()
            redis_client.save_job(job_id, job)
            save_job_to_disk(job_id, job)
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
            animation_db.update_animation_status(job_id, "failed")


def create_animation_job(
    topic: str, topic_id: int, subject: str, subject_id: int,
    chapter: str, chapter_id: int, level: int,
) -> dict:
    """Create animation job with TTS narration (UPDATED)."""
    
    job_id = str(uuid.uuid4())
    logger.info(f"[{job_id}] New animation request - Topic: {topic}, Level: {level}")
    
    now = datetime.now()
    timestamp_numeric = time.time()
    
    # Initialize job
    job_data = {
        "job_id": job_id,
        "status": "generating_narration",
        "message": "Generating narration text with AI...",
        "topic": topic,
        "topic_id": topic_id,
        "subject": subject,
        "subject_id": subject_id,
        "chapter": chapter,
        "chapter_id": chapter_id,
        "level": level,
        "script": None,
        "video_name": None,
        "narration_text": None,
        "audio_filename": None,
        "audio_duration": None,
        "error": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "timestamp_numeric": timestamp_numeric
    }
    
    redis_client.save_job(job_id, job_data)
    save_job_to_disk(job_id, job_data)
    
    try:
        # Step 1: Generate narration text
        logger.info(f"[{job_id}] Step 1: Generating narration text...")
        narration_text = generate_narration_text(topic)
        
        job_data["narration_text"] = narration_text
        job_data["status"] = "generating_audio"
        job_data["message"] = "Generating audio from narration..."
        job_data["updated_at"] = datetime.now().isoformat()
        job_data["timestamp_numeric"] = time.time()
        redis_client.save_job(job_id, job_data)
        
        # Step 2: Generate audio
        logger.info(f"[{job_id}] Step 2: Generating audio...")
        audio_info = generate_narration_audio(narration_text, job_id)
        
        job_data["audio_filename"] = audio_info["audio_filename"]
        job_data["audio_duration"] = audio_info["duration"]
        job_data["audio_path"] = str(audio_info["audio_path"])
        job_data["status"] = "generating_script"
        job_data["message"] = "Generating animation script..."
        job_data["updated_at"] = datetime.now().isoformat()
        job_data["timestamp_numeric"] = time.time()
        redis_client.save_job(job_id, job_data)
        
        # Step 3: Generate Manim script with audio integration
        logger.info(f"[{job_id}] Step 3: Generating Manim script with audio...")
        script = generate_script_with_gemini(
            topic,
            audio_file_path=str(audio_info["audio_path"]),
            audio_duration=audio_info["duration"]
        )
        
        class_name = sanitize_class_name(topic)
        script_filename = f"{job_id}_{class_name}.py"
        script_path = SCRIPTS_DIR / script_filename
        
        with open(script_path, 'w') as f:
            f.write(script)
        
        logger.info(f"[{job_id}] ✓ Script generated with audio integration: {script_filename}")
        
        # Update job with script
        job_data["script"] = script
        job_data["script_path"] = str(script_path)
        job_data["class_name"] = class_name
        job_data["status"] = "pending"
        job_data["message"] = "Ready for rendering"
        job_data["updated_at"] = datetime.now().isoformat()
        job_data["timestamp_numeric"] = time.time()
        
        redis_client.save_job(job_id, job_data)
        save_job_to_disk(job_id, job_data)
        
        # Save to database
        animation_db.save_animation(
            level=level, subject_id=subject_id, subject_name=subject,
            chapter_id=chapter_id, chapter_name=chapter,
            topic_id=topic_id, topic_name=topic,
            job_id=job_id, status="pending",
            narration_text=narration_text,
            audio_filename=audio_info["audio_filename"],
            audio_duration=audio_info["duration"],
            voice_style=audio_info["voice_style"]
        )
        
        return {
            "job_id": job_id,
            "script": script,
            "script_path": script_path,
            "class_name": class_name,
            "narration_text": narration_text,
            "audio_duration": audio_info["duration"]
        }
        
    except Exception as e:
        logger.error(f"[{job_id}] Failed during job creation: {e}")
        job_data["status"] = "failed"
        job_data["message"] = "Failed to create animation"
        job_data["error"] = str(e)
        job_data["updated_at"] = datetime.now().isoformat()
        redis_client.save_job(job_id, job_data)
        save_job_to_disk(job_id, job_data)
        raise


# --- Other functions (unchanged) ---

def get_job(job_id: str) -> Optional[dict]:
    return redis_client.get_job(job_id)

def get_all_jobs() -> list:
    return redis_client.get_all_jobs()

def retry_job(job_id: str) -> dict:
    job = redis_client.get_job(job_id)
    if not job:
        raise ValueError("Job not found")
    if job["status"] not in ["failed"]:
        raise ValueError(f"Cannot retry job with status: {job['status']}")
    
    logger.info(f"[{job_id}] Retrying failed job - Topic: {job['topic']}")
    
    now = datetime.now()
    job["status"] = "generating_narration"
    job["message"] = "Regenerating animation with narration..."
    job["error"] = None
    job["updated_at"] = now.isoformat()
    job["timestamp_numeric"] = time.time()
    redis_client.save_job(job_id, job)
    
    # Regenerate narration and audio
    narration_text = generate_narration_text(job["topic"])
    audio_info = generate_narration_audio(narration_text, job_id)
    
    # Generate new script with audio
    script = generate_script_with_gemini(
        job["topic"],
        audio_file_path=str(audio_info["audio_path"]),
        audio_duration=audio_info["duration"]
    )
    
    class_name = sanitize_class_name(job["topic"])
    script_filename = f"{job_id}_{class_name}.py"
    script_path = SCRIPTS_DIR / script_filename
    
    with open(script_path, 'w') as f:
        f.write(script)
    
    job["script"] = script
    job["script_path"] = str(script_path)
    job["class_name"] = class_name
    job["narration_text"] = narration_text
    job["audio_filename"] = audio_info["audio_filename"]
    job["audio_duration"] = audio_info["duration"]
    job["status"] = "pending"
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