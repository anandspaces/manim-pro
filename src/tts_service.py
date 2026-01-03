import logging
import soundfile as sf
from pathlib import Path
from typing import Optional, Dict
import google.generativeai as genai
import threading

from src.tts.helper import load_text_to_speech, load_voice_style, Style, TextToSpeech
from src.config import (
    ONNX_DIR, VOICE_STYLES_DIR, NARRATIONS_DIR, 
    DEFAULT_VOICE_STYLE, TTS_SPEED, TTS_TOTAL_STEPS, 
    TTS_SILENCE_DURATION, USE_GPU_TTS, GEMINI_API_KEY
)

logger = logging.getLogger("manim_ai_server")

# Global TTS engine (loaded once) with thread lock
_tts_engine: Optional[TextToSpeech] = None
_voice_styles: Dict[str, Style] = {}
_tts_lock = threading.Lock()
_tts_initialized = False


def initialize_tts_engine():
    """Initialize TTS engine and load voice styles (called at startup)"""
    global _tts_engine, _voice_styles, _tts_initialized
    
    with _tts_lock:
        if _tts_initialized and _tts_engine is not None:
            logger.info("TTS engine already initialized")
            return
        
        try:
            logger.info(f"Loading TTS engine from {ONNX_DIR}...")
            _tts_engine = load_text_to_speech(str(ONNX_DIR), USE_GPU_TTS)
            logger.info("✓ TTS engine loaded successfully")
            
            # Load voice styles
            logger.info(f"Loading voice styles from {VOICE_STYLES_DIR}...")
            for voice_file in VOICE_STYLES_DIR.glob("*.json"):
                voice_name = voice_file.stem
                style = load_voice_style([str(voice_file)], verbose=False)
                _voice_styles[voice_name] = style
                logger.info(f"  Loaded voice style: {voice_name}")
            
            if not _voice_styles:
                logger.warning("No voice styles loaded!")
            else:
                logger.info(f"✓ Loaded {len(_voice_styles)} voice styles: {list(_voice_styles.keys())}")
            
            _tts_initialized = True
            logger.info("✓ TTS initialization complete")
                
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")
            _tts_engine = None
            _voice_styles = {}
            _tts_initialized = False
            raise


def ensure_tts_initialized():
    """Ensure TTS is initialized, initialize if needed"""
    global _tts_initialized
    
    if not _tts_initialized or _tts_engine is None:
        logger.warning("TTS engine not initialized, initializing now...")
        try:
            initialize_tts_engine()
        except Exception as e:
            raise RuntimeError(f"Failed to initialize TTS engine: {e}")


def get_available_voices() -> list[str]:
    """Get list of available voice styles"""
    return list(_voice_styles.keys())


def get_narration_length_for_level(level: int) -> tuple[int, int]:
    """
    Determine narration length based on grade level.
    Returns (min_chars, max_chars)
    """
    if level <= 5:  # Elementary (Grades 1-5)
        return (200, 350)
    elif level <= 8:  # Middle School (Grades 6-8)
        return (300, 500)
    elif level <= 10:  # High School (Grades 9-10)
        return (400, 650)
    else:  # Advanced (Grades 11-12+)
        return (500, 800)


def get_complexity_guidance(level: int) -> str:
    """Get complexity and language guidance based on grade level"""
    if level <= 5:
        return """
- Use simple, everyday words
- Short sentences (10-15 words each)
- Concrete examples and analogies
- Encouraging, friendly tone
- Avoid technical jargon
"""
    elif level <= 8:
        return """
- Mix of simple and intermediate vocabulary
- Medium-length sentences
- Introduce basic technical terms with explanations
- Engaging and informative tone
- Connect to real-world applications
"""
    elif level <= 10:
        return """
- Academic vocabulary appropriate for high school
- Varied sentence structure
- Technical terms with context
- Clear, professional tone
- Include practical applications and implications
"""
    else:
        return """
- Advanced academic vocabulary
- Complex sentence structures
- Precise technical terminology
- Scholarly yet accessible tone
- Discuss theoretical foundations and advanced applications
"""


def generate_narration_text(
    topic: str,
    subject: str,
    chapter: str,
    level: int
) -> str:
    """
    Generate context-aware narration text using Gemini.
    
    Args:
        topic: Specific topic to cover
        subject: Subject area (e.g., "Science", "Mathematics")
        chapter: Chapter context (e.g., "Forces and Motion")
        level: Grade level (1-12+)
    
    Returns:
        Educational narration text suitable for TTS
    """
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")
        
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        min_chars, max_chars = get_narration_length_for_level(level)
        complexity = get_complexity_guidance(level)
        
        # Determine target duration (reading speed: ~150 words/min = 2.5 words/sec)
        target_duration = (min_chars + max_chars) // 2 / 5  # rough estimate in seconds
        
        prompt = f"""Generate an educational narration script for an animated video.

CONTEXT:
- Subject: {subject}
- Chapter: {chapter}
- Topic: {topic}
- Grade Level: {level}
- Target Length: {min_chars}-{max_chars} characters
- Target Duration: ~{target_duration:.0f} seconds of speech

NARRATION STRUCTURE:
1. **Hook/Introduction (1-2 sentences)**: 
   - Grab attention with a relatable question, fact, or scenario
   - Connect topic to student's world or prior knowledge

2. **Core Explanation (3-5 sentences)**:
   - Explain the key concept clearly and accurately
   - Use appropriate examples for grade level {level}
   - Build understanding progressively
   - Include visual cues where relevant (e.g., "Imagine...", "Picture...", "Let's observe...")

3. **Application/Significance (1-2 sentences)**:
   - Show why this matters
   - Connect to real-world applications or further learning
   - End with insight or thought-provoking point

LANGUAGE COMPLEXITY FOR GRADE {level}:
{complexity}

TECHNICAL REQUIREMENTS:
- NO special characters, equations, or symbols (e.g., NO "a² + b² = c²")
- Spell out mathematical expressions (e.g., "a squared plus b squared equals c squared")
- NO markdown formatting, asterisks, or emphasis marks
- Proper pronunciation-friendly text for TTS
- Natural speech patterns with good flow
- Total length: {min_chars}-{max_chars} characters

EXAMPLE for Grade 8 - Science - Forces and Motion - "Newton's First Law":
"Have you ever wondered why you lurch forward when a car suddenly stops? This phenomenon is explained by Newton's First Law of Motion, also called the law of inertia. It states that an object at rest stays at rest, and an object in motion continues moving at constant velocity, unless acted upon by an external force. Your body wants to keep moving forward when the car brakes, because there's no force immediately stopping you—that's inertia in action. Understanding this law helps explain everything from seat belt safety to how rockets move through space. It's one of the most fundamental principles governing motion in our universe."

Now generate a {min_chars}-{max_chars} character narration for:
Subject: {subject}
Chapter: {chapter}  
Topic: {topic}
Grade Level: {level}

Return ONLY the narration text, no title, no extra formatting:"""

        response = model.generate_content(prompt)
        narration = response.text.strip()
        
        # Clean up text
        narration = narration.replace("**", "").replace("*", "")
        narration = narration.strip('"').strip("'")
        narration = narration.replace("```", "").replace("`", "")
        
        # Ensure length is within bounds
        if len(narration) > max_chars:
            # Intelligently truncate at sentence boundary
            sentences = narration.split('. ')
            truncated = []
            current_length = 0
            
            for sentence in sentences:
                if current_length + len(sentence) + 2 <= max_chars:
                    truncated.append(sentence)
                    current_length += len(sentence) + 2
                else:
                    break
            
            narration = '. '.join(truncated)
            if not narration.endswith('.'):
                narration += '.'
        
        logger.info(
            f"Generated narration for Level {level} ({len(narration)} chars): "
            f"{narration[:100]}..."
        )
        
        return narration
        
    except Exception as e:
        logger.error(f"Failed to generate narration text: {e}")
        raise


def generate_narration_audio(
    text: str, 
    job_id: str,
    voice_style: str = DEFAULT_VOICE_STYLE
) -> Dict:
    """
    Generate audio from text using TTS engine.
    
    Args:
        text: Narration text to convert to speech
        job_id: Job ID for filename
        voice_style: Voice style to use (default from config)
    
    Returns:
        Dict with audio_path, duration, file_size, voice_style
    """
    global _tts_engine, _voice_styles
    
    # Ensure TTS is initialized
    ensure_tts_initialized()
    
    if _tts_engine is None:
        raise RuntimeError("TTS engine failed to initialize")
    
    if voice_style not in _voice_styles:
        logger.warning(f"Voice style '{voice_style}' not found, using default: {DEFAULT_VOICE_STYLE}")
        voice_style = DEFAULT_VOICE_STYLE
        
        if voice_style not in _voice_styles:
            raise ValueError(f"Default voice style '{DEFAULT_VOICE_STYLE}' not available")
    
    try:
        logger.info(f"[{job_id}] Generating audio for narration ({len(text)} chars)")
        
        # Get voice style
        style = _voice_styles[voice_style]
        
        # Generate speech
        wav, duration = _tts_engine(
            text,
            style,
            TTS_TOTAL_STEPS,
            TTS_SPEED,
            TTS_SILENCE_DURATION
        )
        
        # Trim to actual duration
        sample_rate = _tts_engine.sample_rate
        trimmed_wav = wav[0, : int(sample_rate * duration[0].item())]
        
        # Save to file
        audio_filename = f"{job_id}_narration.wav"
        audio_path = NARRATIONS_DIR / audio_filename
        
        # Ensure directory exists
        NARRATIONS_DIR.mkdir(parents=True, exist_ok=True)
        
        sf.write(str(audio_path), trimmed_wav, sample_rate)
        
        file_size = audio_path.stat().st_size
        duration_seconds = float(duration[0])
        
        logger.info(
            f"[{job_id}] ✓ Audio generated: {audio_filename} "
            f"({duration_seconds:.1f}s, {file_size/1024:.1f}KB)"
        )
        
        return {
            "audio_path": audio_path,
            "audio_filename": audio_filename,
            "duration": duration_seconds,
            "file_size": file_size,
            "voice_style": voice_style,
            "sample_rate": sample_rate
        }
        
    except Exception as e:
        logger.error(f"[{job_id}] Failed to generate audio: {e}")
        raise


def get_audio_info(audio_path: Path) -> Dict:
    """Get information about an audio file"""
    try:
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        data, sample_rate = sf.read(str(audio_path))
        duration = len(data) / sample_rate
        file_size = audio_path.stat().st_size
        
        return {
            "audio_path": audio_path,
            "audio_filename": audio_path.name,
            "duration": duration,
            "file_size": file_size,
            "sample_rate": sample_rate
        }
    except Exception as e:
        logger.error(f"Failed to get audio info: {e}")
        raise


def cleanup_audio_file(audio_path: Path):
    """Remove audio file (for cleanup)"""
    try:
        if audio_path.exists():
            audio_path.unlink()
            logger.info(f"Deleted audio file: {audio_path.name}")
    except Exception as e:
        logger.error(f"Failed to delete audio file {audio_path}: {e}")