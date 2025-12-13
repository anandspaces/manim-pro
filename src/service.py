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

# --- Helper Functions ---

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


def get_animation_complexity(level: int) -> str:
    """Get animation complexity guidance based on grade level"""
    if level <= 5:
        return """
ANIMATION STYLE FOR ELEMENTARY (Grades 1-5):
- Large, colorful shapes and simple icons
- Smooth, slow transitions (1.5-2s per animation)
- Maximum 5-6 visual elements total
- Use bright, friendly colors (BLUE, YELLOW, GREEN, RED, ORANGE)
- Simple movements: grow, shrink, slide, rotate
- Clear visual hierarchy with large text (font_size=60-72)
"""
    elif level <= 8:
        return """
ANIMATION STYLE FOR MIDDLE SCHOOL (Grades 6-8):
- Mix of shapes, diagrams, and annotated illustrations
- Moderate pace transitions (1-1.5s per animation)
- Up to 8-10 visual elements
- Professional but engaging colors
- Introduce basic diagrams and labeled components
- Text size: 48-60
- Show step-by-step processes
"""
    elif level <= 10:
        return """
ANIMATION STYLE FOR HIGH SCHOOL (Grades 9-10):
- Detailed diagrams, graphs, and visual models
- Faster, more efficient transitions (0.8-1.2s)
- Up to 10-12 elements
- Academic color schemes with good contrast
- Include arrows, labels, and annotations
- Text size: 40-48
- Demonstrate relationships and processes
- Can include simple formulas as text
"""
    else:
        return """
ANIMATION STYLE FOR ADVANCED (Grades 11-12+):
- Complex visualizations, detailed graphs, models
- Efficient transitions (0.6-1s)
- Up to 15 elements if needed
- Professional, scholarly presentation
- Sophisticated diagrams with multiple layers
- Text size: 36-44
- Show abstract concepts visually
- Include mathematical representations as text
- Demonstrate advanced relationships
"""


def generate_script_with_gemini(
    topic: str,
    subject: str, 
    chapter: str,
    level: int,
    audio_file_path: Optional[str] = None,
    audio_duration: Optional[float] = None
) -> str:
    """
    Generate context-aware Manim animation script using Gemini API.
    
    Args:
        topic: Specific topic to animate
        subject: Subject area (e.g., "Science", "Mathematics")
        chapter: Chapter context
        level: Grade level (1-12+)
        audio_file_path: Path to narration audio file
        audio_duration: Duration of audio in seconds
    """
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in environment variables")
            
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(GEMINI_MODEL)
        
        class_name = sanitize_class_name(topic)
        complexity = get_animation_complexity(level)
        
        # Base prompt with educational context
        base_prompt = f"""You are an expert educational animator creating a Manim visualization.

EDUCATIONAL CONTEXT:
- Subject: {subject}
- Chapter: {chapter}
- Topic: {topic}
- Grade Level: {level}
- Audience: Students in grade {level}

MISSION: Create a visually rich, educationally sound animation that helps students understand {topic} in the context of {chapter} ({subject}).

{complexity}

CRITICAL PERFORMANCE RULES:
1. **Use Scene class only** (NOT ThreeDScene - 3D is too slow)
2. **Simple 2D shapes**: Circle, Square, Rectangle, Triangle, Line, Arrow, Text, Dot
3. **Maximum object count based on level**:
   - Grades 1-5: Max 6 objects
   - Grades 6-8: Max 10 objects  
   - Grades 9-10: Max 12 objects
   - Grades 11-12+: Max 15 objects
4. **NO physics simulations** (no add_updater, particles, or per-frame calculations)
5. **Efficient animations**: Create, Write, FadeIn, FadeOut, Transform, ReplacementTransform
"""
        
        # Add audio integration
        if audio_file_path and audio_duration:
            audio_section = f"""
AUDIO INTEGRATION (CRITICAL):
- Audio file: {audio_file_path}
- Duration: {audio_duration:.2f} seconds
- **FIRST line in construct() MUST be**: self.add_sound("{audio_file_path}")
- **Total animation MUST match audio duration**: {audio_duration:.2f}s
- Sync visual changes with narration pacing
- Use self.wait() for natural pauses

TIMING BREAKDOWN for {audio_duration:.1f}s:
- Introduction (title/hook): 15-20% of duration ({audio_duration * 0.15:.1f}-{audio_duration * 0.20:.1f}s)
- Core content (main animation): 60-70% ({audio_duration * 0.60:.1f}-{audio_duration * 0.70:.1f}s)
- Conclusion/summary: 10-15% ({audio_duration * 0.10:.1f}-{audio_duration * 0.15:.1f}s)
"""
            duration_target = audio_duration
        else:
            audio_section = "# No audio provided - create 10 second animation"
            duration_target = 10
        
        # Content guidance based on subject
        subject_guidance = ""
        if "science" in subject.lower() or "physics" in subject.lower():
            subject_guidance = """
SCIENCE VISUALIZATION:
- Show processes, cycles, or systems
- Use arrows to indicate force, flow, or direction  
- Color-code different components
- Demonstrate cause and effect relationships
- Include labels for key parts
"""
        elif "math" in subject.lower():
            subject_guidance = """
MATHEMATICS VISUALIZATION:
- Show geometric relationships visually
- Use color to distinguish different elements
- Animate transformations step-by-step
- Display equations as Text (spell out if complex)
- Demonstrate concepts with concrete shapes
"""
        elif "biology" in subject.lower():
            subject_guidance = """
BIOLOGY VISUALIZATION:
- Represent biological structures with simple shapes
- Use color to show different cell types, organisms, etc.
- Animate processes like cell division, energy flow
- Label important structures
- Show scale and organization
"""
        
        prompt = base_prompt + audio_section + subject_guidance + f"""
CONTENT REQUIREMENTS:
1. **Educational accuracy**: Content must be scientifically/mathematically correct
2. **Progressive complexity**: Build from simple to complex
3. **Visual storytelling**: Each animation should add to understanding
4. **Clear labels**: Use Text objects to label key concepts (appropriate font size for grade {level})
5. **Color meaning**: Use color purposefully to distinguish concepts
6. **Smooth flow**: Transitions should feel natural and help understanding

TEMPLATE STRUCTURE:
```python
from manim import *

class {class_name}(Scene):
    def construct(self):
        # 1. AUDIO INTEGRATION
        {'self.add_sound("' + audio_file_path + '")' if audio_file_path else '# No audio'}
        
        # 2. TITLE/INTRODUCTION
        title = Text("{topic}", font_size=APPROPRIATE_SIZE)
        subtitle = Text("{chapter}", font_size=SMALLER_SIZE).next_to(title, DOWN)
        
        self.play(Write(title), run_time=1.5)
        self.play(FadeIn(subtitle), run_time=0.8)
        self.wait(0.5)
        
        # Move title to make room for content
        self.play(
            title.animate.scale(0.5).to_edge(UP),
            FadeOut(subtitle),
            run_time=1
        )
        
        # 3. MAIN EDUCATIONAL CONTENT
        # Create 2-4 key visual sections that build understanding
        # Each section should:
        # - Introduce a new concept or component
        # - Use 2-3 objects maximum
        # - Have clear labels
        # - Connect to previous sections
        
        # Example structure for main content:
        # Section 1: Introduce basic concept (20-30% of time)
        # Section 2: Show relationship/process (30-40% of time)
        # Section 3: Demonstrate application (20-30% of time)
        
        # 4. CONCLUSION
        # Summary or final insight
        # Clean fadeout of all elements
        
        self.wait(0.5)
```

SYNTAX REQUIREMENTS:
- Class name: {class_name}
- Only import: from manim import *
- Rate functions: smooth, linear, rush_into, rush_from, there_and_back
- NO markdown, NO explanations outside code
- Return ONLY executable Python code
- Keep performance optimized

QUALITY CHECKLIST:
✓ Educationally accurate content
✓ Appropriate complexity for grade {level}
✓ Visual elements support learning
✓ Clear, readable labels
✓ Smooth, purposeful animations
✓ Timing matches audio narration
✓ Total duration = {duration_target:.1f}s

Generate the complete, elaborate Manim script now:
"""

        response = model.generate_content(prompt)
        script = response.text.strip()
        
        # Clean markdown
        if script.startswith("```python"):
            script = script.replace("```python", "").replace("```", "").strip()
        elif script.startswith("```"):
            script = script.replace("```", "").strip()
        
        # Auto-fix common errors
        script = auto_fix_script(script)
        
        # Verify audio integration
        if audio_file_path and "add_sound" not in script:
            logger.warning("Generated script missing audio integration, adding it...")
            lines = script.split('\n')
            for i, line in enumerate(lines):
                if 'def construct(self):' in line:
                    indent = ' ' * 8
                    lines.insert(i + 1, f'{indent}# Add narration audio')
                    lines.insert(i + 2, f'{indent}self.add_sound("{audio_file_path}")')
                    lines.insert(i + 3, '')
                    break
            script = '\n'.join(lines)
        
        logger.info(f"Generated {len(script)} char script for {topic} (Level {level})")
        return script
        
    except Exception as e:
        logger.error(f"Script generation error: {e}")
        raise Exception(f"Failed to generate script: {str(e)}")


def render_animation(job_id: str, script_path: Path, class_name: str):
    """Render Manim animation in background."""
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
    """Create animation job with context-aware TTS narration."""
    
    job_id = str(uuid.uuid4())
    logger.info(f"[{job_id}] New animation request - Topic: {topic}, Level: {level}")
    
    now = datetime.now()
    timestamp_numeric = time.time()
    
    # Initialize job
    job_data = {
        "job_id": job_id,
        "status": "generating_narration",
        "message": "Generating educational narration...",
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
        # Step 1: Generate context-aware narration
        logger.info(f"[{job_id}] Step 1: Generating narration text...")
        narration_text = generate_narration_text(topic, subject, chapter, level)
        
        job_data["narration_text"] = narration_text
        job_data["status"] = "generating_audio"
        job_data["message"] = "Converting narration to speech..."
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
        job_data["message"] = "Generating educational animation script..."
        job_data["updated_at"] = datetime.now().isoformat()
        job_data["timestamp_numeric"] = time.time()
        redis_client.save_job(job_id, job_data)
        
        # Step 3: Generate context-aware Manim script
        logger.info(f"[{job_id}] Step 3: Generating Manim script with educational context...")
        script = generate_script_with_gemini(
            topic=topic,
            subject=subject,
            chapter=chapter,
            level=level,
            audio_file_path=str(audio_info["audio_path"]),
            audio_duration=audio_info["duration"]
        )
        
        class_name = sanitize_class_name(topic)
        script_filename = f"{job_id}_{class_name}.py"
        script_path = SCRIPTS_DIR / script_filename
        
        with open(script_path, 'w') as f:
            f.write(script)
        
        logger.info(f"[{job_id}] ✓ Educational script generated: {script_filename}")
        
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
    job["message"] = "Regenerating animation with context-aware narration..."
    job["error"] = None
    job["updated_at"] = now.isoformat()
    job["timestamp_numeric"] = time.time()
    redis_client.save_job(job_id, job)
    
    # Regenerate with full context
    narration_text = generate_narration_text(
        job["topic"], 
        job["subject"], 
        job["chapter"], 
        job["level"]
    )
    audio_info = generate_narration_audio(narration_text, job_id)
    
    # Generate new script with context
    script = generate_script_with_gemini(
        topic=job["topic"],
        subject=job["subject"],
        chapter=job["chapter"],
        level=job["level"],
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