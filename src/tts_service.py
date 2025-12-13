import logging
import soundfile as sf
from pathlib import Path
from typing import Optional, Dict
import google.generativeai as genai

from src.tts.helper import load_text_to_speech, load_voice_style, Style, TextToSpeech
from src.config import (
    ONNX_DIR, VOICE_STYLES_DIR, NARRATIONS_DIR, 
    DEFAULT_VOICE_STYLE, TTS_SPEED, TTS_TOTAL_STEPS, 
    TTS_SILENCE_DURATION, USE_GPU_TTS, GEMINI_API_KEY
)

logger = logging.getLogger("manim_ai_server")

# Global TTS engine (loaded once)
_tts_engine: Optional[TextToSpeech] = None
_voice_styles: Dict[str, Style] = {}


def initialize_tts_engine():
    """Initialize TTS engine and load voice styles (called at startup)"""
    global _tts_engine, _voice_styles
    
    if _tts_engine is not None:
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
            
    except Exception as e:
        logger.error(f"Failed to initialize TTS engine: {e}")
        raise


def get_available_voices() -> list[str]:
    """Get list of available voice styles"""
    return list(_voice_styles.keys())


def generate_narration_text(topic: str) -> str:
    """
    Generate narration text for the topic using Gemini.
    Returns 2-3 sentence narration suitable for TTS.
    """
    try:
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured")
        
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.0-flash-exp")
        
        prompt = f"""Generate a clear, concise narration script for an educational animation about: {topic}

Requirements:
- 2-3 sentences maximum
- Simple, easy-to-understand language
- Explain the core concept
- Suitable for text-to-speech (no special characters, equations, or symbols)
- Total length: 100-200 characters
- Educational and engaging tone

Example for "Pythagorean Theorem":
"The Pythagorean theorem is a fundamental principle in geometry. It states that in a right triangle, the square of the hypotenuse equals the sum of squares of the other two sides. This relationship is expressed as a squared plus b squared equals c squared."

Generate ONLY the narration text, no title, no extra explanation:"""

        response = model.generate_content(prompt)
        narration = response.text.strip()
        
        # Remove any markdown or quotes
        narration = narration.replace("**", "").replace("*", "")
        narration = narration.strip('"').strip("'")
        
        # Limit length
        if len(narration) > 500:
            narration = narration[:497] + "..."
        
        logger.info(f"Generated narration ({len(narration)} chars): {narration[:100]}...")
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
    
    if _tts_engine is None:
        raise RuntimeError("TTS engine not initialized. Call initialize_tts_engine() first.")
    
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
        
        # Read audio file to get duration
        import soundfile as sf
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