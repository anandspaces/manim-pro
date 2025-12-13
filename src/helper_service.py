import logging
import subprocess
import json
import uuid
import time
from pathlib import Path
from typing import Optional
from datetime import datetime
from src.database import animation_db

from src.config import (
    SCRIPTS_DIR, JOBS_DIR, MEDIA_ROOT, RENDER_TIMEOUT, RENDER_QUALITY
)
from src.redis_client import redis_client

# Import TTS functions
from src.tts_service import (
    generate_narration_text,
    generate_narration_audio
)

from src.utilities import (
    sanitize_class_name, safe_filename
)

from src.gemini_service import generate_script_with_gemini

logger = logging.getLogger("manim_ai_server")

def find_video_file(filename: str) -> Optional[Path]:
    filename_lower = filename.lower()
    if not MEDIA_ROOT.exists():
        return None
    for p in MEDIA_ROOT.rglob("*"):
        if p.is_file() and p.name.lower() == filename_lower:
            return p
    return None

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